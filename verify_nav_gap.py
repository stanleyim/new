"""
verify_nav_gap.py — ETF 종가 괴리(종가÷NAV−1)로 밤 수익을 미리 알 수 있나 (읽기 전용, 레포에 쓰지 않음, 주문 없음)

목적: KODEX 200(069500)·ACE 200(105190) 밤사이에서, 15:30 매수 전에 알 수 있는 괴리 값(S1)이
      그 밤 수익을 가르는지, 그리고 매일 둘 중 하나를 고르면 더 나은지 확인한다.

밤 규칙(etf_daily.csv·overnight_paper.py와 같음): 매수일 d 종가(15:30 종가 동시호가) → 다음 거래일 n 시가
  (09:00 시가 동시호가). n이 월요일이면 쉼. 비용 왕복 0.006%. |밤| ≥ 30% 제외. 거래일 = KODEX 200 일봉이 있는 날.
  두 ETF 모두 자료가 있는 밤만 씀(같은 밤끼리 비교).
신호:
  S0 = d 종가 ÷ d NAV − 1  → d 동시호가가 끝나야 알 수 있음 = 미래 자료. 상한선 참고용, 통과 판정에 안 씀.
  S1 = p 종가 ÷ p NAV − 1  (p = d 바로 전 거래일) → d 15:30 전에 앎. 판정용.
기간(매수일 기준): 판정 2025-01-01~끝, 참고 2016-01-01~2024-12-31.

판정(실행 전 고정, 11-07 Stanley 승인):
  0) NAV 시점 확인: NAV가 그날 종가 기준인지(전날 기준이면 S0·S1 뜻이 바뀜 → 판정 보류).
  ① ETF별로 S1 하위 20% 밤 평균 − 상위 20% 밤 평균 > 0, 판정·참고 두 기간 모두. 두 ETF 모두 맞아야 통과.
  ② 매일 S1이 낮은 쪽(할인 큰 쪽)을 사는 방식(같으면 반반)의 판정 기간 밤 평균 ≥ +0.133%
     그리고 ACE 200만 사는 것보다 높음.
  종합: 0)·①·② 모두 → "S1 통과". 아니고 S0가 ① 조건을 만족 → "S1 불통과 · S0만 강함 → 실시간 S2 수집 검토".
        둘 다 아니면 → "B-1 접음".
참고(판정 아님): ETF 밤 − 코스피200 지수 밤(지수 시가÷전날 지수 종가 − 1)과 S0의 상관.
시험용 환경변수: NG_FAKE=CSV(date,ticker,시가,종가,NAV,기초지수), NG_FAKE_IDX=CSV(date,시가,종가).
"""
import os
import sys
import math
import time
import datetime as dt

TICKERS = {"069500": "KODEX 200", "105190": "ACE 200"}
KO, AC = "069500", "105190"
COST = 0.00006
CA_ABS = 0.30
FROM_YEAR = 2015
MAIN = ("20250101", "20991231")
REF = ("20160101", "20241231")
TARGET = 1.30 ** (1 / 197) - 1      # +0.133%
Q = 0.2
ANCHOR = ("20261105", "20261106")   # 기록: 11-05 종가 KODEX 84,610 · ACE 84,780 / 밤 −8.92% · −8.00%


def log(*a):
    print(*a, flush=True)


def retry(fn, what):
    last = None
    for k in range(3):
        try:
            return fn()
        except Exception as e:
            last = e
            time.sleep(3 + 3 * k)
    sys.exit(f"KRX 조회 실패 {what}: {type(last).__name__}: {str(last)[:150]}")


def years():
    today = dt.date.today().strftime("%Y%m%d")
    for y in range(FROM_YEAR, dt.date.today().year + 1):
        yield y, f"{y}0101", min(f"{y}1231", today)


