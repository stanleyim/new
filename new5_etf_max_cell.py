# new5_etf_max_cell.py — 읽기 전용, KRX 조회 없음 (Drive new5_etf_breadth/ · new5_lev/ 저장분만, 레포·운용 변경 없음)
# 목적: ETF 밤사이 CAGR을 가장 크게 하는 후보를 같은 조건으로 비교. 앞 기간에서 고르고 뒤 기간에서 확인.
# 공통(모든 후보 동일): 15:30 종가 매수 → 다음 거래일 09:00 시가 매도, 다음 거래일 월요일이면 쉼,
#   비용 왕복 0.006%, |밤| ≥ 30% 제외, 고를 때는 전날까지 값만. 레버리지 세금은 넣지 않음(lev 셀 [세금0]과 같음).
# 후보(사전 고정):
#   A KODEX 200(기준) · B ACE 200 · C KODEX 레버리지 · D TIGER 레버리지
#   E 업종 갈아타기 상위5 (new5_etf_rotate_liq_cell.py와 같은 규칙: 매달 첫 거래일, 직전 252밤 순수익 로그합 상위5,
#     직전 60거래일 거래대금 중앙값이 후보 중 상위 절반인 것만, 그달 같은 비중) — 후보군 56개(레버리지·인버스 제외)
#   F E와 같은 규칙, 후보군 = 56개 + 레버리지 2종(C·D)
# 판정(사전 고정): 2016~2020 CAGR 1위 후보가 ① A가 아니고 ② 2021~2024 CAGR이 A보다 높고 ③ 2016~2024 플러스 연도 ≥ 7/9 → 통과.
#   7/9는 계산 근거 없음(①의 7/10을 빌림). MDD·Sharpe는 보고만. 2025~2026은 참고.
# 점검: E 2016~2024 CAGR = +24.4%(rotate_liq 결과), A·C·D 2015~2024 = +7.5%·+11.7%·+16.9%(기록) 와 같아야 함.
import os, json, glob
import numpy as np, pandas as pd

P = os.environ.get("ETF_DIR", "/content/drive/MyDrive/new5_etf_breadth")
PL = os.environ.get("LEV_DIR", "/content/drive/MyDrive/new5_lev")
if not os.path.exists(P):
    from google.colab import drive
    drive.mount("/content/drive")
try:
    names = json.load(open(f"{P}/names_20150102.json", encoding="utf-8"))
except Exception:
    names = {}
COST, LOOK, TOP, LIQ = 0.00006, 252, 5, 60
BASE, ACE = "069500", "105190"
LEV = {"122630": "KODEX 레버리지", "123320": "TIGER 레버리지"}
nm = lambda t: LEV.get(t) or names.get(t, t)


def nights(d):
    d = d[(d["시가"] > 0) & (d["종가"] > 0)]
    on = d["시가"].shift(-1)
    r = (on / d["종가"] - 1).where(on > 0)
    return r[r.abs() < 0.30], d["거래대금"].astype(float)


# ---------- 1. 자료 (rotate_liq 셀과 같은 읽기) ----------
R, V = {}, {}
for f in sorted(glob.glob(f"{P}/*.parquet")):
    t = os.path.basename(f)[:-8]
    d = pd.read_parquet(f)
    if not {"시가", "종가", "거래대금"} <= set(d.columns) or d.index.min() > pd.Timestamp("2015-01-05"):
        continue
    R[t], V[t] = nights(d)
R = pd.DataFrame(R).sort_index()
R = R[R.index >= "2015-01-01"].dropna(how="all")
V = pd.DataFrame(V).reindex(R.index)
levinv = [t for t in R.columns if any(w in names.get(t, "") for w in ("레버리지", "인버스", "2X"))]
U = [t for t in R.columns if t not in levinv]
print(f"[1] 업종 후보군 {len(U)}개(레버리지·인버스 제외 {len(levinv)}, 기록 56과 같아야 함) · 밤 {len(R)} · "
      f"{R.index.min().date()}~{R.index.max().date()} · 이름 {len(names)}개")
for t in (BASE, ACE):
    print(f"[1] {t} {nm(t)}: {'있음' if t in U else '없음 ← 후보 제외'}")

for t in LEV:                                      # 레버리지는 lev 셀이 저장한 new5_lev/ 자료(그 셀 결과와 같은 원천)
    r, v = nights(pd.read_parquet(f"{PL}/{t}.parquet"))
    r, v = r[r.index >= "2015-01-01"], v[v.index >= "2015-01-01"]
    out = r.index.difference(R.index)
    if t in R.columns:
        c = pd.concat([r, R[t]], axis=1, join="inner").dropna()
        print(f"[1] {t} {LEV[t]}: new5_lev vs breadth 같은 밤 {len(c)}개 최대 차이 {(c.iloc[:, 0] - c.iloc[:, 1]).abs().max():.2e}")
    R[t], V[t] = r.reindex(R.index), v.reindex(R.index)
    print(f"[1] {t} {LEV[t]}: {r.index.min().date()}~{r.index.max().date()} · breadth 달력 밖 밤 {len(out)}개(버림)")

N = R - COST
dates = R.index.to_series()
mon = (pd.to_datetime(dates.shift(-1)).dt.weekday == 0).astype(bool)
score = np.log1p(N).rolling(LOOK, min_periods=int(LOOK * 0.9)).sum().shift(1)   # 그날 아침까지
liq = V.rolling(LIQ, min_periods=int(LIQ * 0.8)).median().shift(1)               # 그날 아침까지
first = dates.groupby([dates.index.year, dates.index.month]).transform("min") == dates


