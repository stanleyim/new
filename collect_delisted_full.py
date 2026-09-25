"""
collect_delisted_full.py

목적: 현재 439종목 모델 유니버스에 없는 2,916개 종목(실제 상장폐지/이탈 523개
     + 유니버스 미포함 현재 상장 2,393개, repo pit-data 브랜치 sector_YYYY.parquet
     기준 실측)의 전체 기간 일별 OHLCV를 KIS OpenAPI로 수집한다.

검증된 근거 (repo 실제 파일 확인, 추측 아님):
  - 인증/호출 로직: signal_runner.py의 KisClient (토큰 3회 재시도, RPS=10 스로틀,
    HTTP/커넥션 오류 5회 재시도)을 그대로 재사용. 새로 만들지 않음.
  - API 필드명: test_kis_delisted.py / diag_000420.py 실측 확인 결과 날짜 필드는
    `stck_bsop_date` (첫 설계안의 `stck_bsop_dt`는 오기 — 수정됨).
  - 이어받기(진행상황 저장) 방식: dart_collect.py와 동일한 컨벤션 —
    브랜치에서 기존 데이터를 workflow가 미리 복사해주고, 본 스크립트는
    TIME_BUDGET_MINUTES 안에서만 진행 후 progress.json에 상태 저장,
    다음 실행이 이어받음.
  - 종목 리스트: pit-data 브랜치 pit_data_extra/sector_*.parquet 13개 파일에서
    실측 추출 (delisted_candidates_2916.csv, 별도 첨부). 총 고유 3,355종목 중
    현재 439 유니버스(data/universe.parquet)에 없는 2,916종목.

이 스크립트는 설계/검증 단계 산출물이다. 대규모 실행은 승인 후에만.
"""
from __future__ import annotations

import csv
import json
import os
import time
from dataclasses import dataclass, field as dc_field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

# ----------------------------------------------------------------------
# repo 실제 상수 (signal_runner.py에서 그대로 가져옴)
# ----------------------------------------------------------------------

KIS_BASE = "https://openapi.koreainvestment.com:9443"
KIS_RPS = 10  # signal_runner.py와 동일

# ----------------------------------------------------------------------
# 수집 설정
# ----------------------------------------------------------------------

CHUNK_DAYS = 100  # 1회 요청당 달력일 창 (KIS 1회 반환 상한 미확인 — 보수값, 실측 필요)
TICKERS_CSV = "delisted_candidates_2916.csv"  # 실제 추출된 종목 리스트
GLOBAL_START = date(2014, 1, 1)
GLOBAL_END = date.today()

EXISTING_DIR = "existing_delisted_data/delisted_data"  # workflow가 브랜치에서 복사
OUT_DIR = Path("delisted_data")
RAW_ANOMALY_DIR = OUT_DIR / "_raw_anomalies"
PROGRESS_PATH = OUT_DIR / "progress.json"
ANOMALY_LOG_PATH = OUT_DIR / "anomaly_log.jsonl"
FLAGGED_PATH = OUT_DIR / "flagged.json"

TIME_BUDGET_MINUTES = float(os.environ.get("TIME_BUDGET_MINUTES", "110"))
TEST_TICKER_LIMIT = os.environ.get("TEST_TICKER_LIMIT")  # 예: "5" -> 앞 5종목만 처리 (소규모 테스트용)


# ----------------------------------------------------------------------
# KIS 인증/호출 — signal_runner.py KisClient 그대로 재사용 (신규 로직 없음)
# ----------------------------------------------------------------------

def get_kis_token():
    app_key = os.environ.get("KIS_APP_KEY")
    app_secret = os.environ.get("KIS_APP_SECRET")
    if not app_key or not app_secret:
        raise RuntimeError("KIS_APP_KEY/SECRET 환경변수 없음")

    wait_times = [30, 60, 120]
    last_exc = None
    for attempt in range(3):
        try:
            r = requests.post(
                f"{KIS_BASE}/oauth2/tokenP",
                json={"grant_type": "client_credentials", "appkey": app_key, "appsecret": app_secret},
                timeout=30,
            )
            if r.status_code == 200:
                data = r.json()
                if "access_token" in data:
                    if attempt > 0:
                        print(f"[INFO] KIS 토큰 재시도 성공 (attempt {attempt+1})")
                    return data["access_token"], app_key, app_secret
                last_exc = RuntimeError(f"KIS 토큰 응답 이상: {data}")
            else:
                last_exc = RuntimeError(f"KIS 토큰 발급 실패: {r.status_code} {r.text[:200]}")
        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
                requests.exceptions.ChunkedEncodingError) as e:
            last_exc = e

        print(f"[WARN] KIS 토큰 발급 실패 (attempt {attempt+1}/3): {last_exc}")
        if attempt < 2:
            time.sleep(wait_times[attempt])

    raise RuntimeError(f"KIS 토큰 발급 최종 실패 (3회 재시도): {last_exc}")


