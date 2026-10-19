"""
DART 잠정실적 공시 본문(원문 XML) 수집기 — 새 정보원 2단계 / 이어받기 지원. 파싱은 하지 않음(원문 보관만).
- 대상: dart-data 브랜치 dart_data/*.csv 중 report_nm(공백 제거)에 '잠정'·'실적'이 모두 있고
  '['로 시작하지 않으며(정정·첨부 등 제외) '자회사'·'월별'이 없는 공시. rcept_no 순서로 고정.
- 본문: OpenDART document.xml(rcept_no) ZIP에서 {rcept_no}.xml(없으면 가장 큰 파일)만 꺼내
  earn_data/raw/{접수연도}.zip 안에 {rcept_no}.xml 로 원래 바이트 그대로 저장(파일 단위로 바로 추가 → 중간 중단에도 보존).
- 1회 실행은 TIME_BUDGET_MINUTES(기본 110분) 안에서만. 워크플로가 dart-earn 브랜치의 기존 earn_data/를
  ./existing_earn/earn_data/ 로 복사해 주면 그것을 이어받는다.
- 오류 처리: 응답이 ZIP이 아니면 status 코드를 읽는다.
    020(일 한도 초과) → 즉시 중단, progress.limit_hit=true(워크플로는 재트리거하지 않음, 다음 날 수동 실행)
    일시 실패가 연속 20건 → 중단, progress.halted=true(API 장애로 대량 영구 실패가 쌓이는 것 방지, 재트리거 안 함)
    010·011·012·901(키·권한 문제) → 즉시 중단(exit 1), progress.halted=true
    013·014(데이터·파일 없음) → 영구 실패 기록
    그 외·네트워크 오류·깨진 ZIP → 일시 실패(3회 누적 시 영구 실패)
- 기록: earn_data/targets.csv(대상 목록), earn_data/failures.csv, earn_data/progress.json
"""
import csv
import glob
import io
import json
import os
import re
import shutil
import sys
import time
import zipfile
from datetime import datetime, timezone

URL = "https://opendart.fss.or.kr/api/document.xml"
LIST_DIR = os.environ.get("EARN_LIST_DIR", "existing_dart_list/dart_data")
EXISTING_DIR = os.environ.get("EARN_EXISTING_DIR", "existing_earn/earn_data")
OUT_DIR = os.environ.get("EARN_OUT_DIR", "earn_data")
TIME_BUDGET_MINUTES = float(os.environ.get("TIME_BUDGET_MINUTES", "110"))
SLEEP = float(os.environ.get("EARN_SLEEP", "0.5"))
LOG_EVERY = 200
MAX_ATTEMPTS = 3
MAX_CONSEC_TEMP = 20
FATAL = {"010", "011", "012", "901"}
PERMANENT = {"013", "014"}
FAIL_FIELDS = ["rcept_no", "status", "message", "attempts", "permanent", "updated_at"]
TARGET_FIELDS = ["rcept_no", "rcept_dt", "stock_code", "corp_name", "corp_cls", "report_nm"]


def log(*a):
    print(*a, flush=True)


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_target(report_nm):
    nm = re.sub(r"\s+", "", report_nm or "")
    return ("잠정" in nm and "실적" in nm and not (report_nm or "").lstrip().startswith("[")
            and "자회사" not in nm and "월별" not in nm)


def load_targets(list_dir=LIST_DIR):
    files = sorted(glob.glob(os.path.join(list_dir, "*.csv")))
    if not files:
        raise SystemExit(f"대상 목록 csv 없음: {list_dir}")
    seen = {}
    for f in files:
        with open(f, encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh):
                if is_target(r.get("report_nm")):
                    rn = (r.get("rcept_no") or "").strip()
                    if re.fullmatch(r"\d{14}", rn):
                        seen[rn] = {k: (r.get(k) or "").strip() for k in TARGET_FIELDS}
    return [seen[k] for k in sorted(seen)]


