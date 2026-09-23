"""
DART (전자공시시스템) 공시 리스트 수집기 — 일일 자동 수집 / 이어받기 지원
- KOSPI(Y) + KOSDAQ(K) 전체 공시를 2018-01-01 ~ 오늘까지, 90일 단위로 수집
- 1회 실행은 TIME_BUDGET_MINUTES(기본 110분) 안에서만 진행하고,
  어디까지 했는지 dart_data/progress.json에 저장한다.
  다음 실행은 워크플로가 dart-data 브랜치의 기존 dart_data/를
  ./existing_dart_data/dart_data/ 로 미리 복사해준 뒤 이 스크립트를 실행하므로,
  progress.json을 읽어 중단된 지점부터 이어서 진행한다.
- 완료(모든 구간 끝) 후에는 progress.json의 completed=true를 보고 즉시 스킵한다.
"""
import csv
import json
import os
import time
from datetime import date, datetime, timedelta

import requests

API_KEY = os.environ["DART_API_KEY"]
BASE_URL = "https://opendart.fss.or.kr/api/list.json"
EXISTING_DIR = "existing_dart_data/dart_data"
OUT_DIR = "dart_data"
START_DATE = date(2018, 1, 1)
END_DATE = date.today()
CHUNK_DAYS = 90  # DART API 기간 제한(3개월) 대비 안전 마진
PAGE_COUNT = 100  # API 최대값
MAX_RETRIES = 3
TIME_BUDGET_MINUTES = float(os.environ.get("TIME_BUDGET_MINUTES", "110"))

FIELDNAMES = [
    "corp_cls", "corp_code", "corp_name", "stock_code",
    "report_nm", "rcept_no", "flr_nm", "rcept_dt", "rm",
]

MARKETS = [("Y", "KOSPI"), ("K", "KOSDAQ")]


def date_chunks(start, end, days=CHUNK_DAYS):
    chunks = []
    cur = start
    while cur <= end:
        nxt = min(cur + timedelta(days=days - 1), end)
        chunks.append((cur, nxt))
        cur = nxt + timedelta(days=1)
    return chunks


def build_plan():
    """전체 작업 목록: (corp_cls, label, bgn, end) — 실행마다 항상 동일한 순서로 생성됨"""
    plan = []
    for corp_cls, label in MARKETS:
        for bgn, end in date_chunks(START_DATE, END_DATE):
            plan.append((corp_cls, label, bgn, end))
    return plan


def load_progress():
    path = os.path.join(EXISTING_DIR, "progress.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {"done_index": -1, "completed": False}


def load_existing_rows():
    rows = []
    if os.path.isdir(EXISTING_DIR):
        for fname in sorted(os.listdir(EXISTING_DIR)):
            if fname.endswith(".csv"):
                with open(os.path.join(EXISTING_DIR, fname), encoding="utf-8-sig") as f:
                    rows.extend(list(csv.DictReader(f)))
    return rows


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


def fetch_chunk(corp_cls, label, bgn, end):
    rows = []
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
    return rows


def save_all(rows, progress):
    os.makedirs(OUT_DIR, exist_ok=True)

    # rcept_no 기준 중복 제거 (이어받기 경계에서 겹치는 경우 대비)
    dedup = {}
    for r in rows:
        dedup[r.get("rcept_no")] = r
    rows = list(dedup.values())

    by_year = {}
    for r in rows:
        dt = r.get("rcept_dt") or ""
        yr = dt[:4] if len(dt) >= 4 else "unknown"
        by_year.setdefault(yr, []).append(r)

    for yr, items in sorted(by_year.items()):
        path = os.path.join(OUT_DIR, f"{yr}.csv")
        items_sorted = sorted(items, key=lambda x: (x.get("rcept_dt") or "", x.get("rcept_no") or ""))
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=FIELDNAMES)
            w.writeheader()
            for it in items_sorted:
                w.writerow(it)
        print(f"saved {path}: {len(items_sorted)} rows")

    with open(os.path.join(OUT_DIR, "progress.json"), "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)
    print(f"progress saved: {progress}")


def main():
    start_time = time.monotonic()
    deadline = start_time + TIME_BUDGET_MINUTES * 60

    plan = build_plan()
    progress = load_progress()

    if progress.get("completed"):
        print("이미 전체 수집 완료됨 (progress.json completed=true) — 이번 실행은 스킵")
        save_all(load_existing_rows(), progress)
        return

    done_index = progress.get("done_index", -1)
    print(f"전체 작업 {len(plan)}개 중 {done_index + 1}번째부터 재개 (시간예산 {TIME_BUDGET_MINUTES}분)")

    all_rows = load_existing_rows()
    print(f"기존 수집분: {len(all_rows)}건")

    idx = done_index
    for i in range(done_index + 1, len(plan)):
        if time.monotonic() >= deadline:
            print(f"시간예산 소진 — {i}번째 작업 시작 전 중단 (다음 실행에서 이어감)")
            break
        corp_cls, label, bgn, end = plan[i]
        rows = fetch_chunk(corp_cls, label, bgn, end)
        all_rows.extend(rows)
        idx = i
        print(f"진행: {idx + 1}/{len(plan)} 완료")

    completed = (idx == len(plan) - 1)
    progress = {
        "done_index": idx,
        "total": len(plan),
        "completed": completed,
        "updated_at": datetime.utcnow().isoformat(),
    }
    save_all(all_rows, progress)
    if completed:
        print("전체 수집 완료!")
    else:
        print(f"오늘은 여기까지 ({idx + 1}/{len(plan)}) — 다음 자동 실행에서 이어서 진행됩니다.")


if __name__ == "__main__":
    main()