class KisClient:
    """signal_runner.py의 KisClient와 동일 (토큰 발급/RPS 스로틀/HTTP 재시도)."""

    def __init__(self):
        self.token, self.app_key, self.app_secret = get_kis_token()
        self.last_call = 0.0
        self.min_interval = 1.0 / KIS_RPS
        self.session = requests.Session()

    def _throttle(self):
        elapsed = time.time() - self.last_call
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self.last_call = time.time()

    def _get(self, path, tr_id, params, retries=5):
        last_exc = None
        for attempt in range(retries):
            self._throttle()
            headers = {
                "content-type": "application/json",
                "authorization": f"Bearer {self.token}",
                "appkey": self.app_key,
                "appsecret": self.app_secret,
                "tr_id": tr_id,
            }
            try:
                r = self.session.get(f"{KIS_BASE}{path}", headers=headers, params=params, timeout=30)
                if r.status_code == 200:
                    data = r.json()
                    if data.get("rt_cd") == "0":
                        data["_http_status"] = 200
                        return data
                    if data.get("msg_cd") == "EGW00201":  # rate limit
                        time.sleep(2.0)
                        continue
                    data["_http_status"] = 200
                    return data
                else:
                    time.sleep(1.0 + attempt)
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout,
                    requests.exceptions.ChunkedEncodingError) as e:
                last_exc = e
                self.session = requests.Session()
                time.sleep(2.0 + attempt * 2)
                continue
        if last_exc:
            print(f"[WARN] {path} {params.get('FID_INPUT_ISCD','?')}: {last_exc}")
        return None  # 완전 실패 (HTTP/연결 레벨) — validate_response에서 별도 처리

    def get_ohlcv_raw(self, ticker: str, start_date: str, end_date: str) -> dict:
        """일별 OHLCV raw 응답 (output2 원본 그대로, 파싱하지 않음)."""
        return self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            "FHKST03010100",
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": ticker,
                "FID_INPUT_DATE_1": start_date,
                "FID_INPUT_DATE_2": end_date,
                "FID_PERIOD_DIV_CODE": "D",
                "FID_ORG_ADJ_PRC": "0",
            },
        )


# ----------------------------------------------------------------------
# 검증
# ----------------------------------------------------------------------

@dataclass
class ValidationResult:
    is_valid: bool
    reasons: list = dc_field(default_factory=list)
    df: Optional[pd.DataFrame] = None


