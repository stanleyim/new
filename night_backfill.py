"""
night_backfill.py — 지난 날의 코스피200 선물 밤(15:30 → 06:00)을 night_alert/futures_night.csv에 한 번에 채움 (수동 실행, 주문 없음)

계산은 night_alert.py와 같음(그 파일의 함수를 그대로 씀): 날짜 D 주간 15:30 봉 → D 밤 야간 마감(300000) 봉, 같은 종목코드.
다른 점은 종목코드 하나뿐: 날짜 D마다 그때의 최근월물 = 만기(3·6·9·12월 둘째 목요일)가 D보다 뒤인 첫 월물.
  코드 A01 + 연도 끝자리 + 월 (A01603 = 2026-03월물). 만기일 당일은 다음 월물.
점검: 고른 코드로 D 주간 봉이 없는데 다음 월물에는 있으면 '코드 의심'으로 세고 그 날은 저장하지 않음.
같은 날짜가 CSV에 이미 있으면 건너뜀. 끝나면 CSV를 날짜 순으로 정렬.
기간: START(기본 20251212, 선물 분봉이 받히는 첫날 — verify_open_recover 출력) ~ END(기본: 야간장이 끝난 마지막 날).
"""
import os
import csv
import datetime as dt

import night_alert as na

QUARTER = (3, 6, 9, 12)


def second_thursday(y, m):
    d = dt.date(y, m, 1)
    d += dt.timedelta(days=(3 - d.weekday()) % 7)
    return d + dt.timedelta(days=7)


def front(d, skip=0):
    """날짜 d의 최근월물 (코드, 이름). skip=1이면 그다음 월물."""
    out = []
    for y in (d.year, d.year + 1):
        for m in QUARTER:
            if second_thursday(y, m) > d:
                out.append((f"A01{y % 10}{m:02d}", f"F {y}{m:02d}"))
    return out[skip]


def bar_retry(*a):
    last = None
    for k in range(3):
        try:
            return na.bar(*a)
        except na.Fail as e:
            last = e
            na.time.sleep(1 + k)
    raise last


def sort_csv():
    if not os.path.exists(na.CSV_PATH):
        return 0
    with open(na.CSV_PATH, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    rows.sort(key=lambda r: r["date"])
    with open(na.CSV_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=na.CSV_COLS)
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def main():
    now = dt.datetime.now(na.KST)
    start = dt.datetime.strptime(os.environ.get("BF_START") or "20251212", "%Y%m%d").date()
    last_done = now.date() - dt.timedelta(days=1 if now.hour >= 6 else 2)   # 야간장(06:00 마감)이 끝난 마지막 날
    end = dt.datetime.strptime(os.environ["BF_END"], "%Y%m%d").date() if os.environ.get("BF_END") else last_done
    end = min(end, last_done)
    na.log(f"실행 {now:%Y-%m-%d %H:%M} KST · 기간 {start} ~ {end}")
    key, sec = os.environ.get("KIS_APP_KEY"), os.environ.get("KIS_APP_SECRET")
    if not key or not sec:
        raise na.Fail("KIS 비밀값(KIS_APP_KEY·KIS_APP_SECRET) 없음")
    tok = na.token(key, sec)

    have = set()
    if os.path.exists(na.CSV_PATH):
        with open(na.CSV_PATH, newline="", encoding="utf-8") as f:
            have = {r["date"] for r in csv.DictReader(f)}

    cnt = {"저장": 0, "이미 있음": 0, "휴장(주간 봉 없음)": 0, "야간 봉 없음": 0, "코드 의심": 0}
    first = None
    d = start
    while d <= end:
        if d.weekday() >= 5:
            d += dt.timedelta(days=1)
            continue
        ds = d.strftime("%Y%m%d")
        if ds in have:
            cnt["이미 있음"] += 1
            d += dt.timedelta(days=1)
            continue
        code, name = front(d)
        b0 = bar_retry(tok, key, sec, code, "F", ds, "153000")
        if b0 is None:
            code2, _ = front(d, 1)
            if bar_retry(tok, key, sec, code2, "F", ds, "153000"):
                na.log(f"[점검] 코드 의심 {ds}: {code} 봉 없음, {code2} 봉 있음 → 저장 안 함")
                cnt["코드 의심"] += 1
            else:
                cnt["휴장(주간 봉 없음)"] += 1
            d += dt.timedelta(days=1)
            continue
        n = bar_retry(tok, key, sec, code, "CM", ds, "300000")
        p0 = na._num(b0["futs_prpr"])
        row = {"date": ds, "code": code, "name": name, "base_time": b0["stck_cntg_hour"], "base_price": f"{p0:.2f}",
               "night_time": "", "night_price": "", "change_pct": "", "run_kst": f"{now:%Y-%m-%d %H:%M}"}
        if n is None:
            cnt["야간 봉 없음"] += 1
        else:
            p1 = na._num(n["futs_prpr"])
            row.update(night_time=n["stck_cntg_hour"], night_price=f"{p1:.2f}", change_pct=f"{(p1 / p0 - 1) * 100:.4f}")
        na.save_row(row)
        have.add(ds)
        cnt["저장"] += 1
        first = first or ds
        d += dt.timedelta(days=1)

    total = sort_csv()
    summary = (f"🌃 선물 밤 채우기 완료 · {start:%m-%d}~{end:%m-%d}\n"
               + " · ".join(f"{k} {v}" for k, v in cnt.items())
               + f"\n처음 저장한 날 {first or '없음'} · CSV 전체 {total}줄")
    na.log(summary)
    na.send_telegram(summary)


if __name__ == "__main__":
    try:
        main()
    except na.Fail as e:
        na.log(f"[실패] {e}")
        try:
            sort_csv()
        except Exception:
            pass
        na.send_telegram(f"⚠️ 선물 밤 채우기 실패: {e} (그때까지 저장한 줄은 남음)")
        raise SystemExit(1)
    except Exception as e:
        na.log(f"[실패] 예상 못 한 오류 {type(e).__name__}: {str(e)[:150]}")
        try:
            sort_csv()
        except Exception:
            pass
        na.send_telegram(f"⚠️ 선물 밤 채우기 실패: 예상 못 한 오류 {type(e).__name__}")
        raise SystemExit(1)
