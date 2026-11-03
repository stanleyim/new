"""
verify_lev_night.py — ① 레버리지 ETF 밤사이 독립 재계산 (읽기 전용, 레포에 쓰지 않음, 주문 없음)

대상: KODEX 레버리지(122630) = C, TIGER 레버리지(123320) = D.
계산 함수는 verify_kodex_night.py(11-03 독립 재계산 '일치' 판정)의 것을 그대로 씀. 기존 셀(e916bc7d4729·new5_etf_max_cell.py)과 코드 공유 없음.
규칙(기록과 같게): 매수일 종가 → 다음 거래일 시가, 다음 거래일 월요일이면 쉼, 비용 왕복 0.006%, |밤| ≥ 30% 제외,
  연도 귀속은 매수일 기준(11-03 KODEX 200 재계산으로 확인), 세금 미반영.

판정(실행 전 고정): 소수 첫째 자리 반올림 값 차이 0.1%p 이하면 O.
  대상 = 연도별 2016~2025(10-29 표 C·D) · 2015~2024 CAGR·MDD·플러스 연도(10-27 lev 셀) · 2016~2024 CAGR·MDD·플러스 연도(10-29 표).
  모두 O면 그 ETF "일치". 2026·Sharpe는 참고.
진단(판정과 무관, 출력만): C와 D의 연도별 밤 수익 차이, 두 ETF 같은 밤 차이가 큰 10개 원자료,
  연도별 들고 있기(종가→종가) — 밤 차이가 시가에서 오는지 보기 위함.
시험용 환경변수: VL_FAKE_DIR=폴더(그 안의 <코드>.csv: 날짜,시가,종가,기초지수) → KRX 대신 사용.
"""
import os
import sys
import time
import datetime as dt

from verify_kodex_night import nights_of, yearly, period, holding, ok, log

ETFS = {"122630": "C KODEX 레버리지", "123320": "D TIGER 레버리지"}
FROM_YEAR = 2014
REC_YEAR = {
    "122630": {2016: 11.2, 2017: 26.5, 2018: -16.3, 2019: 5.9, 2020: 54.0, 2021: 20.0,
               2022: -4.2, 2023: 28.3, 2024: -3.8, 2025: 89.2},
    "123320": {2016: 11.4, 2017: 23.5, 2018: 11.0, 2019: 12.7, 2020: 55.4, 2021: 22.1,
               2022: 5.9, 2023: 35.6, 2024: -8.8, 2025: 64.6},
}
REC_2026 = {"122630": 271.7, "123320": 341.6}
REC_P1524 = {"122630": {"CAGR": 11.7, "MDD": -38.4, "plus": (7, 10)},      # e916bc7d4729
             "123320": {"CAGR": 16.9, "MDD": -30.6, "plus": (9, 10)}}
REC_P1624 = {"122630": {"CAGR": 11.8, "MDD": -38.4, "plus": (6, 9), "Sharpe": 0.68},   # 10-29 표
             "123320": {"CAGR": 17.6, "MDD": -30.6, "plus": (8, 9), "Sharpe": 0.95}}


def load(ticker):
    fake = os.environ.get("VL_FAKE_DIR")
    if fake:
        out = []
        for ln in open(os.path.join(fake, f"{ticker}.csv"), encoding="utf-8").read().splitlines()[1:]:
            d, o, c, k = ln.split(",")
            out.append((d, float(o), float(c), float(k)))
        return out
    from pykrx import stock
    today = dt.date.today().strftime("%Y%m%d")
    out = []
    for y in range(FROM_YEAR, dt.date.today().year + 1):
        a, b = f"{y}0101", min(f"{y}1231", today)
        last = None
        for k in range(3):
            try:
                df = stock.get_etf_ohlcv_by_date(a, b, ticker)
                break
            except Exception as e:
                last = e
                time.sleep(3 + 3 * k)
        else:
            sys.exit(f"KRX 조회 실패 {ticker} {y}: {type(last).__name__}: {str(last)[:150]}")
        for idx, r in df.iterrows():
            out.append((idx.strftime("%Y%m%d"), float(r["시가"]), float(r["종가"]), float(r["기초지수"])))
        time.sleep(1)
    return sorted(set(out))