def validate_response(raw: Optional[dict], ticker: str, start: date, end: date) -> ValidationResult:
    """
    명백한 구조적 이상만 판정. '80거래일 요청 -> 80건' 같은 고정 기대치는 쓰지 않음
    (상장폐지 직전 거래정지 등으로 정상적으로 적을 수 있음).

    검사 항목:
      1. HTTP/연결 완전 실패 (raw is None) / rt_cd 비정상
      2. output2 키 존재 여부
      3. 날짜 중복 (동일 날짜 다중 행 포함)
      4. 날짜 정렬 (단조 증가 또는 단조 감소 중 하나 — KIS는 보통 내림차순)
      5. 요청 범위 밖 날짜
      6. 행 수가 요청 구간의 달력일 수를 초과 — 논리적으로 불가능한 상태이므로
         하드 이상치 (거래일수 추정 아님; 742건/74건 케이스를 이 기준으로 재현 가능)
      7. OHLC 논리 위반 (high>=low, high>=open/close, low<=open/close, 모든 값 >=0)
    """
    reasons: list[str] = []

    if raw is None:
        return ValidationResult(is_valid=False, reasons=["http_or_connection_failure"])

    if raw.get("rt_cd") != "0":
        return ValidationResult(is_valid=False, reasons=[f"rt_cd={raw.get('rt_cd')} msg={raw.get('msg1')}"])

    output2 = raw.get("output2")
    if output2 is None:
        return ValidationResult(is_valid=False, reasons=["output2_missing"])

    if len(output2) == 0:
        # 상장폐지 이후 구간 등 정상적으로 비어있을 수 있음 — 이상 아님
        return ValidationResult(
            is_valid=True, reasons=[],
            df=pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"]),
        )

    df = pd.DataFrame(output2)

    required_cols = {"stck_bsop_date", "stck_oprc", "stck_hgpr", "stck_lwpr", "stck_clpr", "acml_vol"}
    missing_cols = required_cols - set(df.columns)
    if missing_cols:
        return ValidationResult(is_valid=False, reasons=[f"missing_columns:{missing_cols}"])

    try:
        dates = pd.to_datetime(df["stck_bsop_date"].astype(str), format="%Y%m%d")
    except Exception as e:
        return ValidationResult(is_valid=False, reasons=[f"date_parse_error:{e}"])

    n_total = len(dates)
    n_unique = dates.nunique()
    if n_unique != n_total:
        reasons.append(f"duplicate_dates:{n_total - n_unique}건")

    if not (dates.is_monotonic_increasing or dates.is_monotonic_decreasing):
        reasons.append("dates_not_monotonic")

    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    out_of_range = int(((dates < start_ts) | (dates > end_ts)).sum())
    if out_of_range > 0:
        reasons.append(f"out_of_range_dates:{out_of_range}건")

    calendar_days = (end - start).days + 1
    if n_total > calendar_days:
        reasons.append(f"row_count_exceeds_calendar_days:{n_total}건 > {calendar_days}일")

    try:
        o = pd.to_numeric(df["stck_oprc"], errors="coerce")
        h = pd.to_numeric(df["stck_hgpr"], errors="coerce")
        l = pd.to_numeric(df["stck_lwpr"], errors="coerce")
        c = pd.to_numeric(df["stck_clpr"], errors="coerce")
        v = pd.to_numeric(df["acml_vol"], errors="coerce")
    except Exception as e:
        return ValidationResult(is_valid=False, reasons=[f"ohlcv_parse_error:{e}"])

    if o.isna().any() or h.isna().any() or l.isna().any() or c.isna().any() or v.isna().any():
        reasons.append("ohlcv_non_numeric_values")
    else:
        violations = int(
            (h < l).sum() + (h < o).sum() + (h < c).sum()
            + (l > o).sum() + (l > c).sum()
            + (o < 0).sum() + (h < 0).sum() + (l < 0).sum() + (c < 0).sum()
            + (v < 0).sum()
        )
        if violations > 0:
            reasons.append(f"ohlc_logic_violations:{violations}건")

    if reasons:
        return ValidationResult(is_valid=False, reasons=reasons)

    out_df = pd.DataFrame({
        "date": dates.dt.strftime("%Y-%m-%d"), "open": o, "high": h, "low": l, "close": c, "volume": v,
    }).sort_values("date").reset_index(drop=True)
    return ValidationResult(is_valid=True, reasons=[], df=out_df)


# ----------------------------------------------------------------------
# 원본 보존 / 로그 / FLAG
# ----------------------------------------------------------------------

def save_raw_anomaly(raw: Optional[dict], ticker: str, start: date, end: date, attempt: int) -> Path:
    RAW_ANOMALY_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = RAW_ANOMALY_DIR / f"{ticker}_{start:%Y%m%d}_{end:%Y%m%d}_attempt{attempt}_{ts}.json"
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def log_anomaly_event(event: dict) -> None:
    ANOMALY_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with ANOMALY_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def flag_permanently(ticker: str, start: date, end: date, reasons_first: list, reasons_retry: list) -> None:
    FLAGGED_PATH.parent.mkdir(parents=True, exist_ok=True)
    flagged = json.loads(FLAGGED_PATH.read_text(encoding="utf-8")) if FLAGGED_PATH.exists() else []
    flagged.append({
        "ticker": ticker, "start": start.isoformat(), "end": end.isoformat(),
        "reasons_first_attempt": reasons_first, "reasons_retry_attempt": reasons_retry,
        "flagged_at": datetime.now().isoformat(),
    })
    FLAGGED_PATH.write_text(json.dumps(flagged, ensure_ascii=False, indent=2), encoding="utf-8")


# ----------------------------------------------------------------------
# 청크 단위 수집 (검증 -> 재조회 -> 격리)
# ----------------------------------------------------------------------