def fetch_etf(t):
    """{날짜: (시가, 종가, NAV, 기초지수)}"""
    fake = os.environ.get("NG_FAKE")
    out = {}
    if fake:
        for ln in open(fake, encoding="utf-8").read().splitlines()[1:]:
            d, tk, o, c, nav, k = ln.split(",")
            if tk == t:
                out[d] = (float(o), float(c), float(nav), float(k))
        log(f"[자료] 가짜 {TICKERS[t]} {len(out)}일")
        return out
    from pykrx import stock
    shown = False
    for y, a, b in years():
        df = retry(lambda: stock.get_etf_ohlcv_by_date(a, b, t), f"{t} {y}")
        if not shown:
            log(f"[열] {TICKERS[t]}: {list(df.columns)}")
            shown = True
            if "NAV" not in df.columns:
                sys.exit(f"[중단] {TICKERS[t]} 일봉에 NAV 열 없음 → 설계 다시")
        for idx, r in df.iterrows():
            out[idx.strftime("%Y%m%d")] = (float(r["시가"]), float(r["종가"]), float(r["NAV"]), float(r["기초지수"]))
        log(f"[자료] {TICKERS[t]} {y}: {len(df)}일")
        time.sleep(1)
    return out


def fetch_index():
    """{날짜: (지수 시가, 지수 종가)} 코스피200(1028). 참고용 — 실패해도 판정은 진행."""
    fake = os.environ.get("NG_FAKE_IDX")
    out = {}
    if fake:
        for ln in open(fake, encoding="utf-8").read().splitlines()[1:]:
            d, o, c = ln.split(",")
            out[d] = (float(o), float(c))
        return out
    try:
        from pykrx import stock
        for y, a, b in years():
            df = retry(lambda: stock.get_index_ohlcv_by_date(a, b, "1028"), f"지수 {y}")
            for idx, r in df.iterrows():
                out[idx.strftime("%Y%m%d")] = (float(r["시가"]), float(r["종가"]))
            time.sleep(1)
    except (SystemExit, Exception) as e:
        log(f"[참고] 지수 조회 실패 → 분해 참고 생략 ({e})")
        return {}
    return out


def weekday(ds):
    return dt.date(int(ds[:4]), int(ds[4:6]), int(ds[6:])).weekday()


def build(etf):
    """[{d, n, 069500: (밤, S0, S1), 105190: (...)}] — 두 ETF 모두 유효한 밤만."""
    cal = sorted(etf[KO])
    rows, cnt = [], {"월요일 쉼": 0, "자료 없음": 0, "0원": 0, "|밤|≥30%": 0}
    for i in range(1, len(cal) - 1):
        p, d, n = cal[i - 1], cal[i], cal[i + 1]
        if weekday(n) == 0:
            cnt["월요일 쉼"] += 1
            continue
        rec, why = {"d": d, "n": n}, None
        for t in TICKERS:
            s = etf[t]
            if not all(x in s for x in (p, d, n)):
                why = "자료 없음"
                break
            op, cl, navd, clp, navp = s[n][0], s[d][1], s[d][2], s[p][1], s[p][2]
            if not (op > 0 and cl > 0 and navd > 0 and clp > 0 and navp > 0):
                why = "0원"
                break
            r = op / cl - 1 - COST
            if abs(r) >= CA_ABS:
                why = "|밤|≥30%"
                break
            rec[t] = (r, cl / navd - 1, clp / navp - 1)
        if why:
            cnt[why] += 1
        else:
            rows.append(rec)
    return rows, cnt


def sel(rows, per):
    return [r for r in rows if per[0] <= r["d"] <= per[1]]


def mean(v):
    return sum(v) / len(v) if v else float("nan")


def corr(x, y):
    if len(x) < 3:
        return float("nan")
    mx, my = mean(x), mean(y)
    sx = math.sqrt(sum((a - mx) ** 2 for a in x))
    sy = math.sqrt(sum((b - my) ** 2 for b in y))
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / (sx * sy) if sx > 0 and sy > 0 else float("nan")


def quint(rs, t, k_sig):
    """신호 오름차순 5등분. 반환 (하위20% 평균, 상위20% 평균, 차이, [5칸 평균], 칸 크기)."""
    v = sorted(((r[t][k_sig], r[t][0]) for r in rs), key=lambda x: x[0])
    k = int(len(v) * Q)
    if k < 1:
        return float("nan"), float("nan"), float("nan"), [], 0
    b = [mean([x[1] for x in v[int(len(v) * i / 5):int(len(v) * (i + 1) / 5)]]) for i in range(5)]
    lo, hi = mean([x[1] for x in v[:k]]), mean([x[1] for x in v[-k:]])
    return lo, hi, lo - hi, b, k


