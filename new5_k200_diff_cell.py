# new5_k200_diff_cell.py — 읽기 전용, KRX 조회 없음 (Drive new5_etf_breadth/ 저장분만)
# 목적: 같은 코스피200을 따르는 ETF 5개(거래 많은 것)의 밤사이 수익이 왜 다른지 원인 진단. ETF 고르기 아님.
# 방법(로그 수익, 2015~2024 전 거래일, 같은 날짜만 비교):
#   밤 = ln(다음날 시가 / 오늘 종가), 낮 = ln(종가 / 시가), 하루 = ln(다음날 종가 / 오늘 종가) = 밤 + 다음날 낮
#   ① 하루(종가→종가)는 같은 지수라 거의 같아야 함. 하루가 같은데 밤이 다르면 → 차이는 '시가 가격'에서 생김
#   ② 분배락 추정: ln(NAV 다음날/오늘) − ln(기초지수 다음날/오늘) < −0.2% 인 밤 → 그 밤들이 차이를 만드는지
#   ③ 집중도: KODEX 200 대비 밤 차이의 합 중 상위 10일이 차지하는 비율 → 며칠에 몰렸는지, 매일 쌓였는지
import os
import numpy as np, pandas as pd

P = "/content/drive/MyDrive/new5_etf_breadth"
if not os.path.exists(P):
    from google.colab import drive
    drive.mount("/content/drive")
ETF = {"069500": "KODEX 200", "102110": "TIGER 200", "148020": "RISE 200", "105190": "ACE 200", "152100": "PLUS 200"}
BASE = "069500"

comp, flag = {}, {}
for t in ETF:
    d = pd.read_parquet(f"{P}/{t}.parquet")
    d = d[(d["시가"] > 0) & (d["종가"] > 0)]
    o, c, nav, ix = np.log(d["시가"]), np.log(d["종가"]), np.log(d["NAV"].where(d["NAV"] > 0)), np.log(d["기초지수"].where(d["기초지수"] > 0))
    comp[t] = pd.DataFrame({"밤": o.shift(-1) - c, "낮": c - o, "하루": c.shift(-1) - c})
    flag[t] = ((nav.shift(-1) - nav) - (ix.shift(-1) - ix)) < -0.002
common = None
for t in ETF:
    idx = comp[t].dropna().index
    idx = idx[(idx.year >= 2015) & (idx.year <= 2024)]
    common = idx if common is None else common.intersection(idx)
yrs = (common[-1] - common[0]).days / 365.25
print(f"[0] 같은 날짜 {len(common)}일 {common[0].date()}~{common[-1].date()}")

print("\n[1] 연평균(로그 합 ÷ 연수, %) — 하루 = 밤 + 낮(다음날)")
for t, nm in ETF.items():
    x = comp[t].loc[common]
    print(f"  {t} {nm:10s} 밤 {x['밤'].sum() / yrs * 100:+6.2f} · 낮 {x['낮'].sum() / yrs * 100:+6.2f} · 하루 {x['하루'].sum() / yrs * 100:+6.2f} · 분배락 추정 밤 {int(flag[t].loc[common].sum())}일")

print(f"\n[2] {ETF[BASE]} 대비 차이(연평균 %p) — 연도별 밤 차이")
b = comp[BASE].loc[common]
for t, nm in ETF.items():
    if t == BASE:
        continue
    x = comp[t].loc[common]
    dn, dd = x["밤"] - b["밤"], x["하루"] - b["하루"]
    fl = flag[t].loc[common] | flag[BASE].loc[common]
    top = dn.reindex(dn.abs().sort_values(ascending=False).index[:10])
    share = f"({top.sum() / dn.sum() * 100:.0f}%)" if abs(dn.sum()) > 1e-6 else "(차이 없음)"
    print(f"■ {nm}: 밤 {dn.sum() / yrs * 100:+.2f}%p · 하루 {dd.sum() / yrs * 100:+.2f}%p · "
          f"분배락 추정 밤 빼면 밤 {dn[~fl].sum() / yrs * 100:+.2f}%p · 상위 10일 합 {top.sum() / yrs * 100:+.2f}%p/년{share}")
    yr = dn.groupby(dn.index.year).sum() * 100
    print("   연도별 밤 차이 %p: " + " · ".join(f"{y} {v:+.1f}" for y, v in yr.items()))
    print("   밤 차이 큰 10일: " + " · ".join(f"{dt.date()} {v * 100:+.2f}%p{'(분배)' if fl.loc[dt] else ''}" for dt, v in top.items()))

print("\n해석 기준: 하루 차이 ≈ 0 인데 밤 차이가 크고 매년 같은 방향 → 시가 동시호가 가격 차이(구조).")
print("          밤 차이가 분배락 밤·상위 몇 일에 몰림 → 자료·분배 처리 착시.")
print("끝. 출력 전체를 붙여 주세요.")