def collect_one_chunk(client: "KisClient", ticker: str, start: date, end: date) -> str:
    """반환: "saved" | "saved_empty" | "flagged" """
    raw = client.get_ohlcv_raw(ticker, start.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
    result = validate_response(raw, ticker, start, end)

    if result.is_valid:
        _append_to_parquet(ticker, result.df)
        return "saved_empty" if result.df.empty else "saved"

    raw_path_1 = save_raw_anomaly(raw, ticker, start, end, attempt=1)
    log_anomaly_event({
        "ticker": ticker, "start": start.isoformat(), "end": end.isoformat(),
        "attempt": 1, "reasons": result.reasons, "raw_saved_to": str(raw_path_1),
    })
    print(f"[ANOMALY] {ticker} {start}~{end} attempt1 reasons={result.reasons}")

    time.sleep(1.0 / KIS_RPS)
    raw_retry = client.get_ohlcv_raw(ticker, start.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
    result_retry = validate_response(raw_retry, ticker, start, end)

    if result_retry.is_valid:
        _append_to_parquet(ticker, result_retry.df)
        log_anomaly_event({
            "ticker": ticker, "start": start.isoformat(), "end": end.isoformat(),
            "attempt": 2, "outcome": "resolved_on_retry",
        })
        return "saved_empty" if result_retry.df.empty else "saved"

    raw_path_2 = save_raw_anomaly(raw_retry, ticker, start, end, attempt=2)
    log_anomaly_event({
        "ticker": ticker, "start": start.isoformat(), "end": end.isoformat(),
        "attempt": 2, "reasons": result_retry.reasons, "raw_saved_to": str(raw_path_2),
        "outcome": "flagged",
    })
    flag_permanently(ticker, start, end, result.reasons, result_retry.reasons)
    print(f"[FLAGGED] {ticker} {start}~{end} — 재조회도 이상, 수집은 계속 진행")
    return "flagged"


def _append_to_parquet(ticker: str, df: pd.DataFrame) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"{ticker}.parquet"
    if df.empty:
        return
    if path.exists():
        existing = pd.read_parquet(path)
        combined = pd.concat([existing, df], ignore_index=True).drop_duplicates(subset="date").sort_values("date")
    else:
        combined = df
    combined.to_parquet(path, index=False)


# ----------------------------------------------------------------------
# 청크 분할 / 진행상황 (dart_collect.py와 동일 컨벤션)
# ----------------------------------------------------------------------

def date_chunks(start: date, end: date, days: int = CHUNK_DAYS) -> list:
    chunks = []
    cur = start
    while cur <= end:
        nxt = min(cur + timedelta(days=days - 1), end)
        chunks.append((cur, nxt))
        cur = nxt + timedelta(days=1)
    return chunks


def build_plan(tickers: list) -> list:
    """전체 작업 목록: (ticker, start, end) — 실행마다 항상 동일한 순서로 생성됨"""
    plan = []
    for ticker in tickers:
        for c_start, c_end in date_chunks(GLOBAL_START, GLOBAL_END):
            plan.append((ticker, c_start, c_end))
    return plan


def load_progress() -> dict:
    path = os.path.join(EXISTING_DIR, "progress.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {"done_index": -1, "completed": False}


def load_tickers(csv_path: str) -> list:
    with open(csv_path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return [row["ticker"] for row in reader]


# ----------------------------------------------------------------------
# 메인
# ----------------------------------------------------------------------

def main() -> None:
    tickers = load_tickers(TICKERS_CSV)
    if TEST_TICKER_LIMIT:
        n = int(TEST_TICKER_LIMIT)
        tickers = tickers[:n]
        print(f"[TEST MODE] TEST_TICKER_LIMIT={n} — 앞 {n}종목만 처리: {tickers}")
    plan = build_plan(tickers)
    progress = load_progress()
    done_index = progress["done_index"]

    if progress.get("completed"):
        print("이미 전체 완료 — 스킵")
        return

    print(f"전체 작업 {len(plan)}건 (종목 {len(tickers)} x 청크), 이어받기 시작 지점: {done_index + 1}")

    client = KisClient()
    t0 = time.monotonic()
    budget_sec = TIME_BUDGET_MINUTES * 60
    n_saved = n_flagged = 0

    idx = done_index + 1
    while idx < len(plan):
        if time.monotonic() - t0 > budget_sec:
            print(f"[시간예산 소진] idx={idx}까지 진행, 다음 실행이 이어받음")
            break

        ticker, c_start, c_end = plan[idx]
        outcome = collect_one_chunk(client, ticker, c_start, c_end)
        if outcome == "flagged":
            n_flagged += 1
        else:
            n_saved += 1

        progress["done_index"] = idx
        idx += 1

        if idx % 50 == 0:
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            PROGRESS_PATH.write_text(json.dumps(progress, ensure_ascii=False), encoding="utf-8")

    progress["completed"] = idx >= len(plan)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    PROGRESS_PATH.write_text(json.dumps(progress, ensure_ascii=False), encoding="utf-8")
    print(f"이번 실행 종료: saved={n_saved} flagged={n_flagged} done_index={progress['done_index']} completed={progress['completed']}")


if __name__ == "__main__":
    main()
