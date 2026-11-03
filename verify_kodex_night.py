"""
verify_kodex_night.py — KODEX 200(069500) 밤사이 독립 재계산 (읽기 전용, 레포에 쓰지 않음, 주문 없음)

목적: 기존 셀(b415db454c16, new5_etf_max_cell.py 등)과 코드를 공유하지 않는 단순 계산으로
      KRX 원자료에서 연도별 밤 수익을 다시 구해 기록값과 맞춰 본다. 들고 있기 연도 수익도 함께 출력(외부 대조용).

규칙(기록과 같게): 매수일 d 종가(15:30 종가 동시호가) → 다음 거래일 n 시가(09:00 시가 동시호가).
  n이 월요일이면 그 밤은 쉼. 비용 왕복 0.006%. |밤 수익| ≥ 30% 제외. 거래일 = KODEX 200 일봉이 있는 날.
  시가 또는 종가가 0인 날이 끼인 밤은 계산하지 않고 개수를 출력.
연도 귀속: 매수일 기준(주) · 매도일 기준(보조) 두 가지를 모두 출력.

판정(실행 전 고정): 아래 [기록] 값과 비교해 소수 첫째 자리 반올림 값 차이가 모두 0.1%p 이하면 "일치".
  대상 = 연도별 2016~2025(10개) · 2015~2024 CAGR · MDD · 플러스 연도 수 · 들고 있기 2018·2022·2024.
  두 연도 귀속 중 하나라도 전부 맞으면 일치(어느 쪽인지 출력). 2026·Sharpe는 참고(자료 끝 날짜·정의가 다를 수 있음).
시험용 환경변수: VK_FAKE=파일경로(CSV: 날짜,시가,종가,기초지수) → KRX 대신 그 자료 사용.
"""
import os
import sys
import math
import time
import datetime as dt

TICKER = "069500"
COST = 0.00006
CA_ABS = 0.30
FROM_YEAR = 2014           # 2015년 들고 있기에 2014년 마지막 종가가 필요

REC_YEAR = {2016: 4.3, 2017: 13.7, 2018: -4.0, 2019: 3.1, 2020: 27.2, 2021: 15.8,
            2022: -0.1, 2023: 11.6, 2024: 0.8, 2025: 37.4}            # 10-29 A~F 표의 A
REC_2026 = 105.5                                                        # 참고
REC_P1524 = {"CAGR": 7.5, "MDD": -15.9, "plus": (8, 10), "Sharpe": 0.81}  # b415db454c16
REC_P1624 = {"CAGR": 7.7, "MDD": -15.9, "plus": (7, 9), "Sharpe": 0.80}   # 10-29 표 A
REC_HOLD = {2018: -18.9, 2022: -25.8, 2024: -11.4}                      # 4aa34da8a690
ANCHORS = ["20261027", "20261028", "20261102"]                         # etf_daily.csv 대조점


def log(*a):
    print(*a, flush=True)


def load():
    """[(YYYYMMDD, 시가, 종가, 기초지수)] 날짜 오름차순."""
    fake = os.environ.get("VK_FAKE")
    if fake:
        out = []
        for ln in open(fake, encoding="utf-8").read().splitlines()[1:]:
            d, o, c, k = ln.split(",")
            out.append((d, float(o), float(c), float(k)))
        log(f"[자료] 가짜 자료 {fake} · {len(out)}줄")
        return out
    from pykrx import stock
    today = dt.date.today().strftime("%Y%m%d")
    out = []
    for y in range(FROM_YEAR, dt.date.today().year + 1):
        a, b = f"{y}0101", min(f"{y}1231", today)
        last = None
        for k in range(3):
            try:
                df = stock.get_etf_ohlcv_by_date(a, b, TICKER)
                break
            except Exception as e:
                last = e
                time.sleep(3 + 3 * k)
        else:
            sys.exit(f"KRX 조회 실패 {y}: {type(last).__name__}: {str(last)[:150]}")
        n0 = len(out)
        for idx, r in df.iterrows():
            out.append((idx.strftime("%Y%m%d"), float(r["시가"]), float(r["종가"]), float(r["기초지수"])))
        log(f"[자료] {y}: {len(out) - n0}일")
        time.sleep(1)
    out = sorted(set(out))
    return out


def weekday(ds):
    return dt.date(int(ds[:4]), int(ds[4:6]), int(ds[6:])).weekday()


def nights_of(rows):
    """[(매수일, 매도일, 순수익)] + 개수 정보."""
    res, mon, bad, ca = [], 0, 0, []
    for i in range(len(rows) - 1):
        d, _, c, _ = rows[i]
        n, o, _, _ = rows[i + 1]
        if weekday(n) == 0:
            mon += 1
            continue
        if not (c > 0 and o > 0):
            bad += 1
            continue
        r = o / c - 1 - COST
        if abs(r) >= CA_ABS:
            ca.append((d, n, r))
            continue
        res.append((d, n, r))
    return res, mon, bad, ca


def yearly(ns, key):
    """key 0 = 매수일 연도, 1 = 매도일 연도 → {연도: 복리 순수익 %}"""
    acc = {}
    for x in ns:
        y = int(x[key][:4])
        acc[y] = acc.get(y, 1.0) * (1 + x[2])
    return {y: (v - 1) * 100 for y, v in sorted(acc.items())}


