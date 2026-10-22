"""분봉 1종목 실측 테스트 (New5, 2026-10-22 Stanley 승인).

목적: KIS 과거 분봉 API(FHKST03010230)가 쓸 수 있는 자료인지 숫자로 확인.
  1) 정확성(핵심): 최근 5거래일 분봉을 일 단위로 합친 시·고·저·종이 KRX 공식 일봉(pykrx)과 일치하는가
  2) 보관 기간: 실제로 가장 오래된 조회 가능 날짜
  3) 호출당 건수·하루 호출 수
  4) 초당 한도: 0.05초 간격 200회, EGW00201 횟수
  5) 용량: 1년치 1분봉·5분봉 파일 크기
레포 파일은 쓰지 않는다. 결과 = Actions artifact(minute_probe_out) + 텔레그램 요약.
KIS 인증·텔레그램은 collect_delisted_full.py / signal_runner.py와 같은 방식.
"""
import os
import time
import json
import datetime as dt
from pathlib import Path

import requests
import pandas as pd

KIS_BASE = "https://openapi.koreainvestment.com:9443"
KIS_RPS = 10  # 수집 구간은 기존 운용과 같은 초당 10회
PATH_MIN = "/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice"
TR_MIN = "FHKST03010230"
TICKER = (os.environ.get("PROBE_TICKER") or "005930").strip()
KST = dt.timezone(dt.timedelta(hours=9))
OUT = Path("minute_probe_out")
LOOKBACK_DAYS = 420        # 달력일. 1년 보관 한도보다 넉넉히
STOP_EMPTY_STREAK = 15     # 연속 15평일 무자료면 보관 한도 밖으로 보고 중단
MAX_CALLS_PER_DAY = 12
RATE_TEST_N = 200
RATE_TEST_INTERVAL = 0.05  # 초당 20회


def send_telegram(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("[WARN] Telegram 환경변수 없음, 알림 스킵")
        return
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      data={"chat_id": chat_id, "text": text}, timeout=30)
    if r.status_code != 200:
        print(f"[WARN] Telegram 실패: {r.status_code} {r.text}")


def get_kis_token():
    app_key = os.environ.get("KIS_APP_KEY")
    app_secret = os.environ.get("KIS_APP_SECRET")
    if not app_key or not app_secret:
        raise RuntimeError("KIS_APP_KEY/SECRET 환경변수 없음")
    last = None
    for attempt, wait in enumerate([30, 60, 120]):
        try:
            r = requests.post(f"{KIS_BASE}/oauth2/tokenP",
                              json={"grant_type": "client_credentials", "appkey": app_key, "appsecret": app_secret},
                              timeout=30)
            if r.status_code == 200 and "access_token" in r.json():
                return r.json()["access_token"], app_key, app_secret
            last = RuntimeError(f"토큰 실패: {r.status_code} {r.text[:200]}")
        except requests.exceptions.RequestException as e:
            last = e
        print(f"[WARN] KIS 토큰 실패 (attempt {attempt + 1}/3): {last}")
        if attempt < 2:
            time.sleep(wait)
    raise RuntimeError(f"KIS 토큰 최종 실패: {last}")


class Kis:
    def __init__(self):
        self.token, self.app_key, self.app_secret = get_kis_token()
        self.session = requests.Session()
        self.last_call = 0.0
        self.n_calls = 0

    def raw(self, params, interval):
        """1회 호출, 재시도 없음. (http_status, json 또는 None)."""
        wait = interval - (time.time() - self.last_call)
        if wait > 0:
            time.sleep(wait)
        self.last_call = time.time()
        self.n_calls += 1
        headers = {"content-type": "application/json", "authorization": f"Bearer {self.token}",
                   "appkey": self.app_key, "appsecret": self.app_secret, "tr_id": TR_MIN}
        try:
            r = self.session.get(f"{KIS_BASE}{PATH_MIN}", headers=headers, params=params, timeout=30)
        except requests.exceptions.RequestException as e:
            self.session = requests.Session()
            return -1, {"msg_cd": "CONN", "msg1": str(e)[:150]}
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, None

    def get(self, params):
        """수집용: 한도 초과·연결 오류만 재시도. 성공 json 또는 실패 json."""
        last = None
        for attempt in range(5):
            st, d = self.raw(params, 1.0 / KIS_RPS)
            last = d
            if st == 200 and d and d.get("rt_cd") == "0":
                return d
            if d and d.get("msg_cd") in ("EGW00201", "CONN"):
                time.sleep(1.0 + attempt)
                continue
            if st != 200:
                time.sleep(1.0 + attempt)
                continue
            return d  # 업무 오류(rt_cd != 0)는 그대로 반환
        return last


