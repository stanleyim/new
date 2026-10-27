# new5_etf_overnight_v2_cell.py — 읽기 전용 (레포·운용 변경 없음)
# 목적: 밤사이 상승을 거래세 없는 그릇(국내주식형 ETF)으로 담으면 비용 빼고 남는지, ETF 자기 가격으로 실측.
# v2: 비용을 실제 값 0.006%로 고침(v1은 0.06% 잘못 가정), 연도별 순수익·CAGR·Sharpe 추가.
# 매매 가정: 종가 동시호가 매수 → 다음 거래일 시가 동시호가 매도(동시호가라 호가 차이 없음, 비용 = 수수료).
# 다음 거래일이 월요일이면 지금 규칙처럼 '그림자'(성과 제외). 기간 2015-01 ~ 2026-10-26.
# 점검: ETF 기간 조회 값이 날짜별 전 종목 조회(정규장 값)와 같은지 최근 5일 대조 → 다르면 결과 쓰지 않음.
import os, sys, subprocess
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "pykrx"], check=True)
from google.colab import userdata
os.environ["KRX_ID"] = userdata.get("KRX_ID")
os.environ["KRX_PW"] = userdata.get("KRX_PW")
import numpy as np, pandas as pd
from pykrx import stock

ETF = {"069500": "KODEX 200", "102110": "TIGER 200", "091160": "KODEX 반도체", "091230": "TIGER 반도체"}
S, E = "20150101", "20261026"
COST = 0.00006                            # 실제 ETF 왕복 비용 0.006% (수수료 편도 0.003% × 2, 거래세 없음)
COSTS = [0.0, COST, 0.00206]              # 참고: 비용 0 / 실제 0.006% / 주식과 같은 0.206%(비교용)

# ---------- 1. 자료 ----------
px = {}
for t, nm in ETF.items():
    d = stock.get_etf_ohlcv_by_date(S, E, t)
    px[t] = d
    print(f"[1] {t} {nm}: {len(d)}일 {d.index.min().date()}~{d.index.max().date()} · 컬럼 {list(d.columns)}")
ix = stock.get_index_ohlcv_by_date(S, E, "1028")
print(f"[1] KOSPI200 지수: {len(ix)}일 {ix.index.min().date()}~{ix.index.max().date()}")

# ---------- 2. 정규장 값 대조 ----------
bad = []
for dd in px["069500"].index[-5:]:
    b = stock.get_etf_ohlcv_by_ticker(dd.strftime("%Y%m%d"))
    for t in ETF:
        if dd not in px[t].index or t not in b.index:
            bad.append(f"{dd.date()} {t} 없음")
            continue
        for c in ("시가", "종가"):
            if float(px[t].at[dd, c]) != float(b.at[t, c]):
                bad.append(f"{dd.date()} {t} {c} 기간조회 {px[t].at[dd, c]} vs 날짜조회 {b.at[t, c]}")
print("[2] 정규장 대조:", "최근 5일 × 4종목 시가·종가 모두 일치" if not bad else "불일치 → 아래 결과 쓰지 말 것\n    " + "\n    ".join(bad))

# ---------- 3. 계산 ----------
def nights(d):
    d = d[d["종가"] > 0]
    on = d["시가"].shift(-1)
    r = (on / d["종가"] - 1).where(on > 0)
    nxt = pd.Series(d.index, index=d.index).shift(-1)
    x = pd.DataFrame({"r": r, "nxt": nxt}).dropna()
    x = x[x.r.abs() < 0.30]
    x["mon"] = pd.to_datetime(x.nxt).dt.weekday == 0
    x["y"] = x.index.year
    return x

def days(d):
    d = d[(d["시가"] > 0) & (d["종가"] > 0)]
    return d["종가"] / d["시가"] - 1

def report(nm, d):
    on, dy = nights(d), days(d)
    on["n"] = on.r - COST                                       # 정식 밤 순수익(비용 0.006%)
    print(f"\n■ {nm}")
    print("  연도  밤(정식)  정식 플러스비율  연 순수익(복리·0.006%)  낮(시가→종가)")
    for y, g in on.groupby("y"):
        f = g[~g.mon]
        print(f"  {y}  {f.r.mean() * 100:+.3f}%  {(f.r > 0).mean() * 100:5.1f}%  {((1 + f.n).prod() - 1) * 100:+7.1f}%  {dy[dy.index.year == y].mean() * 100:+.3f}%")
    for lab, lo, hi in (("2015~2024", 2015, 2024), ("2025~2026", 2025, 2026)):
        f = on[(on.y >= lo) & (on.y <= hi) & ~on.mon]
        span = (f.index[-1] - f.index[0]).days / 365.25
        eq = (1 + f.n).cumprod()
        cagr = eq.iloc[-1] ** (1 / span) - 1
        sharpe = f.n.mean() / f.n.std() * np.sqrt(len(f) / span)
        yrs = f.groupby("y").n.apply(lambda s: (1 + s).prod() - 1)
        cs = " · ".join(f"{c * 100:.3f}%→{(f.r.mean() - c) * 100:+.3f}%" for c in COSTS)
        print(f"  {lab}: 정식밤 {len(f)} · 비용별 하루 순수익 {cs}")
        print(f"     [비용 0.006%] CAGR {cagr * 100:+.1f}% · Sharpe {sharpe:.2f} · MDD {(eq / eq.cummax() - 1).min() * 100:.1f}% · "
              f"누적 {(eq.iloc[-1] - 1) * 100:+.1f}% · 플러스 연도 {(yrs > 0).sum()}/{len(yrs)}")
    return on

kon = report("KOSPI200 지수(참고, 거래 불가)", ix)
for t, nm in ETF.items():
    eon = report(f"{t} {nm}", px[t])
    j = eon[["r"]].join(kon[["r"]], rsuffix="_k", how="inner")
    print(f"  지수 밤사이와 비교({len(j)}일): 상관 {j.r.corr(j.r_k):.3f} · 평균 차이(ETF−지수) {(j.r - j.r_k).mean() * 100:+.3f}%p")

print("\n참고: Sharpe = 정식 밤 순수익 평균÷표준편차×√(연간 정식 밤 수). 월요일 넘김 밤은 보유 안 함(현금).")
print("참고: ETF 분배락(연 수회) 날은 밤사이 하락으로 잡혀 수익이 조금 낮게 나옴(보수적).")
print("끝. 출력 전체를 붙여 주세요.")
