# new5_etf_rotate_liq_cell.py — 읽기 전용, KRX 조회 없음 (Drive new5_etf_breadth/ 저장분만)
# 목적: ② 갈아타기(상위5 CAGR +31.5%)가 거래 많은 ETF 안에서도 유지되는지 → 실제 체결 가능성 확인.
# 규칙(앞 셀과 동일): 매달 첫 거래일, 직전 252밤 밤사이 순수익(로그 합) 상위 5개, 그달 같은 비중,
#   종가 매수 → 다음 거래일 시가 매도, 비용 왕복 0.006%, 다음 거래일 월요일이면 쉼.
# 추가(사전 고정): 고를 때 '직전 60거래일 거래대금 중앙값'이 그날 대상 ETF 중 상위 절반인 것만 후보.
#   (그날 아침까지 아는 값만 사용. 상위 1/4 안에서 고르기는 참고만)
# 판정(사전 고정, 2016~2024): 거래 상위 절반 안 상위5 CAGR > 거래 상위 절반 전체 같은 비중 CAGR ∧ 플러스 연도 ≥ 7/9
# 점검: 제한 없는 상위5를 다시 계산 → 앞 셀 결과(CAGR +31.5%)와 같아야 함.
import os, json, glob
import numpy as np, pandas as pd

P = "/content/drive/MyDrive/new5_etf_breadth"
if not os.path.exists(P):
    from google.colab import drive
    drive.mount("/content/drive")
try:
    names = json.load(open(f"{P}/names_20150102.json", encoding="utf-8"))
except Exception:
    names = {}
nm = lambda t: names.get(t, t)
COST, LOOK, TOP, LIQ = 0.00006, 252, 5, 60

# ---------- 1. 자료 ----------
R, V, G = {}, {}, {}
for f in sorted(glob.glob(f"{P}/*.parquet")):
    t = os.path.basename(f)[:-8]
    d = pd.read_parquet(f)
    if not {"시가", "종가", "거래대금"} <= set(d.columns) or d.index.min() > pd.Timestamp("2015-01-05"):
        continue
    d = d[(d["시가"] > 0) & (d["종가"] > 0)]
    on = d["시가"].shift(-1)
    r = (on / d["종가"] - 1).where(on > 0)
    R[t] = r[r.abs() < 0.30]
    V[t] = d["거래대금"].astype(float)
    G[t] = d["종가"] / d["NAV"].where(d["NAV"] > 0) - 1 if "NAV" in d.columns else pd.Series(np.nan, index=d.index)
R = pd.DataFrame(R).sort_index()
R = R[R.index >= "2015-01-01"].dropna(how="all")
V = pd.DataFrame(V).reindex(R.index)
G = pd.DataFrame(G).reindex(R.index)
lev = [t for t in R.columns if any(w in names.get(t, "") for w in ("레버리지", "인버스", "2X"))]
R, V, G = R.drop(columns=lev), V.drop(columns=lev), G.drop(columns=lev)
print(f"[1] ETF {R.shape[1]}개(레버리지·인버스 제외 {len(lev)}) · 밤 {len(R)} · {R.index.min().date()}~{R.index.max().date()} · 이름 {len(names)}개")

dates = R.index.to_series()
mon = (pd.to_datetime(dates.shift(-1)).dt.weekday == 0).astype(bool)
N = R - COST
score = np.log1p(N).rolling(LOOK, min_periods=int(LOOK * 0.9)).sum().shift(1)   # 그날 아침까지
liq = V.rolling(LIQ, min_periods=int(LIQ * 0.8)).median().shift(1)               # 그날 아침까지
first = dates.groupby([dates.index.year, dates.index.month]).transform("min") == dates

# ---------- 2. 매달 고르기 ----------
def build(k, q):
    """q = None: 제한 없음 / 0.5: 거래 상위 절반 / 0.25: 상위 1/4. k = None: 후보 전체 같은 비중"""
    sel = pd.DataFrame(False, index=R.index, columns=R.columns)
    picks, p = {}, None
    for dt in R.index:
        if first.loc[dt]:
            s = score.loc[dt].dropna()
            if q is not None:
                l = liq.loc[dt].dropna()
                elig = l[l >= l.quantile(1 - q)].index
                s = s.reindex(elig).dropna()
            p = None
            if len(s) >= (k or 1) and len(s) >= 5:
                p = list(s.sort_values(ascending=False).index[:k]) if k else list(s.index)
                picks[dt] = p
        if p:
            sel.loc[dt, p] = True
    return sel, picks

