"""
v7 12년 백테스트 재현 (원본 스크립트 소실로 signal_runner.py 프로덕션 로직 그대로 재구성)
자본배분 방법론(형님 승인, 2026-09-24): K_MAX=5 슬롯 각각 독립적으로 "진입 시점의 실현자본" 25%를
배분(=최대 총 노출 125%, 미실현 손익은 마킹하지 않음). signal_runner.py의 K_MAX/MAX_WEIGHT 정의와
정확히 일치. 이 결과가 새 v7 기준선이며, 예전 보고치(+395%/Sharpe1.28/승률52%)는 재현 불가로 폐기.
"""
import pandas as pd
import numpy as np

DATA = '/home/claude/repo/data'
TOTAL_COST = 0.00206
LIQ_TH = 30e8
K_MAX = 5
MAX_WEIGHT = 0.25
N_PICK_MIN = 3
HOLD_BDAYS = 21  # T+1 진입 + T+20 종가청산 = entry_date로부터 21영업일

ohlcv = pd.read_parquet(f'{DATA}/ohlcv_full.parquet')
flow  = pd.read_parquet(f'{DATA}/flow_full.parquet')
short = pd.read_parquet(f'{DATA}/short_full.parquet')

# ===== compute_features (signal_runner.py와 동일 로직) =====
df = ohlcv.merge(flow[["date","ticker","외국인합계","기관합계","개인"]], on=["date","ticker"], how="inner")
df = df.merge(short[["date","ticker","공매도","비중"]], on=["date","ticker"], how="left")
df = df.rename(columns={"비중":"short_ratio"})
df["date"] = pd.to_datetime(df["date"])
df = df.sort_values(["ticker","date"]).reset_index(drop=True)
df.loc[df["시가"]==0, "시가"] = np.nan

def per_ticker(g):
    g = g.sort_values("date").copy()
    g["vol_surge"] = g["거래량"].rolling(5).mean() / g["거래량"].rolling(20).mean()
    g["gap"] = g["등락률"]
    g["high_20d"] = g["고가"].rolling(20).max()
    g["high_60d"] = g["고가"].rolling(60).max()
    g["is_breakout_20"] = (g["종가"] > g["high_20d"].shift(1)).astype(int)
    g["is_breakout_60"] = (g["종가"] > g["high_60d"].shift(1)).astype(int)
    g["frgn_z"] = (g["외국인합계"] - g["외국인합계"].rolling(20).mean()) / g["외국인합계"].rolling(20).std()
    g["short_chg"] = g["short_ratio"].rolling(5).mean() - g["short_ratio"].rolling(20).mean()
    g["hl_range"] = (g["고가"] - g["저가"]) / g["종가"]
    g["vol_ratio"] = g["hl_range"].rolling(5).mean() / g["hl_range"].rolling(20).mean()
    g["거래대금"] = g["종가"] * g["거래량"]
    g["거래대금_20ma"] = g["거래대금"].rolling(20).mean()
    return g

df = df.groupby("ticker", group_keys=True).apply(per_ticker, include_groups=False).reset_index(level=0)

df["SIG1"] = (df["is_breakout_20"] & (df["vol_surge"]>2.0) & (df["frgn_z"]>2.0)).astype(int)
df["SIG2"] = (df["is_breakout_60"] & (df["vol_surge"]>2.0)).astype(int)
df["SIG3"] = ((df["vol_surge"]>2.0) & (df["frgn_z"]>2.0)).astype(int)
df["SIG4"] = (df["gap"]<-5.0).astype(int)
df["SIG5"] = ((df["vol_surge"]>2.0) & (df["frgn_z"]>2.0) & (df["short_chg"]<-0.5)).astype(int)
df["SIG6"] = (df["is_breakout_20"] & (df["vol_surge"]>2.0)).astype(int)
df["SIG7"] = (df["vol_ratio"]>1.5).astype(int)
df["signal_score"] = (df["SIG1"]*1.87 + df["SIG2"]*1.64 + df["SIG3"]*1.61 +
                      df["SIG4"]*1.50 + df["SIG5"]*1.42 + df["SIG6"]*1.38 + df["SIG7"]*1.34)
df["n_signals"] = df[["SIG1","SIG2","SIG3","SIG4","SIG5","SIG6","SIG7"]].sum(axis=1)

print(f"피처 계산 완료: {len(df)}행, 기간 {df['date'].min().date()}~{df['date'].max().date()}")

# ===== 거래일 목록 & 빠른 조회용 인덱스 =====
trade_dates = sorted(df["date"].unique())
date_idx = {d:i for i,d in enumerate(trade_dates)}

