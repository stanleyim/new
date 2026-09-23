"""
DART (전자공시시스템) 공시 리스트 수집기
- KOSPI(corp_cls=Y) + KOSDAQ(corp_cls=K) 전체 공시를 2018-01-01 ~ 오늘까지 수집
- DART API 기간 제한(최대 3개월)에 맞춰 90일 단위로 나눠서 호출
- 결과를 rcept_dt(접수일자) 기준 연도별 CSV로 저장 (dart_data/YYYY.csv)
- 필터링(종목 유니버스 매칭 등)은 이 단계에서 하지 않음 — 원본 그대로 저장
"""
import csv
import os
import time
from datetime import date, timedelta

import requests

API_KEY = os.environ["DART_API_KEY"]
BASE_URL = "https://opendart.fss.or.kr/api/list.json"
OUT_DIR = "dart_data"
START_DATE = date(2018, 1, 1)
END_DATE = date.today()
CHUNK_DAYS = 90  # DART API 기간 제한(3개월) 대비 안전 마진
PAGE_COUNT = 100  # API 최대값
MAX_RETRIES = 3

FIELDNAMES = [
    "corp_cls", "corp_code", "corp_name", "stock_code",
    "report_nm", "rcept_no", "flr_nm", "rcept_dt", "rm",
]


def date_chunks(start, end, days=CHUNK_DAYS):
    chunks = []
    cur = start
    while cur <= end:
        nxt = min(cur + timedelta(days=days - 1), end)
        chunks.append((cur, nxt))
        cur = nxt + timedelta(days=1)
    return chunks


def fetch_page(corp_cls, bgn_de, end_de, page_no):
    params = {
        "crtfc_key": API_KEY,
        "bgn_de": bgn_de.strftime("%Y%m%d"),
        "end_de": end_de.strftime("%Y%m%d"),
        "corp_cls": corp_cls,
        "page_no": page_no,
        "page_count": PAGE_COUNT,
    }
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(BASE_URL, params=params, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            last_err = e
            wait = 2 * attempt
            print(f"  retry {attempt}/{MAX_RETRIES} after error: {e} (sleep {wait}s)")
            time.sleep(wait)
    raise RuntimeError(f"fetch_page failed after {MAX_RETRIES} retries: {last_err}")


def collect():
    rows = []
    for corp_cls, label in [("Y", "KOSPI"), ("K", "KOSDAQ")]:
        for bgn, end in date_chunks(START_DATE, END_DATE):
            page_no = 1
            while True:
                data = fetch_page(corp_cls, bgn, end, page_no)
                status = data.get("status")
                if status == "013":  # 조회된 데이터 없음
                    break
                if status != "000":
                    print(f"ERROR {label} {bgn}~{end} page {page_no}: {data}")
                    break
                items = data.get("list", [])
                for it in items:
                    rows.append({
                        "corp_cls": label,
                        "corp_code": it.get("corp_code"),
                        "corp_name": it.get("corp_name"),
                        "stock_code": it.get("stock_code"),
                        "report_nm": it.get("report_nm"),
                        "rcept_no": it.get("rcept_no"),
                        "flr_nm": it.get("flr_nm"),
                        "rcept_dt": it.get("rcept_dt"),
                        "rm": it.get("rm"),
                    })
                total_page = data.get("total_page", 1)
                print(f"{label} {bgn}~{end} page {page_no}/{total_page} rows={len(items)}")
                if page_no >= total_page:
                    break
                page_no += 1
                time.sleep(0.3)
            time.sleep(0.3)
    return rows


def save_by_year(rows):
    os.makedirs(OUT_DIR, exist_ok=True)
    by_year = {}
    for r in rows:
        dt = r.get("rcept_dt") or ""
        yr = dt[:4] if len(dt) >= 4 else "unknown"
        by_year.setdefault(yr, []).append(r)

    for yr, items in sorted(by_year.items()):
        path = os.path.join(OUT_DIR, f"{yr}.csv")
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=FIELDNAMES)
            w.writeheader()
            for it in items:
                w.writerow(it)
        print(f"saved {path}: {len(items)} rows")


if __name__ == "__main__":
    print(f"수집 범위: {START_DATE} ~ {END_DATE}")
    all_rows = collect()
    print(f"총 수집 건수: {len(all_rows)}")
    save_by_year(all_rows)
    print("완료")
