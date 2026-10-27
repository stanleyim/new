# new5_etf_rotate_cell.py — 읽기 전용 (레포·운용 변경 없음, KRX는 이름 조회만)
# 목적: 밤 흐름이 강한 ETF(업종·스타일·지수 포함, 레버리지 제외)를 따라가면 흐름을 더 크게 먹는지. 자료 = Drive new5_etf_breadth/ (2015-01-02 상장 국내주식형 56개).
# 규칙(사전 고정): 매달 첫 거래일, 직전 252밤의 밤사이 순수익(로그 합)이 큰 상위 5개를 고르고
#   그달 동안 매일 종가 매수 → 다음 거래일 시가 매도, 5개 같은 비중. 비용 왕복 0.006%. 다음 거래일 월요일이면 쉼(정식).
# 제외: 이름에 레버리지·인버스·2X가 있는 ETF(①과 겹치지 않게).
# 비교: 대상 전체 같은 비중 / KODEX 200(069500). 평가 2016~2024(2015는 첫 1년 관찰용), 2025~2026 참고.
# 판정(사전 고정): 상위5 CAGR > 대상 전체 CAGR ∧ 플러스 연도 ≥ 7/9. 상위 1·3·10개는 참고만(판정 아님).
import os, glob
import numpy as np, pandas as pd

P = "/content/drive/MyDrive/new5_etf_breadth"
if not os.path.exists(P):
    from google.colab import drive
    drive.mount("/content/drive")
COST, LOOK, TOP, REF = 0.00006, 252, 5, (1, 3, 10)

# ---------- 1. 자료 ----------
R = {}
for f in sorted(glob.glob(f"{P}/*.parquet")):
    t = os.path.basename(f)[:-8]
    d = pd.read_parquet(f)
    if not {"시가", "종가"} <= set(d.columns) or d.index.min() > pd.Timestamp("2015-01-05"):
        continue
    d = d[(d["시가"] > 0) & (d["종가"] > 0)]
    on = d["시가"].shift(-1)
    r = (on / d["종가"] - 1).where(on > 0)
    R[t] = r[r.abs() < 0.30]
R = pd.DataFrame(R).sort_index()
R = R[R.index >= "2015-01-01"].dropna(how="all")
print(f"[1] ETF {R.shape[1]}개 · 밤 {len(R)} · {R.index.min().date()}~{R.index.max().date()}")
if R.shape[1] != 56:
    print("[1] 56개가 아님 → 결과 쓰기 전 상의")

names = {}
try:
    import sys, subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "pykrx"], check=True)
    from google.colab import userdata
    os.environ["KRX_ID"] = userdata.get("KRX_ID")
    os.environ["KRX_PW"] = userdata.get("KRX_PW")
    from pykrx import stock
    for t in R.columns:
        names[t] = str(stock.get_etf_ticker_name(t))
except Exception as e:
    print(f"[1] 이름 조회 실패(계산은 계속): {type(e).__name__} {str(e)[:100]}")
lev = [t for t in R.columns if any(w in names.get(t, "") for w in ("레버리지", "인버스", "2X"))]
if not names:
    print("[1] 이름 확인 못함 → 레버리지 제외 못함, 결과 쓰기 전 상의")
print(f"[1] 레버리지·인버스 제외 {len(lev)}개: " + (", ".join(names[t] for t in lev) if lev else "없음"))
R = R.drop(columns=lev)
print(f"[1] 대상 {R.shape[1]}개")

nm = lambda t: names.get(t, t)

dates = R.index.to_series()
nxt = dates.shift(-1)
mon = pd.to_datetime(nxt).dt.weekday == 0
N = R - COST

# ---------- 2. 매달 고르기 ----------
score = np.log1p(N).rolling(LOOK, min_periods=int(LOOK * 0.9)).sum().shift(1)   # 그날 아침까지 아는 것만
first = dates.groupby([dates.index.year, dates.index.month]).transform("min") == dates
picks, cur = {}, {}
for k in (TOP,) + REF:
    sel = pd.DataFrame(False, index=R.index, columns=R.columns)
    p = None
    for dt in R.index:
        if first.loc[dt]:
            s = score.loc[dt].dropna()
            p = list(s.sort_values(ascending=False).index[:k]) if len(s) >= 20 else None
            if k == TOP and p:
                picks[dt] = p
        if p:
            sel.loc[dt, p] = True
    cur[k] = sel

def port(mask):
    x = N.where(mask)
    return x.mean(axis=1)                                   # 그날 고른 것 중 자료 있는 것 같은 비중

ser = {f"상위{k}": port(cur[k]) for k in (TOP,) + REF}
ser["전체"] = N.mean(axis=1)
ser["KODEX 200"] = N["069500"] if "069500" in N else None

def summ(s, lo, hi):
    s = s[(s.index.year >= lo) & (s.index.year <= hi) & ~mon.reindex(s.index).fillna(False).astype(bool)].dropna()
    span = (s.index[-1] - s.index[0]).days / 365.25
    eq = (1 + s).cumprod()
    yrs = s.groupby(s.index.year).apply(lambda v: (1 + v).prod() - 1)
    return dict(cagr=eq.iloc[-1] ** (1 / span) - 1, sharpe=s.mean() / s.std() * np.sqrt(len(s) / span),
                mdd=(eq / eq.cummax() - 1).min(), plus=int((yrs > 0).sum()), ny=len(yrs), yrs=yrs, n=len(s))

# ---------- 3. 결과 ----------
for lab, lo, hi in (("2016~2024 (판정)", 2016, 2024), ("2025~2026 (참고)", 2025, 2026)):
    print(f"\n■ {lab}")
    for k, s in ser.items():
        if s is None:
            continue
        m = summ(s, lo, hi)
        print(f"  {k:9s} CAGR {m['cagr'] * 100:+6.1f}% · Sharpe {m['sharpe']:.2f} · MDD {m['mdd'] * 100:6.1f}% · 플러스 {m['plus']}/{m['ny']} ({m['n']}밤)")

print("\n■ 연도별 순수익(정식 밤)")
ys = {k: summ(s, 2016, 2026)["yrs"] for k, s in ser.items() if s is not None}
print("  연도  " + "  ".join(f"{k:>9s}" for k in ys))
for y in sorted(ys[f"상위{TOP}"].index):
    print(f"  {y}  " + "  ".join(f"{ys[k].get(y, np.nan) * 100:+8.1f}%" for k in ys))

# ---------- 4. 무엇을 골랐나 ----------
cnt = pd.Series([t for p in picks.values() for t in p]).value_counts()
print(f"\n■ 상위{TOP}에 자주 뽑힌 ETF (전체 {len(picks)}개월)")
for t, c in cnt.head(15).items():
    print(f"  {t} {nm(t)}: {c}개월")
for dt in list(picks)[-3:]:
    print(f"  {dt.date()} 고른 것: " + ", ".join(nm(t) for t in picks[dt]))

# ---------- 5. 판정 ----------
a, b = summ(ser[f"상위{TOP}"], 2016, 2024), summ(ser["전체"], 2016, 2024)
ok = a["cagr"] > b["cagr"] and a["plus"] >= 7
print(f"\n[5] 판정: 상위{TOP} CAGR {a['cagr'] * 100:+.1f}% vs 전체 {b['cagr'] * 100:+.1f}% · 플러스 {a['plus']}/{a['ny']} → {'통과' if ok else '불통과'}")
print("참고: 점수는 그달 첫날 아침까지의 자료만 씀(미래 자료 없음). 정식 밤 = 다음 거래일 월요일이면 쉼.")
print("끝. 출력 전체를 붙여 주세요.")
