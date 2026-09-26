"""
retry_ticker_chunk.py

목적: 특정 종목 + 특정 기간 하나를 단발 재수집한다 (retry_006140.py의 일반화 버전 —
로직은 100% 동일, 하드코딩된 종목/기간을 환경변수로 뺐을 뿐).

배경: kis_delisted_collect.yml에는 종목+기간 지정 재수집 옵션이 없음
(workflow_dispatch의 유일한 입력 ticker_limit은 "앞 N종목" 테스트용 제한 —
확인 완료, 2026-09-26). flagged.json에 남는 개별 항목을 매번 새 스크립트로
만들지 않도록 이번에 범용화함.

실행 전제:
  - delisted-data 브랜치의 delisted_data/ 폴더가 현재 작업 디렉토리에 있을 것
  - 이 파일과 collect_delisted_full.py(main 브랜치 최신본)가 같은 디렉토리에 있을 것
  - 환경변수: KIS_APP_KEY, KIS_APP_SECRET, RETRY_TICKER, RETRY_START(YYYY-MM-DD),
    RETRY_END(YYYY-MM-DD)

사용법 (로컬):
    git checkout delisted-data
    cp <main 브랜치>/collect_delisted_full.py .
    KIS_APP_KEY=... KIS_APP_SECRET=... \
    RETRY_TICKER=006140 RETRY_START=2016-06-19 RETRY_END=2016-09-26 \
    python retry_ticker_chunk.py
    git add delisted_data && git commit -m "006140 2016-06-19~09-26 수동 재수집" && git push

결과:
  - 정상 수신 시: {ticker}.parquet에 병합, flagged.json에서 해당 (ticker, start, end)
    항목 제거, anomaly_log.jsonl에 resolved_on_manual_retry 기록
  - 여전히 이상 시: flagged.json은 그대로 두고 still_flagged 로그만 추가.
    자동으로 제외 처리하지 않음 — 판단은 사람이 함 (제외/포함 민감도 비교로 진행).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from collect_delisted_full import (  # noqa: E402  (경로 삽입 후 임포트 — 의도된 순서)
    KisClient,
    validate_response,
    _append_to_parquet,
    save_raw_anomaly,
    log_anomaly_event,
    FLAGGED_PATH,
)


def _require_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"환경변수 {name} 없음 — RETRY_TICKER/RETRY_START/RETRY_END 모두 필요")
    return val


def _remove_from_flagged(ticker: str, start: date, end: date) -> None:
    if not FLAGGED_PATH.exists():
        return
    flagged = json.loads(FLAGGED_PATH.read_text(encoding="utf-8"))
    remaining = [
        f for f in flagged
        if not (f["ticker"] == ticker and f["start"] == start.isoformat() and f["end"] == end.isoformat())
    ]
    if len(remaining) != len(flagged):
        FLAGGED_PATH.write_text(json.dumps(remaining, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    ticker = _require_env("RETRY_TICKER")
    start = date.fromisoformat(_require_env("RETRY_START"))
    end = date.fromisoformat(_require_env("RETRY_END"))

    client = KisClient()
    raw = client.get_ohlcv_raw(ticker, start.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
    result = validate_response(raw, ticker, start, end)

    if result.is_valid:
        _append_to_parquet(ticker, result.df)
        log_anomaly_event({
            "ticker": ticker, "start": start.isoformat(), "end": end.isoformat(),
            "attempt": "manual_retry", "outcome": "resolved_on_manual_retry",
            "rows_saved": int(len(result.df)),
        })
        if result.benign_zero_vol_dates:
            log_anomaly_event({
                "ticker": ticker, "start": start.isoformat(), "end": end.isoformat(),
                "outcome": "benign_zero_volume_rows_kept",
                "dates": result.benign_zero_vol_dates,
            })
        _remove_from_flagged(ticker, start, end)
        print(f"[RESOLVED] {ticker} {start}~{end} — {len(result.df)}건 저장, flagged.json에서 제거 완료")
    else:
        raw_path = save_raw_anomaly(raw, ticker, start, end, attempt=99)
        log_anomaly_event({
            "ticker": ticker, "start": start.isoformat(), "end": end.isoformat(),
            "attempt": "manual_retry", "reasons": result.reasons,
            "raw_saved_to": str(raw_path), "outcome": "still_flagged",
        })
        print(f"[STILL FLAGGED] {ticker} {start}~{end} reasons={result.reasons}")
        print("→ flagged.json은 그대로 둠. 제외/포함 민감도 비교로 진행할 것.")


if __name__ == "__main__":
    main()
