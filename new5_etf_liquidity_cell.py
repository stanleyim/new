# new5_etf_liquidity_cell.py — 읽기 전용, KRX 조회 없음 (Drive new5_etf_breadth/ 저장분만 사용)
# 목적: 밤사이 규칙 결과가 '실제로 그 가격에 사고팔 수 있는' ETF에서도 유지되는지 확인.
#
# ── 사전 고정 (결과 보기 전에 정함) ──
# 거래 많음 기준: 2015~2024 하루 거래대금 중앙값으로 56개를 4등분(Q1 적음 ~ Q4 많음). 임의 문턱 없음.
# 체결 가능 판정: Q4(거래 많은 14개) 동일가중 2015~2024 CAGR > 0 ∧ 플러스 연도 ≥ 7/10
# 가격 잡음 지표: 종가 괴리 = 종가 ÷ NAV − 1 (매일). 평균(부호 있음)·절대값 중앙값을 ETF별로.
#   종가가 NAV보다 낮게 찍히는 ETF는 다음날 시가가 제자리로 돌아오며 밤사이 수익이 부풀려질 수 있음.
# 규칙·비용·월요일 처리는 앞 셀과 동일(비용 왕복 0.006%).
import os, json
import numpy as np, pandas as pd

P = "/content/drive/MyDrive/new5_etf_breadth"
if not os.path.exists(P):
    from google.colab import drive
    drive.mount("/content/drive")
names = json.load(open(f"{P}/names_20150102.json", encoding="utf-8"))
COST = 0.00006

def nights(d):
    d = d[d["종가"] > 0]
    on = d["시가"].shift(-1)
    r = (on / d["종가"] - 1).where(on > 0)
    nxt = pd.Series(d.index, index=d.index).shift(-1)
    x = pd.DataFrame({"r": r, "nxt": nxt}).dropna()
    x = x[x.r.abs() < 0.30]
    x = x[pd.to_datetime(x.nxt).dt.weekday != 0]
    return x.r - COST

def stats(n):
    span = (n.index[-1] - n.index[0]).days / 365.25
    eq = (1 + n).cumprod()
    yrs = n.groupby(n.index.year).apply(lambda s: (1 + s).prod() - 1)
    return dict(CAGR=eq.iloc[-1] ** (1 / span) - 1, Sharpe=n.mean() / n.std() * np.sqrt(len(n) / span),
                MDD=(eq / eq.cummax() - 1).min(), 플러스=int((yrs > 0).sum()), 연수=len(yrs))

rows, nets = [], {}
for f in sorted(os.listdir(P)):
    if not f.endswith(".parquet"):
        continue
    t = f[:-8]
    d = pd.read_parquet(f"{P}/{f}")
    d10 = d[(d.index.year >= 2015) & (d.index.year <= 2024)]
    n = nights(d)
    a = n[(n.index.year >= 2015) & (n.index.year <= 2024)]
    if len(a) < 500:
        continue
    nets[t] = a
    v = d10[(d10["NAV"] > 0) & (d10["종가"] > 0)]
    prem = v["종가"] / v["NAV"] - 1
    s = stats(a)
    rows.append([t, names.get(t, ""), d10["거래대금"].median() / 1e8, (d10["거래량"] == 0).mean() * 100,
                 prem.mean() * 100, prem.abs().median() * 100, a.mean() * 100, s["CAGR"] * 100, s["Sharpe"],
                 s["MDD"] * 100, f"{s['플러스']}/{s['연수']}"])
T = pd.DataFrame(rows, columns=["코드", "이름", "거래대금중앙(억)", "거래0일%", "종가괴리평균%", "괴리절대중앙%",
                                "하루평균%", "CAGR%", "Sharpe", "MDD%", "플러스연도"])
T["등분"] = pd.qcut(T["거래대금중앙(억)"].rank(method="first"), 4, labels=["Q1 적음", "Q2", "Q3", "Q4 많음"])
pd.set_option("display.width", 260); pd.set_option("display.max_rows", 200)
print(f"[1] ETF {len(T)}개, 거래대금 많은 순")
print(T.sort_values("거래대금중앙(억)", ascending=False).round(3).to_string(index=False))

print("\n[2] 거래대금 4등분별 동일가중 2015~2024 (비용 0.006%)")
for q, g in T.groupby("등분", observed=True):
    s = stats(pd.DataFrame({t: nets[t] for t in g["코드"]}).mean(axis=1).dropna())
    print(f"  {q} ({len(g)}개, 거래대금 중앙 {g['거래대금중앙(억)'].median():,.1f}억): CAGR {s['CAGR'] * 100:+.1f}% · "
          f"Sharpe {s['Sharpe']:.2f} · MDD {s['MDD'] * 100:.1f}% · 플러스 연도 {s['플러스']}/{s['연수']} · "
          f"종가괴리 절대 중앙 {g['괴리절대중앙%'].median():.3f}%")

c1 = T["하루평균%"].corr(T["종가괴리평균%"]); c2 = T["하루평균%"].corr(np.log(T["거래대금중앙(억)"]))
print(f"\n[3] ETF 사이 상관: 하루평균 수익 vs 종가괴리 평균 {c1:+.2f} · vs log(거래대금) {c2:+.2f}")
k200 = T[T["이름"].str.contains(r"(?:^|\s)200$", regex=True) & ~T["이름"].str.contains("펀더멘탈")]
print("\n[4] 코스피200 추종 ETF끼리 (같은 지수)")
print(k200.sort_values("CAGR%", ascending=False)[["코드", "이름", "거래대금중앙(억)", "거래0일%", "종가괴리평균%",
                                                   "괴리절대중앙%", "CAGR%"]].round(3).to_string(index=False))

q4 = T[T["등분"] == "Q4 많음"]["코드"]
s4 = stats(pd.DataFrame({t: nets[t] for t in q4}).mean(axis=1).dropna())
ok = s4["CAGR"] > 0 and s4["플러스"] >= 7
print(f"\n[판정] Q4(거래 많은 {len(q4)}개) CAGR {s4['CAGR'] * 100:+.1f}% · 플러스 연도 {s4['플러스']}/{s4['연수']} → "
      f"{'체결 가능한 ETF에서도 유지' if ok else '거래 많은 ETF에선 유지 안 됨'}")
print("참고: 거래대금 4등분은 2015~2024 전체 중앙값 기준(그 시점에 미리 알 수 없는 값) → 체결 가능성 확인용, 매매 규칙 아님.")
print("끝. 출력 전체를 붙여 주세요.")