def summ(s, lo, hi):
    s = s[(s.index.year >= lo) & (s.index.year <= hi) & ~mon.reindex(s.index).fillna(False).astype(bool)].dropna()
    span = (s.index[-1] - s.index[0]).days / 365.25
    eq = (1 + s).cumprod()
    yrs = s.groupby(s.index.year).apply(lambda v: (1 + v).prod() - 1)
    return dict(cagr=eq.iloc[-1] ** (1 / span) - 1, sharpe=s.mean() / s.std() * np.sqrt(len(s) / span),
                mdd=(eq / eq.cummax() - 1).min(), plus=int((yrs > 0).sum()), ny=len(yrs), yrs=yrs, n=len(s))

cases = {"제한없음 상위5": (TOP, None), "거래상위절반 상위5": (TOP, 0.5), "거래상위절반 전체": (None, 0.5),
         "거래상위1/4 상위5": (TOP, 0.25), "거래상위1/4 전체": (None, 0.25)}
ser, pk, sl = {}, {}, {}
for lab, (k, q) in cases.items():
    sl[lab], pk[lab] = build(k, q)
    ser[lab] = N.where(sl[lab]).mean(axis=1)
ser["전체"] = N.mean(axis=1)

# ---------- 3. 결과 ----------
for lab, lo, hi in (("2016~2024 (판정)", 2016, 2024), ("2025~2026 (참고)", 2025, 2026)):
    print(f"\n■ {lab}")
    for k, s in ser.items():
        m = summ(s, lo, hi)
        print(f"  {k:12s} CAGR {m['cagr'] * 100:+6.1f}% · Sharpe {m['sharpe']:.2f} · MDD {m['mdd'] * 100:6.1f}% · 플러스 {m['plus']}/{m['ny']} ({m['n']}밤)")

print("\n■ 연도별 순수익(정식 밤)")
ys = {k: summ(s, 2016, 2026)["yrs"] for k, s in ser.items()}
print("  연도 " + " ".join(f"{k:>12s}" for k in ys))
for y in sorted(ys["전체"].index):
    print(f"  {y} " + " ".join(f"{ys[k].get(y, np.nan) * 100:+11.1f}%" for k in ys))

# ---------- 4. 고른 ETF의 거래대금·종가 괴리 ----------
print("\n■ 고른 ETF의 거래대금(고를 때 직전 60일 중앙값, 억원)과 종가 괴리(종가÷NAV−1, 보유한 날 평균)")
for lab in ("제한없음 상위5", "거래상위절반 상위5", "거래상위1/4 상위5"):
    vals = [liq.at[dt, t] / 1e8 for dt, p in pk[lab].items() for t in p if pd.notna(liq.at[dt, t])]
    g = G.where(sl[lab]).stack().mean() * 100
    print(f"  {lab}: 거래대금 최소 {np.min(vals):,.1f} · 하위10% {np.percentile(vals, 10):,.1f} · 중앙 {np.median(vals):,.1f}억 · 종가 괴리 평균 {g:+.3f}%")
print(f"  전체 ETF 종가 괴리 평균 {G.stack().mean() * 100:+.3f}%")

cnt = pd.Series([t for p in pk["거래상위절반 상위5"].values() for t in p]).value_counts()
print(f"\n■ 거래상위절반 상위5에 자주 뽑힌 ETF (전체 {len(pk['거래상위절반 상위5'])}개월)")
for t, c in cnt.head(10).items():
    print(f"  {t} {nm(t)}: {c}개월")
for dt in list(pk["거래상위절반 상위5"])[-3:]:
    print(f"  {dt.date()} 고른 것: " + ", ".join(nm(t) for t in pk["거래상위절반 상위5"][dt]))

# ---------- 5. 판정 ----------
a, b = summ(ser["거래상위절반 상위5"], 2016, 2024), summ(ser["거래상위절반 전체"], 2016, 2024)
ok = a["cagr"] > b["cagr"] and a["plus"] >= 7
c = summ(ser["제한없음 상위5"], 2016, 2024)
print(f"\n[점검] 제한없음 상위5 CAGR {c['cagr'] * 100:+.1f}% (앞 셀 +31.5%와 같아야 함)")
print(f"[5] 판정: 거래상위절반 상위5 CAGR {a['cagr'] * 100:+.1f}% vs 거래상위절반 전체 {b['cagr'] * 100:+.1f}% · 플러스 {a['plus']}/{a['ny']} → {'통과' if ok else '불통과'}")
print("참고: 종가 괴리가 크게 마이너스면, 종가가 NAV보다 싸게 찍혀 다음날 시가에서 되돌아오는 몫이 수익에 섞였을 수 있음.")
print("끝. 출력 전체를 붙여 주세요.")
