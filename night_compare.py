"""
night_compare.py — 같은 밤끼리 나란히: 코스피200 선물 밤 vs 코스피200 지수 밤 vs KODEX 200 밤 (수동 실행, 읽기 전용, 레포에 쓰지 않음, 주문 없음)

밤 = 매수일 D → 다음 거래일 N. N이 월요일이면 뺌(기존 규칙과 같음).
  선물 밤  = night_alert/futures_night.csv의 change_pct (D 15:30 봉 → D 밤 06:00 마감 봉, 같은 월물)
  지수 밤  = 코스피200 시가(N) / 코스피200 종가(D) − 1
  KODEX 밤 = KODEX 200 시가(N) / KODEX 200 종가(D) − 1
세 값이 모두 있는 밤만 씀(같은 밤 집합). 모두 비용·세금 전. 참고로 KODEX는 0.006% 뺀 값도 출력.
가져온 비율 = 하루 평균(선물 밤) / 하루 평균(지수 밤). 판정 없음(숫자 보고만).
지수·ETF 일봉 받기는 verify_open_noise.py / verify_lev_night.py(11-03 Actions 실행 성공)와 같은 pykrx 호출을 그대로 씀.
시험용 환경변수: NC_FAKE_DIR=폴더(IDX.csv: 날짜,시가,종가 / 069500.csv: 날짜,시가,종가).
"""
import os
import sys
import csv
import math
import time
import datetime as dt

from night_alert import send_telegram

CSV_PATH = os.path.join("night_alert", "futures_night.csv")
IDX, KODEX = "1028", "069500"
COST = 0.00006


def log(*a):
    print(*a, flush=True)


def load_daily(kind, start, end):
    """{날짜: (시가, 종가)}. kind = 'idx' 또는 ETF 코드."""
    fake = os.environ.get("NC_FAKE_DIR")
    if fake:
        out = {}
        fn = "IDX.csv" if kind == "idx" else f"{kind}.csv"
        for ln in open(os.path.join(fake, fn), encoding="utf-8").read().splitlines()[1:]:
            d, o, c = ln.split(",")
            out[d] = (float(o), float(c))
        return out
    from pykrx import stock
    last = None
    for k in range(3):
        try:
            if kind == "idx":
                df = stock.get_index_ohlcv_by_date(start, end, IDX, name_display=False)
            else:
                df = stock.get_etf_ohlcv_by_date(start, end, kind)
            break
        except Exception as e:
            last = e
            time.sleep(3 + 3 * k)
    else:
        sys.exit(f"KRX 조회 실패 {kind}: {type(last).__name__}: {str(last)[:150]}")
    return {i.strftime("%Y%m%d"): (float(r["시가"]), float(r["종가"])) for i, r in df.iterrows()}


def comp(xs):
    return (math.prod(1 + x for x in xs) - 1) * 100


def main():
    fut = {}
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["change_pct"]:
                fut[r["date"]] = float(r["change_pct"]) / 100
    ds = sorted(fut)
    start = ds[0]
    end = (dt.datetime.strptime(ds[-1], "%Y%m%d") + dt.timedelta(days=14)).strftime("%Y%m%d")
    log(f"[선물] {len(fut)}밤 · {ds[0]}~{ds[-1]}")
    idx = load_daily("idx", start, end)
    kdx = load_daily(KODEX, start, end)
    days = sorted(idx)
    log(f"[지수 {IDX}] {days[0]}~{days[-1]} · {len(days)}일 · [KODEX 200] {len(kdx)}일")
    log(f"[대조점] 지수 20261102 {idx.get('20261102')} (외부 기록 종가 920.63) · KODEX 20261102 {kdx.get('20261102')} (etf_daily 종가 92,945)")
    notday = [d for d in ds if d not in idx]
    log(f"[점검] 선물 날짜 중 지수 거래일이 아닌 날 {len(notday)} {notday[:5]}")

    rows, mon, noN = [], 0, 0
    for d in ds:
        if d not in idx:
            continue
        later = [x for x in days if x > d]
        if not later:
            noN += 1
            continue
        n = later[0]
        if dt.datetime.strptime(n, "%Y%m%d").weekday() == 0:
            mon += 1
            continue
        if d not in kdx or n not in kdx:
            continue
        g = idx[n][0] / idx[d][1] - 1
        k = kdx[n][0] / kdx[d][1] - 1
        rows.append((d, n, fut[d], g, k))
    log(f"[밤] 같은 밤 {len(rows)} · 월요일 넘김 뺌 {mon} · 다음 거래일 자료 없음 {noN}")
    if not rows:
        sys.exit("같은 밤이 없음")

    F, G, K = [r[2] for r in rows], [r[3] for r in rows], [r[4] for r in rows]
    n = len(rows)
    mF, mG, mK = sum(F) / n, sum(G) / n, sum(K) / n
    log("\n월 | 밤 | 선물 누적 | 지수 누적 | KODEX 누적")
    for ym in sorted({r[0][:6] for r in rows}):
        s = [r for r in rows if r[0][:6] == ym]
        log(f"{ym[:4]}-{ym[4:]} | {len(s):2d} | {comp([x[2] for x in s]):+7.2f}% | {comp([x[3] for x in s]):+7.2f}% | {comp([x[4] for x in s]):+7.2f}%")
    log(f"\n전체 {rows[0][0]}~{rows[-1][0]} · {n}밤 (비용·세금 전)")
    log(f"선물 밤 : 누적 {comp(F):+.2f}% · 하루 평균 {mF:+.4%} · 플러스 {sum(x > 0 for x in F)}/{n}")
    log(f"지수 밤 : 누적 {comp(G):+.2f}% · 하루 평균 {mG:+.4%} · 플러스 {sum(x > 0 for x in G)}/{n}")
    log(f"KODEX 밤: 누적 {comp(K):+.2f}% · 하루 평균 {mK:+.4%} · 플러스 {sum(x > 0 for x in K)}/{n} · 비용 0.006% 뺀 누적 {comp([x - COST for x in K]):+.2f}%")
    log(f"가져온 비율(하루 평균 기준): 선물 {mF / mG:.2f} · KODEX {mK / mG:.2f}")
    agree = sum(1 for f, g in zip(F, G) if (f > 0) == (g > 0))
    log(f"선물 밤과 지수 밤 부호 같은 밤 {agree}/{n}")
    log("선물 − 지수 차이가 큰 5밤 (매수일→매도일 · 선물 · 지수 · KODEX):")
    for r in sorted(rows, key=lambda r: abs(r[2] - r[3]), reverse=True)[:5]:
        log(f"  {r[0]}→{r[1]} · {r[2]:+.2%} · {r[3]:+.2%} · {r[4]:+.2%}")

    msg = (f"📐 같은 밤 비교 · {rows[0][0][2:4]}-{rows[0][0][4:6]}-{rows[0][0][6:]}~{rows[-1][0][4:6]}-{rows[-1][0][6:]} · {n}밤 (비용·세금 전)\n"
           f"선물 {comp(F):+.1f}% · 지수 {comp(G):+.1f}% · KODEX {comp(K):+.1f}%\n"
           f"하루 평균: 선물 {mF:+.3%} · 지수 {mG:+.3%} · KODEX {mK:+.3%}\n"
           f"지수 대비 가져온 비율: 선물 {mF / mG:.2f} · KODEX {mK / mG:.2f}")
    log("\n" + msg)
    send_telegram(msg)


if __name__ == "__main__":
    main()