def main():
    data, nights, verdict = {}, {}, {}
    for t, nm in ETFS.items():
        rows = load(t)
        data[t] = rows
        ns, mon, bad, ca = nights_of(rows)
        nights[t] = ns
        log(f"\n##### {nm} ({t}) #####")
        log(f"[자료] {rows[0][0]} ~ {rows[-1][0]} · {len(rows)}거래일 · 처음 {rows[0]} · 마지막 {rows[-1]}")
        log(f"[밤] 계산 {len(ns)} · 월요일 쉼 {mon} · 0원 자료로 뺀 밤 {bad} · |밤|≥30% 제외 {len(ca)} {ca[:3]}")
        yr = yearly(ns, 0)
        good = True
        log("연도 | 재계산 | 기록 | 일치")
        for y, v in yr.items():
            rec = REC_YEAR[t].get(y)
            mark = "" if rec is None else ("O" if ok(v, rec) else "X")
            good = good and (rec is None or ok(v, rec))
            log(f"{y} | {v:+8.2f}% | {'' if rec is None else f'{rec:+.1f}%'} | {mark}")
        if any(y not in yr for y in REC_YEAR[t]):
            good = False
            log(f"기록 연도 중 자료 없음: {[y for y in REC_YEAR[t] if y not in yr]}")
        if 2026 in yr:
            log(f"(참고) 2026 재계산 {yr[2026]:+.2f}% vs 기록 {REC_2026[t]:+.1f}% — 자료 끝 날짜가 달라 다를 수 있음")
        for (y0, y1), rec in [((2015, 2024), REC_P1524[t]), ((2016, 2024), REC_P1624[t])]:
            p = period(ns, 0, y0, y1)
            c_ok, m_ok, pl_ok = ok(p["CAGR"], rec["CAGR"]), ok(p["MDD"], rec["MDD"]), p["plus"] == rec["plus"]
            good = good and c_ok and m_ok and pl_ok
            sh = f" (참고, 기록 {rec['Sharpe']:.2f})" if "Sharpe" in rec else " (참고)"
            log(f"{y0}~{y1}: 밤 {p['n']} · CAGR {p['CAGR']:+.2f}% (기록 {rec['CAGR']:+.1f}% {'O' if c_ok else 'X'}) · "
                f"MDD {p['MDD']:.2f}% (기록 {rec['MDD']:.1f}% {'O' if m_ok else 'X'}) · "
                f"플러스 {p['plus'][0]}/{p['plus'][1]} (기록 {rec['plus'][0]}/{rec['plus'][1]} {'O' if pl_ok else 'X'}) · "
                f"Sharpe {p['Sharpe']:.2f}{sh}")
        verdict[nm] = good

    # ---- 진단: C와 D (판정과 무관) ----
    c, d = "122630", "123320"
    yc, yd = yearly(nights[c], 0), yearly(nights[d], 0)
    hc, hd = holding(data[c], 2), holding(data[d], 2)
    hi = holding(data[c], 3)
    log("\n=== 진단: C와 D 연도별 (밤 순수익 · 들고 있기 종가→종가 · 기초지수) ===")
    log("연도 | 밤 C | 밤 D | 밤 D−C | 들고있기 C | 들고있기 D | 들고있기 D−C | 기초지수(C 자료)")
    for y in sorted(set(yc) & set(yd)):
        log(f"{y} | {yc[y]:+7.2f}% | {yd[y]:+7.2f}% | {yd[y] - yc[y]:+7.2f}%p | "
            f"{hc.get(y, float('nan')):+7.2f}% | {hd.get(y, float('nan')):+7.2f}% | "
            f"{hd.get(y, float('nan')) - hc.get(y, float('nan')):+7.2f}%p | {hi.get(y, float('nan')):+7.2f}%")
    rc = {(a, b): r for a, b, r in nights[c]}
    both = [(a, b, rc[(a, b)], r) for a, b, r in nights[d] if (a, b) in rc]
    log(f"같은 밤 {len(both)}개 · 차이(D−C) 평균 {sum(x[3] - x[2] for x in both) / len(both):+.4%}/밤")
    rowc = {r[0]: r for r in data[c]}
    rowd = {r[0]: r for r in data[d]}
    log("같은 밤 차이가 큰 10개 (매수일→매도일 · C 종가→시가 · D 종가→시가):")
    for a, b, r1, r2 in sorted(both, key=lambda x: abs(x[3] - x[2]), reverse=True)[:10]:
        log(f"  {a}→{b} · C {rowc[a][2]:.0f}→{rowc[b][1]:.0f} {r1:+.2%} · D {rowd[a][2]:.0f}→{rowd[b][1]:.0f} {r2:+.2%} · 차이 {r2 - r1:+.2%}p")

    log("\n=== 판정 (실행 전 고정 기준) ===")
    for nm, g in verdict.items():
        log(f"{nm}: {'일치' if g else '불일치'}")
    log(f"■ 종합: {'일치 — 기존 셀의 레버리지 밤 계산을 독립 계산이 재현' if all(verdict.values()) else '불일치 — X 줄부터 원인 확인'}")


if __name__ == "__main__":
    main()
