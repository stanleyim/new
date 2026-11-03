"""
verify_open_noise.py — ETF 시가 잡음 측정 (읽기 전용, 레포에 쓰지 않음, 주문 없음)

무엇을 재나 (정의, 해석 아님):
  같은 밤(매수일 d 종가 → 다음 거래일 n 시가, n이 월요일이면 제외)마다
    ETF 밤   rE = ETF 시가(n) / ETF 종가(d) − 1
    지수 밤  gI = 코스피200 시가(n) / 코스피200 종가(d) − 1
    차이     e  = rE − L × gI   (L = 배수. KODEX 200·ACE 200 = 1, 레버리지 = 2)
  비교용으로 같은 날짜 쌍의 종가→종가 차이 c = (ETF 종가(n)/종가(d) − 1) − L × (지수 종가(n)/종가(d) − 1)도 잼.
  L은 이름으로 정하지 않고 자료로 확인: 종가→종가 일간 수익을 지수 일간 수익에 회귀한 기울기를 출력.

자료 점검(출력): 지수 티커 1028 이름 · 지수 종가와 KODEX 200 '기초지수' 일치 일수 · 지수 시가 = 전일 종가인 날 수 ·
  대조점(11-02 지수 종가, 외부 기록 920.63).
판정(실행 전 고정): ETF별 2016~2024에서
  CAGR(ETF 밤 − 비용) 와 CAGR(L × 지수 밤 − 비용) 의 차이 절댓값 ≤ 1.0%p → "시가 잡음 영향 작음", 초과 → "큼".
  (1.0%p는 보고용으로 정한 선, 계산 근거 없음. 숫자는 판정과 무관하게 모두 출력)
비용 0.006%, |ETF 밤| ≥ 30% 제외, 연도 귀속 매수일 기준 — 앞의 독립 재계산과 같음.
시험용 환경변수: VN_FAKE_DIR=폴더(<코드>.csv: 날짜,시가,종가,기초지수 / IDX.csv: 날짜,시가,종가).
"""
import os
import sys
import math
import time
import datetime as dt

from verify_kodex_night import log, weekday, ok  # noqa: F401  (11-03 '일치' 판정 받은 파일)
from verify_lev_night import load as load_etf

ETFS = {"069500": ("KODEX 200", 1), "105190": ("ACE 200", 1),
        "122630": ("KODEX 레버리지", 2), "123320": ("TIGER 레버리지", 2)}
IDX = "1028"
COST, CA_ABS = 0.00006, 0.30
FROM_YEAR = 2014
LIMIT = 1.0   # %p, 판정선


def load_idx():
    fake = os.environ.get("VN_FAKE_DIR")
    if fake:
        out = {}
        for ln in open(os.path.join(fake, "IDX.csv"), encoding="utf-8").read().splitlines()[1:]:
            d, o, c = ln.split(",")
            out[d] = (float(o), float(c))
        return out, "가짜 지수"
    from pykrx import stock
    name = stock.get_index_ticker_name(IDX)
    today = dt.date.today().strftime("%Y%m%d")
    out = {}
    for y in range(FROM_YEAR, dt.date.today().year + 1):
        a, b = f"{y}0101", min(f"{y}1231", today)
        last = None
        for k in range(3):
            try:
                df = stock.get_index_ohlcv_by_date(a, b, IDX, name_display=False)
                break
            except Exception as e:
                last = e
                time.sleep(3 + 3 * k)
        else:
            sys.exit(f"지수 조회 실패 {y}: {type(last).__name__}: {str(last)[:150]}")
        for idx, r in df.iterrows():
            out[idx.strftime("%Y%m%d")] = (float(r["시가"]), float(r["종가"]))
        time.sleep(1)
    return out, name


def stats(xs):
    s = sorted(xs)
    n = len(s)
    m = sum(s) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in s) / (n - 1))
    q = lambda p: s[min(n - 1, int(p * (n - 1)))]
    return {"n": n, "mean": m, "sd": sd, "p01": q(0.01), "p50": q(0.5), "p99": q(0.99),
            "gt1": sum(1 for x in s if abs(x) > 0.01), "gt3": sum(1 for x in s if abs(x) > 0.03)}


def slope(xs, ys):
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)


def cagr(rs, yrs):
    eq = 1.0
    for r in rs:
        eq *= 1 + r
    return (eq ** (1 / yrs) - 1) * 100