def min_params(date_s, hour_s):
    return {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": TICKER, "FID_INPUT_HOUR_1": hour_s,
            "FID_INPUT_DATE_1": date_s, "FID_PW_DATA_INCU_YN": "N", "FID_FAKE_TICK_INCU_YN": "N"}


def fetch_day(kis, date_s):
    """하루치 1분봉. 180000부터 거꾸로 페이지. 반환 (bars dict time->row, 호출 기록 list)."""
    bars, log, hour = {}, [], "180000"
    for _ in range(MAX_CALLS_PER_DAY):
        d = kis.get(min_params(date_s, hour)) or {}
        out2 = d.get("output2") or []
        same = [r for r in out2 if r.get("stck_bsop_date") == date_s and r.get("stck_cntg_hour")]
        other = len(out2) - len(same)
        new = 0
        for r in same:
            if r["stck_cntg_hour"] not in bars:
                bars[r["stck_cntg_hour"]] = r
                new += 1
        log.append({"date": date_s, "hour_in": hour, "rt_cd": d.get("rt_cd"), "msg_cd": d.get("msg_cd"),
                    "n_out2": len(out2), "n_same_date": len(same), "n_other_date": other, "n_new": new})
        if not same or new == 0 or other > 0:
            break
        earliest = min(r["stck_cntg_hour"] for r in same)
        if earliest <= "090000":
            break
        hour = earliest  # 1봉 겹치게 요청, 시간 키로 중복 제거
    return bars, log


def to_num(s):
    return pd.to_numeric(s, errors="coerce")  # 빈 문자열 → NaN


def bars_to_df(date_s, bars):
    if not bars:
        return pd.DataFrame()
    df = pd.DataFrame(bars.values())
    out = pd.DataFrame({
        "date": date_s,
        "time": df["stck_cntg_hour"].astype(str),
        "open": to_num(df.get("stck_oprc", pd.Series(dtype=str)).astype(str)),
        "high": to_num(df.get("stck_hgpr", pd.Series(dtype=str)).astype(str)),
        "low": to_num(df.get("stck_lwpr", pd.Series(dtype=str)).astype(str)),
        "close": to_num(df.get("stck_prpr", pd.Series(dtype=str)).astype(str)),
        "volume": to_num(df.get("cntg_vol", pd.Series(dtype=str)).astype(str)),
        "acml_tr_pbmn": to_num(df.get("acml_tr_pbmn", pd.Series(dtype=str)).astype(str)),
    })
    return out.sort_values("time").reset_index(drop=True)


def day_agg(day_df):
    reg = day_df[day_df["time"] <= "153000"]
    if reg.empty:
        return None
    return {"open": reg["open"].iloc[0], "high": reg["high"].max(), "low": reg["low"].min(),
            "close": reg["close"].iloc[-1], "volume": reg["volume"].sum(),
            "n_bars": len(reg), "n_after_1530": int((day_df["time"] > "153000").sum()),
            "first": reg["time"].iloc[0], "last": reg["time"].iloc[-1]}