def year_of(t):
    y = (t.get("rcept_dt") or "")[:4]
    return y if re.fullmatch(r"\d{4}", y) else t["rcept_no"][:4]


def restore_existing():
    if os.path.isdir(EXISTING_DIR) and not os.path.isdir(OUT_DIR):
        shutil.copytree(EXISTING_DIR, OUT_DIR)
        log(f"기존 수집분 복사: {EXISTING_DIR} -> {OUT_DIR}")
    os.makedirs(os.path.join(OUT_DIR, "raw"), exist_ok=True)


def stored_set():
    s = set()
    for z in glob.glob(os.path.join(OUT_DIR, "raw", "*.zip")):
        try:
            with zipfile.ZipFile(z) as zf:
                s |= {n[:-4] for n in zf.namelist() if n.endswith(".xml")}
        except zipfile.BadZipFile:
            raise SystemExit(f"저장 ZIP 손상: {z} (수동 확인 필요)")
    return s


def load_failures():
    p = os.path.join(OUT_DIR, "failures.csv")
    out = {}
    if os.path.exists(p):
        with open(p, encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                r["attempts"] = int(r["attempts"])
                r["permanent"] = r["permanent"] == "True"
                out[r["rcept_no"]] = r
    return out


def save_failures(fails):
    p = os.path.join(OUT_DIR, "failures.csv")
    with open(p, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FAIL_FIELDS)
        w.writeheader()
        for k in sorted(fails):
            w.writerow(fails[k])


def save_targets(targets):
    with open(os.path.join(OUT_DIR, "targets.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=TARGET_FIELDS)
        w.writeheader()
        w.writerows(targets)


def status_of(content):
    head = content[:500].decode("utf-8", errors="replace")
    m = re.search(r'status"?\s*[:>]\s*"?(\d{3})', head)
    msg = re.search(r'message"?\s*[:>]\s*"?([^"<]{0,120})', head)
    return (m.group(1) if m else "???"), (msg.group(1).strip() if msg else head[:120].replace("\n", " "))


def main_xml(content, rcept_no):
    z = zipfile.ZipFile(io.BytesIO(content))
    names = z.namelist()
    if not names:
        raise zipfile.BadZipFile("빈 ZIP")
    nm = f"{rcept_no}.xml" if f"{rcept_no}.xml" in names else max(names, key=lambda n: z.getinfo(n).file_size)
    return z.read(nm)


def append_raw(year, rcept_no, data):
    p = os.path.join(OUT_DIR, "raw", f"{year}.zip")
    with zipfile.ZipFile(p, "a", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        zf.writestr(f"{rcept_no}.xml", data)


def run(key, get=None, clock=time.monotonic, sleep=time.sleep):
    if get is None:
        import requests
        get = requests.get
    t0 = clock()
    deadline = t0 + TIME_BUDGET_MINUTES * 60
    restore_existing()
    targets = load_targets()
    save_targets(targets)
    stored = stored_set()
    fails = load_failures()
    todo = [t for t in targets if t["rcept_no"] not in stored and not fails.get(t["rcept_no"], {}).get("permanent")]
    log(f"대상 {len(targets):,}건 · 저장됨 {len(stored & {t['rcept_no'] for t in targets}):,} · 영구 실패 "
        f"{sum(1 for f in fails.values() if f['permanent']):,} · 이번 남은 작업 {len(todo):,} · 시간예산 {TIME_BUDGET_MINUTES}분")
    n_ok = n_fail = n_call = consec = 0
    stop_reason, limit_hit, halted = "모든 대상 처리", False, False
    for t in todo:
        if clock() >= deadline:
            stop_reason = "시간예산 소진"
            break
        rn = t["rcept_no"]
        n_call += 1
        try:
            r = get(URL, params={"crtfc_key": key, "rcept_no": rn}, timeout=30)
            content = r.content
            if content[:2] == b"PK":
                append_raw(year_of(t), rn, main_xml(content, rn))
                stored.add(rn)
                fails.pop(rn, None)
                n_ok += 1
                consec = 0
            else:
                code, msg = status_of(content)
                if code == "020":
                    stop_reason, limit_hit = f"일 한도 초과(020): {msg}", True
                    break
                if code in FATAL:
                    save_failures(fails)
                    write_progress(targets, stored, fails, False, f"치명 오류 {code}: {msg}", False, True)
                    log(f"치명 오류 {code} {msg} — 중단(키·권한 확인 필요)")
                    sys.exit(1)
                rec = fails.get(rn, {"rcept_no": rn, "attempts": 0})
                rec.update(status=code, message=msg, attempts=rec["attempts"] + 1, updated_at=now())
                rec["permanent"] = code in PERMANENT or rec["attempts"] >= MAX_ATTEMPTS
                fails[rn] = rec
                n_fail += 1
                consec = 0 if code in PERMANENT else consec + 1
        except (zipfile.BadZipFile, OSError, Exception) as ex:  # noqa: BLE001 — 네트워크·ZIP 오류는 일시 실패
            if isinstance(ex, SystemExit):
                raise
            rec = fails.get(rn, {"rcept_no": rn, "attempts": 0})
            rec.update(status="EXC", message=f"{type(ex).__name__}: {str(ex)[:100]}", attempts=rec["attempts"] + 1, updated_at=now())
            rec["permanent"] = rec["attempts"] >= MAX_ATTEMPTS
            fails[rn] = rec
            n_fail += 1
            consec += 1
        if consec >= MAX_CONSEC_TEMP:
            stop_reason, halted = f"일시 실패 연속 {consec}건(마지막 {fails[rn]['status']}: {fails[rn]['message']})", True
            break
        if n_call % LOG_EVERY == 0:
            save_failures(fails)
            log(f"  진행 {n_call:,}/{len(todo):,} · 성공 {n_ok:,} · 실패 {n_fail:,} · 경과 {(clock()-t0)/60:.1f}분")
        sleep(SLEEP)
    save_failures(fails)
    left = [t for t in targets if t["rcept_no"] not in stored and not fails.get(t["rcept_no"], {}).get("permanent")]
    completed = len(left) == 0
    write_progress(targets, stored, fails, completed, stop_reason, limit_hit, halted)
    log(f"이번 실행: 호출 {n_call:,} · 성공 {n_ok:,} · 실패 {n_fail:,} · 종료 사유: {stop_reason}")
    log("전체 수집 완료!" if completed else (f"남은 {len(left):,}건 — " + ("일 한도 초과: 다음 날 수동 실행" if limit_hit else "연속 오류로 정지: 원인 확인 후 수동 실행" if halted else "다음 자동 실행에서 이어감")))
    return completed, limit_hit, halted


def write_progress(targets, stored, fails, completed, reason, limit_hit, halted=False):
    tset = {t["rcept_no"] for t in targets}
    by_year = {}
    for t in targets:
        y = year_of(t)
        d = by_year.setdefault(y, [0, 0])
        d[0] += 1
        d[1] += t["rcept_no"] in stored
    prog = {"total": len(targets), "stored": len(stored & tset),
            "failed_permanent": sum(1 for k, f in fails.items() if f["permanent"] and k in tset),
            "failed_temporary": sum(1 for k, f in fails.items() if not f["permanent"] and k in tset),
            "completed": completed, "limit_hit": limit_hit, "halted": halted, "last_stop_reason": reason, "updated_at": now(),
            "by_year_total_stored": {y: v for y, v in sorted(by_year.items())}}
    with open(os.path.join(OUT_DIR, "progress.json"), "w", encoding="utf-8") as fh:
        json.dump(prog, fh, ensure_ascii=False, indent=2)
    log(f"progress: {json.dumps({k: v for k, v in prog.items() if k != 'by_year_total_stored'}, ensure_ascii=False)}")


if __name__ == "__main__":
    run(os.environ["DART_API_KEY"])