# ---------- 2. 갈아타기 (rotate_liq build와 같음, 후보군만 지정) ----------
def rotate(cols, k=TOP, q=0.5):
    sel = pd.DataFrame(False, index=R.index, columns=cols)
    picks, p = {}, None
    for dt in R.index:
        if first.loc[dt]:
            s = score.loc[dt, cols].dropna()
            l = liq.loc[dt, cols].dropna()
            s = s.reindex(l[l >= l.quantile(1 - q)].index).dropna()
            p = None
            if len(s) >= k:
                p = list(s.sort_values(ascending=False).index[:k])
                picks[dt] = p
        if p:
            sel.loc[dt, p] = True
    return N[cols].where(sel).mean(axis=1), picks


def summ(s, lo, hi):
    s = s[(s.index.year >= lo) & (s.index.year <= hi) & ~mon.reindex(s.index).fillna(False).astype(bool)].dropna()
    if len(s) < 2:
        return dict(cagr=np.nan, sharpe=np.nan, mdd=np.nan, plus=0, ny=0, yrs=pd.Series(dtype=float), n=len(s))
    span = (s.index[-1] - s.index[0]).days / 365.25
    eq = (1 + s).cumprod()
    yrs = s.groupby(s.index.year).apply(lambda v: (1 + v).prod() - 1)
    return dict(cagr=eq.iloc[-1] ** (1 / span) - 1, sharpe=s.mean() / s.std() * np.sqrt(len(s) / span),
                mdd=(eq / eq.cummax() - 1).min(), plus=int((yrs > 0).sum()), ny=len(yrs), yrs=yrs, n=len(s))


ser = {}
for lab, t in (("A KODEX 200", BASE), ("B ACE 200", ACE), ("C KODEX 레버리지", "122630"), ("D TIGER 레버리지", "123320")):
    if t in N.columns:
        ser[lab] = N[t]
sE, pE = rotate(U)
sF, pF = rotate(U + list(LEV))
ser["E 업종 상위5"], ser["F 업종+레버리지 상위5"] = sE, sF
A = "A KODEX 200"

# ---------- 3. 결과 ----------
for lab, lo, hi in (("2016~2020 (고르기)", 2016, 2020), ("2021~2024 (확인)", 2021, 2024),
                    ("2016~2024 (전체)", 2016, 2024), ("2025~2026 (참고)", 2025, 2026)):
    print(f"\n■ {lab}")
    ms = {k: summ(s, lo, hi) for k, s in ser.items()}
    for k in sorted(ms, key=lambda x: -ms[x]["cagr"]):
        m = ms[k]
        print(f"  {k:16s} CAGR {m['cagr'] * 100:+7.1f}% · Sharpe {m['sharpe']:.2f} · MDD {m['mdd'] * 100:6.1f}% · 플러스 {m['plus']}/{m['ny']} ({m['n']}밤)")

print("\n■ 연도별 순수익(정식 밤)")
ys = {k: summ(s, 2016, 2026)["yrs"] for k, s in ser.items()}
print("  연도 " + " ".join(f"{k.split()[0]:>8s}" for k in ys))
for y in sorted(ys[A].index):
    print(f"  {y} " + " ".join(f"{ys[k].get(y, np.nan) * 100:+7.1f}%" for k in ys))

lev_m = sum(any(t in LEV for t in p) for p in pF.values())
cnt = pd.Series([t for p in pF.values() for t in p]).value_counts()
print(f"\n■ F: 레버리지가 들어간 달 {lev_m}/{len(pF)}개월 · 자주 뽑힌 것: " + ", ".join(f"{nm(t)} {c}" for t, c in cnt.head(7).items()))
for dt in list(pF)[-3:]:
    print(f"  {dt.date()} F 고른 것: " + ", ".join(nm(t) for t in pF[dt]))

# ---------- 4. 점검 ----------
print("\n[점검] 기록과 같아야 함")
print(f"  E 2016~2024 CAGR {summ(sE, 2016, 2024)['cagr'] * 100:+.1f}% (기록 +24.4%)")
for k, rec in ((A, "+7.5%"), ("C KODEX 레버리지", "+11.7%"), ("D TIGER 레버리지", "+16.9%")):
    if k in ser:
        print(f"  {k} 2015~2024 CAGR {summ(ser[k], 2015, 2024)['cagr'] * 100:+.1f}% (기록 {rec})")

# ---------- 5. 판정 (사전 고정) ----------
m1 = {k: summ(s, 2016, 2020)["cagr"] for k, s in ser.items()}
best = max((k for k in m1 if pd.notna(m1[k])), key=m1.get)
b2, a2 = summ(ser[best], 2021, 2024)["cagr"], summ(ser[A], 2021, 2024)["cagr"]
pl = summ(ser[best], 2016, 2024)
ok = best != A and b2 > a2 and pl["plus"] >= 7
print(f"\n[5] 판정: 2016~2020 1위 = {best} (CAGR {m1[best] * 100:+.1f}%) · 2021~2024 {b2 * 100:+.1f}% vs A {a2 * 100:+.1f}% · "
      f"플러스 {pl['plus']}/{pl['ny']} → {'통과' if ok else '불통과'}")
print("참고: 레버리지 세금 미반영(lev 셀 기록: 국내 레버리지 과표 상승분 ≈ 0). F는 레버리지 2종이 업종 ETF와 같은 거래대금 조건으로 후보.")
print("끝. 출력 전체를 붙여 주세요.")