def pick(rs, k_sig):
    out = []
    for r in rs:
        a, c = r[KO], r[AC]
        out.append(a[0] if a[k_sig] < c[k_sig] else c[0] if c[k_sig] < a[k_sig] else (a[0] + c[0]) / 2)
    return out


def perf(v):
    eq, pk, mdd = 1.0, 1.0, 0.0
    for r in v:
        eq *= 1 + r
        pk = max(pk, eq)
        mdd = min(mdd, eq / pk - 1)
    m = mean(v)
    return f"밤 {len(v)} · 평균 {m:+.3%} · 연환산 {(1 + m) ** 197 - 1:+.1%} · 누적 {eq - 1:+.1%} · MDD {mdd:.1%}"


def pct(v):
    return f"{v * 100:+.3f}%"


def nav_timing(s):
    """NAV÷기초지수 하루 변화 중앙값: 같은 날 기준(a0) vs 전날 지수 기준(a1). a0가 작아야 NAV = 그날 종가 기준."""
    ds = sorted(d for d in s if s[d][2] > 0 and s[d][3] > 0)
    a0, a1 = [], []
    for i in range(2, len(ds)):
        x2, x1, x0 = s[ds[i - 2]], s[ds[i - 1]], s[ds[i]]
        a0.append(abs(math.log(x0[2] / x0[3]) - math.log(x1[2] / x1[3])))
        a1.append(abs(math.log(x0[2] / x1[3]) - math.log(x1[2] / x2[3])))
    med = lambda v: sorted(v)[len(v) // 2] if v else float("nan")
    return med(a0), med(a1)


def main():
    etf = {t: fetch_etf(t) for t in TICKERS}
    for t, nm in TICKERS.items():
        ds = sorted(etf[t])
        log(f"[자료] {nm} {ds[0]}~{ds[-1]} · {len(ds)}일")
    d0, d1 = ANCHOR
    for t, nm in TICKERS.items():
        s = etf[t]
        if d0 in s and d1 in s:
            log(f"[대조점] {nm} {d0} 종가 {s[d0][1]:,.0f} · NAV {s[d0][2]:,.2f} · {d0}→{d1} 밤 {s[d1][0] / s[d0][1] - 1 - COST:+.2%}")
        else:
            log(f"[대조점] {nm} {d0}/{d1} 자료 없음")
    log("        (기록: 11-05 종가 KODEX 84,610 · ACE 84,780 / 11-05→11-06 KODEX −8.92% · ACE −8.00%)")

    log("\n=== 0) NAV 시점 확인 ===")
    timing_ok = True
    for t, nm in TICKERS.items():
        a0, a1 = nav_timing(etf[t])
        ok = a0 < a1
        timing_ok = timing_ok and ok
        log(f"{nm}: 같은 날 지수 기준 {a0:.5f} · 전날 지수 기준 {a1:.5f} → {'그날 종가 기준 O' if ok else '전날 기준 의심 X'}")

    rows, cnt = build(etf)
    log(f"\n[밤] 쓰는 밤 {len(rows)} · " + " · ".join(f"{k} {v}" for k, v in cnt.items()))
    per = {"판정 2025~": sel(rows, MAIN), "참고 2016~2024": sel(rows, REF)}
    for pn, rs in per.items():
        if rs:
            log(f"  {pn}: {len(rs)}밤 ({rs[0]['d']}→{rs[0]['n']} ~ {rs[-1]['d']}→{rs[-1]['n']})")

    for t, nm in TICKERS.items():
        s1 = sorted(r[t][2] for r in per["판정 2025~"])
        if s1:
            q = lambda f: s1[min(len(s1) - 1, int(len(s1) * f))]
            log(f"[S1 분포 판정 기간] {nm}: 5% {pct(q(.05))} · 중앙 {pct(q(.5))} · 95% {pct(q(.95))}")

    res = {}
    for k_sig, sn in [(2, "S1 (판정용, 15:30 전에 앎)"), (1, "S0 (참고, 미래 자료)")]:
        log(f"\n=== ① {sn}: 신호 낮은 20% − 높은 20% 밤 평균 ===")
        allok = True
        for t, nm in TICKERS.items():
            for pn, rs in per.items():
                lo, hi, df_, b, k = quint(rs, t, k_sig)
                ok = df_ > 0
                allok = allok and ok
                log(f"{nm} {pn}: 낮은20% {pct(lo)} − 높은20% {pct(hi)} = {pct(df_)} {'O' if ok else 'X'} "
                    f"(칸 {k}밤) · 5칸 {' / '.join(pct(x) for x in b)} · 상관 "
                    f"{corr([r[t][k_sig] for r in rs], [r[t][0] for r in rs]):+.3f}")
        res[k_sig] = allok

    log("\n=== ② 매일 신호 낮은 쪽 사기 (같으면 반반) ===")
    sw_ok = False
    for pn, rs in per.items():
        if not rs:
            continue
        log(f"[{pn}]")
        log(f"  KODEX 200만 : {perf([r[KO][0] for r in rs])}")
        log(f"  ACE 200만   : {perf([r[AC][0] for r in rs])}")
        log(f"  S1로 고르기 : {perf(pick(rs, 2))}")
        log(f"  S0로 고르기 : {perf(pick(rs, 1))}  (참고, 미래 자료)")
        n_ko = sum(1 for r in rs if r[KO][2] < r[AC][2])
        n_ac = sum(1 for r in rs if r[AC][2] < r[KO][2])
        log(f"  S1 선택 횟수: KODEX {n_ko} · ACE {n_ac} · 반반 {len(rs) - n_ko - n_ac}")
        if pn == "판정 2025~":
            m_sw, m_ac = mean(pick(rs, 2)), mean([r[AC][0] for r in rs])
            sw_ok = m_sw >= TARGET and m_sw > m_ac
            log(f"  판정: S1 평균 {pct(m_sw)} ≥ {pct(TARGET)} {'O' if m_sw >= TARGET else 'X'} · "
                f"ACE만 {pct(m_ac)}보다 높음 {'O' if m_sw > m_ac else 'X'}")

    idx = fetch_index()
    if idx:
        log("\n=== 참고: ETF 밤 − 지수 밤 (괴리 변화 몫) 판정 기간 ===")
        rs = [r for r in per["판정 2025~"] if r["d"] in idx and r["n"] in idx and idx[r["d"]][1] > 0 and idx[r["n"]][0] > 0]
        same = sum(1 for r in rs if abs(idx[r["d"]][1] - etf[KO][r["d"]][3]) < 0.011)
        log(f"밤 {len(rs)} · 지수 종가 = KODEX 기초지수 일치 {same}/{len(rs)}")
        for t, nm in TICKERS.items():
            gap = [r[t][0] + COST - (idx[r["n"]][0] / idx[r["d"]][1] - 1) for r in rs]
            log(f"{nm}: 평균 {pct(mean(gap))} · S0와 상관 {corr([r[t][1] for r in rs], gap):+.3f} · "
                f"S1과 상관 {corr([r[t][2] for r in rs], gap):+.3f}")

    log("\n=== 판정 (실행 전 고정 기준) ===")
    log(f"0) NAV 시점: {'O' if timing_ok else 'X'} · ① S1: {'O' if res[2] else 'X'} · ② S1 고르기: {'O' if sw_ok else 'X'} · "
        f"(참고) ① S0: {'O' if res[1] else 'X'}")
    if not timing_ok:
        log("■ 종합: 판정 보류 — NAV가 그날 종가 기준이 아님, S0·S1 정의부터 다시")
    elif res[2] and sw_ok:
        log("■ 종합: S1 통과 — 15:30 전에 알 수 있는 값으로 쓸 수 있음")
    elif res[1]:
        log("■ 종합: S1 불통과 · S0만 강함 → 실시간 S2(15:29 예상체결가÷iNAV) 수집 검토")
    else:
        log("■ 종합: B-1 접음")


if __name__ == "__main__":
    main()