open_px = df.pivot(index="date", columns="ticker", values="시가")
close_px = df.pivot(index="date", columns="ticker", values="종가")

# 일자별 선정 결과 미리 계산 (select_signals 로직 그대로)
elig = df[(df["n_signals"]>=N_PICK_MIN) & (df["거래대금_20ma"]>=LIQ_TH)].dropna(subset=["거래대금_20ma"])
daily_picks = {}
for d, g in elig.groupby("date"):
    top = g.nlargest(min(5,len(g)), "signal_score")
    daily_picks[d] = list(zip(top["ticker"], top["signal_score"]))

# ===== 포트폴리오 시뮬레이션 =====
equity = 1.0  # 실현자본 (미실현 마킹 안 함 — 승인된 방법론)
open_pos = []  # {ticker, entry_date_idx, entry_price, notional}
trades = []    # 청산된 거래 기록
equity_curve = []

for i, d in enumerate(trade_dates):
    # 1) 만기 청산 체크 (entry_date_idx + HOLD_BDAYS 도달)
    still_open = []
    for p in open_pos:
        if i - p["entry_date_idx"] >= HOLD_BDAYS:
            exit_px = close_px.at[d, p["ticker"]] if p["ticker"] in close_px.columns else np.nan
            if pd.notna(exit_px) and pd.notna(p["entry_price"]) and p["entry_price"]>0:
                ret = exit_px/p["entry_price"] - 1 - TOTAL_COST
                pnl = p["notional"] * ret
                equity += pnl
                trades.append({"ticker":p["ticker"], "entry_date":trade_dates[p["entry_date_idx"]],
                                "exit_date":d, "ret":ret, "pnl":pnl})
            # 가격 결측이면 거래 스킵(등록도 손익도 안 함) — 드묾
        else:
            still_open.append(p)
    open_pos = still_open

    # 2) 신규 진입 (전일 시그널 → 오늘 시가 진입, T+1 구조)
    if i>0:
        prev_d = trade_dates[i-1]
        if prev_d in daily_picks and len(daily_picks[prev_d])>=N_PICK_MIN:
            can_add = K_MAX - len(open_pos)
            if can_add>0:
                held_tickers = {p["ticker"] for p in open_pos}
                cands = [t for t,s in daily_picks[prev_d] if t not in held_tickers][:can_add]
                for t in cands:
                    ep = open_px.at[d, t] if t in open_px.columns else np.nan
                    if pd.notna(ep) and ep>0:
                        open_pos.append({"ticker":t, "entry_date_idx":i, "entry_price":ep,
                                          "notional": equity*MAX_WEIGHT})

    equity_curve.append({"date":d, "equity":equity, "n_open":len(open_pos)})

ec = pd.DataFrame(equity_curve).set_index("date")
tr = pd.DataFrame(trades)

# ===== 성과지표 =====
ec["ret"] = ec["equity"].pct_change().fillna(0)
daily_ret = ec["ret"]
ann_sharpe = daily_ret.mean()/daily_ret.std()*np.sqrt(252) if daily_ret.std()>0 else np.nan
cum_ret = ec["equity"].iloc[-1]/ec["equity"].iloc[0] - 1
roll_max = ec["equity"].cummax()
dd = ec["equity"]/roll_max - 1
mdd = dd.min()
win_rate = (tr["ret"]>0).mean()
mean_ret = tr["ret"].mean()

print("\n===== v7 재현 백테스트 결과 (새 기준선) =====")
print(f"기간: {trade_dates[0].date()} ~ {trade_dates[-1].date()}")
print(f"총 청산거래: {len(tr)}건")
print(f"승률: {win_rate:.1%}")
print(f"거래당 평균수익: {mean_ret:.2%}")
print(f"누적수익률: {cum_ret:.1%}")
print(f"Sharpe(연율화, 단순): {ann_sharpe:.2f}")
print(f"MDD: {mdd:.1%}")
print(f"n_pick_valid(5선정) 발생일수: {sum(1 for v in daily_picks.values() if len(v)>=N_PICK_MIN and len(v)==5)}")
print(f"n_pick(>=3) 발생일수: {sum(1 for v in daily_picks.values() if len(v)>=N_PICK_MIN)}")

ec.to_csv('/home/claude/work/v7_equity_curve.csv')
tr.to_csv('/home/claude/work/v7_trades.csv', index=False)
print("\n저장: v7_equity_curve.csv, v7_trades.csv")