def main():
    idx, name = load_idx()
    ds = sorted(idx)
    log(f"[지수] 티커 {IDX} 이름 '{name}' · {ds[0]}~{ds[-1]} · {len(ds)}일 · 처음 {ds[0]} {idx[ds[0]]} · 마지막 {ds[-1]} {idx[ds[-1]]}")
    same_open = sum(1 for a, b in zip(ds, ds[1:]) if idx[b][0] == idx[a][1])
    zero = sum(1 for d in ds if not (idx[d][0] > 0 and idx[d][1] > 0))
    log(f"[지수 점검] 시가 = 전일 종가인 날 {same_open} · 0 값인 날 {zero}")
    for a in ["20261102", "20261103"]:
        log(f"[대조점] 지수 {a}: 시가·종가 {idx.get(a)}  (외부 기록: 11-02 코스피200 현물 종가 920.63)")

    verdict = {}
    for t, (nm, L) in ETFS.items():
        rows = load_etf(t)
        log(f"\n##### {nm} ({t}) · 배수 L = {L} #####")
        log(f"[자료] {rows[0][0]}~{rows[-1][0]} · {len(rows)}거래일")
        if t == "069500":
            both = [(r[0], r[3], idx[r[0]][1]) for r in rows if r[0] in idx]
            eq = sum(1 for _, a, b in both if abs(a - b) < 0.005)
            log(f"[자료 점검] KODEX 200 '기초지수' = 지수 {IDX} 종가인 날 {eq}/{len(both)} · 다른 날 예: "
                f"{[x for x in both if abs(x[1] - x[2]) >= 0.005][:3]}")
        miss = [r[0] for r in rows if r[0] not in idx]
        log(f"[자료 점검] ETF 날짜 중 지수 자료 없는 날 {len(miss)} {miss[:5]}")

        # 배수 확인: 종가→종가 일간
        xs, ys = [], []
        for a, b in zip(rows, rows[1:]):
            if a[0] in idx and b[0] in idx and a[2] > 0 and b[2] > 0:
                xs.append(idx[b[0]][1] / idx[a[0]][1] - 1)
                ys.append(b[2] / a[2] - 1)
        log(f"[배수 확인] 종가→종가 일간 기울기 {slope(xs, ys):.3f} (일수 {len(xs)})")

        nights = []   # (d, n, rE, gI, c_close, 원자료)
        for a, b in zip(rows, rows[1:]):
            d, n = a[0], b[0]
            if weekday(n) == 0 or d not in idx or n not in idx:
                continue
            if not (a[2] > 0 and b[1] > 0 and b[2] > 0):
                continue
            rE = b[1] / a[2] - 1
            if abs(rE) >= CA_ABS:
                continue
            gI = idx[n][0] / idx[d][1] - 1
            cc = (b[2] / a[2] - 1) - L * (idx[n][1] / idx[d][1] - 1)
            nights.append((d, n, rE, gI, cc, (a[2], b[1], idx[d][1], idx[n][0])))
        e = [x[2] - L * x[3] for x in nights]
        so, sc = stats(e), stats([x[4] for x in nights])
        log(f"[밤] {len(nights)}개")
        log(f"시가 차이 e(밤)  : 평균 {so['mean']:+.4%} · 중앙 {so['p50']:+.4%} · 표준편차 {so['sd']:.4%} · "
            f"1%~99% [{so['p01']:+.3%}, {so['p99']:+.3%}] · |e|>1% {so['gt1']}밤 · >3% {so['gt3']}밤")
        log(f"종가 차이 c(비교): 평균 {sc['mean']:+.4%} · 중앙 {sc['p50']:+.4%} · 표준편차 {sc['sd']:.4%} · "
            f"1%~99% [{sc['p01']:+.3%}, {sc['p99']:+.3%}] · |c|>1% {sc['gt1']}밤 · >3% {sc['gt3']}밤")

        log("연도 | ETF 밤 | L×지수 밤 | 차이 | 밤 수")
        years = sorted({int(x[0][:4]) for x in nights})
        for y in years:
            sel = [x for x in nights if int(x[0][:4]) == y]
            a1 = (math.prod(1 + x[2] - COST for x in sel) - 1) * 100
            a2 = (math.prod(1 + L * x[3] - COST for x in sel) - 1) * 100
            log(f"{y} | {a1:+8.2f}% | {a2:+8.2f}% | {a1 - a2:+7.2f}%p | {len(sel)}")
        sel = [x for x in nights if 2016 <= int(x[0][:4]) <= 2024]
        c1 = cagr([x[2] - COST for x in sel], 9)
        c2 = cagr([L * x[3] - COST for x in sel], 9)
        big = abs(c1 - c2) > LIMIT
        verdict[nm] = (c1, c2, big)
        log(f"2016~2024 CAGR: ETF 밤 {c1:+.2f}% · L×지수 밤 {c2:+.2f}% · 차이 {c1 - c2:+.2f}%p → "
            f"{'시가 잡음 영향 큼' if big else '시가 잡음 영향 작음'}")
        log("|e| 큰 10개 (매수일→매도일 · ETF 종가→시가 · 지수 종가→시가 · e):")
        for x in sorted(nights, key=lambda x: abs(x[2] - L * x[3]), reverse=True)[:10]:
            c0, o1, i0, i1 = x[5]
            log(f"  {x[0]}→{x[1]} · {c0:.0f}→{o1:.0f} {x[2]:+.2%} · {i0:.2f}→{i1:.2f} {x[3]:+.2%} · e {x[2] - L * x[3]:+.2%}")

    log("\n=== 판정 (실행 전 고정: 2016~2024 CAGR 차이 |ETF 밤 − L×지수 밤| ≤ 1.0%p면 작음) ===")
    for nm, (c1, c2, big) in verdict.items():
        log(f"{nm}: ETF {c1:+.2f}% · L×지수 {c2:+.2f}% · 차이 {c1 - c2:+.2f}%p → {'큼' if big else '작음'}")


if __name__ == "__main__":
    main()