def to_5min(df):
    if df.empty:
        return df
    hh = df["time"].str[:2].astype(int)
    mm = df["time"].str[2:4].astype(int)
    m = hh * 60 + mm
    b = (m // 5) * 5
    x = df.assign(bucket=(b // 60).astype(str).str.zfill(2) + (b % 60).astype(str).str.zfill(2) + "00")
    g = x.groupby(["date", "bucket"], sort=True)
    return pd.DataFrame({
        "open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
        "close": g["close"].last(), "volume": g["volume"].sum(),
    }).reset_index().rename(columns={"bucket": "time"})


def krx_day(date_s):
    """KRX 공식 일봉(pykrx, signal_runner.krx_ohlcv_day와 같은 호출). 없으면 None, 오류면 예외."""
    from pykrx import stock
    last = None
    for k in range(3):
        try:
            x = stock.get_market_ohlcv(date_s, market="ALL")
            break
        except Exception as e:
            last = e
            time.sleep(2 + 2 * k)
    else:
        raise RuntimeError(f"KRX 조회 실패 {date_s}: {type(last).__name__}: {str(last)[:150]}")
    if x is None or len(x) == 0:
        return None
    x = x.reset_index()
    x = x.rename(columns={x.columns[0]: "ticker"})
    x["ticker"] = x["ticker"].astype(str)
    row = x[x["ticker"] == TICKER]
    if row.empty or not (float(row["종가"].iloc[0]) > 0):
        return None
    r = row.iloc[0]
    return {c: float(r[c]) for c in ["시가", "고가", "저가", "종가", "거래량"]}


def main():
    OUT.mkdir(exist_ok=True)
    t0 = time.time()
    today = dt.datetime.now(KST).date()
    kis = Kis()
    summary = {"ticker": TICKER, "run_kst": dt.datetime.now(KST).isoformat(timespec="seconds")}

    # ---- 1년치 수집 (어제부터 거꾸로) ----
    frames, calls, have_dates, empty_streak = [], [], [], 0
    d = today - dt.timedelta(days=1)
    while d >= today - dt.timedelta(days=LOOKBACK_DAYS):
        if d.weekday() < 5:
            ds = d.strftime("%Y%m%d")
            bars, log = fetch_day(kis, ds)
            calls.extend(log)
            df = bars_to_df(ds, bars)
            if df.empty:
                empty_streak += 1
                if empty_streak >= STOP_EMPTY_STREAK and have_dates:
                    break
            else:
                empty_streak = 0
                frames.append(df)
                have_dates.append(ds)
        d -= dt.timedelta(days=1)
    m1 = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    calls_df = pd.DataFrame(calls)
    calls_df.to_csv(OUT / "calls_log.csv", index=False)
    summary["n_days_with_data"] = len(have_dates)
    summary["oldest_date"] = min(have_dates) if have_dates else None
    summary["newest_date"] = max(have_dates) if have_dates else None
    if not calls_df.empty:
        ok = calls_df[calls_df["n_same_date"] > 0]
        summary["rows_per_call_max"] = int(ok["n_out2"].max()) if len(ok) else 0
        per_day = calls_df[calls_df["date"].isin(have_dates)].groupby("date").size()
        summary["calls_per_day_median"] = float(per_day.median()) if len(per_day) else None
        summary["calls_per_day_max"] = int(per_day.max()) if len(per_day) else None
        errs = calls_df[(calls_df["rt_cd"] != "0") & calls_df["rt_cd"].notna()]
        summary["collect_business_errors"] = errs["msg_cd"].value_counts().to_dict()
    summary["collect_calls_total"] = kis.n_calls
    summary["collect_seconds"] = round(time.time() - t0, 1)

    # ---- 1) 정확성: 최근 5거래일 vs KRX ----
    cmp_rows, krx_err = [], None
    for ds in sorted(have_dates, reverse=True)[:5]:
        agg = day_agg(m1[m1["date"] == ds])
        try:
            k = krx_day(ds)
        except Exception as e:
            krx_err = str(e)[:200]
            break
        row = {"date": ds, "krx_found": k is not None}
        if agg:
            row.update({f"min_{c}": agg[c] for c in agg})
        if k:
            row.update({"krx_open": k["시가"], "krx_high": k["고가"], "krx_low": k["저가"],
                        "krx_close": k["종가"], "krx_volume": k["거래량"]})
        if agg and k:
            row["ohlc_match"] = bool(agg["open"] == k["시가"] and agg["high"] == k["고가"]
                                     and agg["low"] == k["저가"] and agg["close"] == k["종가"])
            row["volume_ratio"] = round(agg["volume"] / k["거래량"], 4) if k["거래량"] else None
        cmp_rows.append(row)
    cmp_df = pd.DataFrame(cmp_rows)
    cmp_df.to_csv(OUT / "krx_compare.csv", index=False)
    if krx_err:
        verdict = f"판정 불가(KRX 조회 오류: {krx_err})"
    elif len(cmp_df) < 5 or "ohlc_match" not in cmp_df or cmp_df["ohlc_match"].isna().any():
        verdict = f"판정 불가(비교 가능 {int(cmp_df['ohlc_match'].notna().sum()) if 'ohlc_match' in cmp_df else 0}/5일)"
    elif bool(cmp_df["ohlc_match"].all()):
        verdict = "통과(5/5일 시·고·저·종 일치)"
    else:
        verdict = f"불일치 → 중단({int((~cmp_df['ohlc_match'].astype(bool)).sum())}/5일 불일치)"
    summary["accuracy_verdict"] = verdict

    # ---- 5) 용량 ----
    if not m1.empty:
        m1.to_parquet(OUT / f"{TICKER}_1m.parquet", index=False)
        m5 = to_5min(m1)
        m5.to_parquet(OUT / f"{TICKER}_5m.parquet", index=False)
        summary["rows_1m"] = len(m1)
        summary["rows_5m"] = len(m5)
        summary["bytes_1m"] = (OUT / f"{TICKER}_1m.parquet").stat().st_size
        summary["bytes_5m"] = (OUT / f"{TICKER}_5m.parquet").stat().st_size
        summary["bars_per_day_median"] = float(m1.groupby("date").size().median())

    # ---- 4) 초당 한도 (마지막, 재시도 없음) ----
    time.sleep(2)
    probe_date = max(have_dates) if have_dates else (today - dt.timedelta(days=1)).strftime("%Y%m%d")
    codes, t1 = {}, time.time()
    for _ in range(RATE_TEST_N):
        st, dd = kis.raw(min_params(probe_date, "153000"), RATE_TEST_INTERVAL)
        key = "OK" if (st == 200 and dd and dd.get("rt_cd") == "0") else f"{st}:{(dd or {}).get('msg_cd')}"
        codes[key] = codes.get(key, 0) + 1
    summary["rate_test"] = {"n": RATE_TEST_N, "interval_s": RATE_TEST_INTERVAL,
                            "seconds": round(time.time() - t1, 1), "results": codes}
    summary["total_calls"] = kis.n_calls
    summary["total_seconds"] = round(time.time() - t0, 1)
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))

    # ---- 텔레그램 ----
    lines = [f"🧪 분봉 실측 테스트 {TICKER}", f"1) 정확성: {verdict}"]
    if not cmp_df.empty and "volume_ratio" in cmp_df:
        lines.append(f"   거래량 비율(분봉합/KRX): {', '.join(str(v) for v in cmp_df['volume_ratio'])}")
    lines.append(f"2) 보관: {summary.get('oldest_date')} ~ {summary.get('newest_date')} ({summary.get('n_days_with_data')}일)")
    lines.append(f"3) 호출당 최대 {summary.get('rows_per_call_max')}건 · 하루 호출 중앙 {summary.get('calls_per_day_median')}회"
                 f" · 하루 봉 중앙 {summary.get('bars_per_day_median')}개")
    lines.append(f"4) 0.05초 간격 {RATE_TEST_N}회: {codes}")
    if "bytes_1m" in summary:
        lines.append(f"5) 1분봉 {summary['bytes_1m'] / 1e6:.2f}MB · 5분봉 {summary['bytes_5m'] / 1e6:.2f}MB (1종목)")
    lines.append(f"총 {summary['total_calls']}회 · {summary['total_seconds']:.0f}초 · 상세는 Actions artifact")
    send_telegram("\n".join(lines))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        OUT.mkdir(exist_ok=True)
        (OUT / "error.txt").write_text(f"{type(e).__name__}: {e}")
        send_telegram(f"🧪 분봉 실측 테스트 {TICKER} 오류: {type(e).__name__}: {str(e)[:300]}")
        raise