def period(ns, key, y0, y1):
    sel = [x[2] for x in ns if y0 <= int(x[key][:4]) <= y1]
    eq, peak, mdd = 1.0, 1.0, 0.0
    for r in sel:
        eq *= 1 + r
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1)
    yrs = y1 - y0 + 1
    yr = yearly([x for x in ns if y0 <= int(x[key][:4]) <= y1], key)
    m = sum(sel) / len(sel)
    sd = math.sqrt(sum((r - m) ** 2 for r in sel) / (len(sel) - 1))
    return {"n": len(sel), "CAGR": (eq ** (1 / yrs) - 1) * 100, "MDD": mdd * 100,
            "plus": (sum(1 for v in yr.values() if v > 0), len(yr)),
            "Sharpe": m / sd * math.sqrt(len(sel) / yrs)}


def holding(rows, col):
    """연도별 들고 있기: 전년 마지막 값 → 그해 마지막 값. col 2 = KODEX 종가, 3 = 기초지수(코스피200)."""
    last = {}
    for r in rows:
        last[int(r[0][:4])] = r[col]
    ys = sorted(last)
    return {y: (last[y] / last[y - 1] - 1) * 100 for y in ys[1:]}


def ok(v, rec):
    return abs(round(v, 1) - rec) <= 0.1 + 1e-9


def main():
    rows = load()
    log(f"[자료] {rows[0][0]} ~ {rows[-1][0]} · {len(rows)}거래일 · 처음 {rows[0]} · 마지막 {rows[-1]}")
    for a in ANCHORS:
        hit = [r for r in rows if r[0] == a]
        log(f"[대조점] {a}: {hit[0] if hit else '없음'}  (etf_daily.csv 기록: 10-27 종가 88905 · 10-28 시가 91315 · 11-02 종가 92945)")
    ns, mon, bad, ca = nights_of(rows)
    log(f"[밤] 계산 {len(ns)} · 월요일 쉼 {mon} · 0원 자료로 뺀 밤 {bad} · |밤|≥30% 제외 {len(ca)} {ca[:3]}")
    big = sorted(ns, key=lambda x: abs(x[2]), reverse=True)[:5]
    log("[밤] 절댓값 큰 5개(원자료 확인용): " + " / ".join(f"{d}→{n} {r:+.2%}" for d, n, r in big))

    verdict = {}
    for key, nm in [(0, "매수일 기준"), (1, "매도일 기준")]:
        yr = yearly(ns, key)
        log(f"\n=== 연도별 밤 순수익 ({nm}) ===")
        log("연도 | 재계산 | 기록 | 일치")
        good = True
        for y, v in yr.items():
            rec = REC_YEAR.get(y)
            mark = "" if rec is None else ("O" if ok(v, rec) else "X")
            if rec is not None and not ok(v, rec):
                good = False
            log(f"{y} | {v:+7.2f}% | {'' if rec is None else f'{rec:+.1f}%'} | {mark}")
        missing = [y for y in REC_YEAR if y not in yr]
        if missing:
            good = False
            log(f"기록 연도 중 자료 없음: {missing}")
        if 2026 in yr:
            log(f"(참고) 2026 재계산 {yr[2026]:+.2f}% vs 기록 {REC_2026:+.1f}% — 자료 끝 날짜가 달라 다를 수 있음")
        for (y0, y1), rec in [((2015, 2024), REC_P1524), ((2016, 2024), REC_P1624)]:
            p = period(ns, key, y0, y1)
            c_ok, m_ok, pl_ok = ok(p["CAGR"], rec["CAGR"]), ok(p["MDD"], rec["MDD"]), p["plus"] == rec["plus"]
            if (y0, y1) == (2015, 2024):
                good = good and c_ok and m_ok and pl_ok
            log(f"{y0}~{y1}: 밤 {p['n']} · CAGR {p['CAGR']:+.2f}% (기록 {rec['CAGR']:+.1f}% {'O' if c_ok else 'X'}) · "
                f"MDD {p['MDD']:.2f}% (기록 {rec['MDD']:.1f}% {'O' if m_ok else 'X'}) · "
                f"플러스 {p['plus'][0]}/{p['plus'][1]} (기록 {rec['plus'][0]}/{rec['plus'][1]} {'O' if pl_ok else 'X'}) · "
                f"Sharpe {p['Sharpe']:.2f} (참고, 기록 {rec['Sharpe']:.2f})")
        verdict[nm] = good

    hk, hi = holding(rows, 2), holding(rows, 3)
    log("\n=== 연도별 들고 있기 (전년 마지막 → 그해 마지막, 분배금 제외 가격) ===")
    log("연도 | KODEX 200 종가 | 코스피200 지수(기초지수) | 기록(KODEX)")
    h_ok = True
    for y in hk:
        rec = REC_HOLD.get(y)
        mark = "" if rec is None else ("O" if ok(hk[y], rec) else "X")
        if rec is not None and not ok(hk[y], rec):
            h_ok = False
        log(f"{y} | {hk[y]:+7.2f}% | {hi[y]:+7.2f}% | {'' if rec is None else f'{rec:+.1f}% {mark}'}")

    log("\n=== 판정 (실행 전 고정 기준) ===")
    for nm, g in verdict.items():
        log(f"밤 수익 {nm}: {'일치' if g else '불일치'}")
    log(f"들고 있기 2018·2022·2024: {'일치' if h_ok else '불일치'}")
    final = any(verdict.values()) and h_ok
    log(f"■ 종합: {'일치 — 기존 셀의 KODEX 200 밤 계산을 독립 계산이 재현' if final else '불일치 — 위 X 줄부터 원인 확인'}")


if __name__ == "__main__":
    main()
