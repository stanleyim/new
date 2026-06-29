"""
v7 final forward test runner.
매일 KST 20:00 실행 (GitHub Actions cron).
"""
import os
import json
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path

# KRX 로그인 우회 (GH Actions IP 차단 회피)
_krx_id = os.environ.pop("KRX_ID", None)
_krx_pw = os.environ.pop("KRX_PW", None)

import pandas as pd
import numpy as np
import requests
from pykrx import stock

# ===== 설정 =====
ROOT = Path(__file__).parent
DATA = ROOT / "data"
OUT = ROOT / "output"
SIG_DIR = OUT / "signals"
HOLDINGS_PATH = OUT / "holdings.json"
RESULTS_PATH = OUT / "results.json"

UNIVERSE_PATH = DATA / "universe.parquet"
OHLCV_PATH = DATA / "ohlcv_full.parquet"
FLOW_PATH = DATA / "flow_full.parquet"
SHORT_PATH = DATA / "short_full.parquet"

# v7 final 파라미터
TOTAL_COST = 0.00206
LIQ_TH = 30e8
K_MAX = 5
MAX_WEIGHT = 0.25
N_PICK_MIN = 3
HOLD_DAYS = 20

REPO_LABEL = "stanleyim/new"

# ===== Telegram =====
def send_telegram(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("[WARN] Telegram 환경변수 없음, 알림 스킵")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    r = requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=30)
    if r.status_code != 200:
        print(f"[WARN] Telegram 실패: {r.status_code} {r.text}")

# ===== 데이터 fetch =====
def fetch_and_append():
    """기존 parquet 마지막 날짜 이후 데이터를 append (KRX 로그인 없이)"""
    universe = pd.read_parquet(UNIVERSE_PATH)
    ohlcv_old = pd.read_parquet(OHLCV_PATH)
    flow_old  = pd.read_parquet(FLOW_PATH)
    short_old = pd.read_parquet(SHORT_PATH)

    # 3개 데이터 중 가장 이른 마지막 날짜 = 시작점
    last_ohlcv = pd.to_datetime(ohlcv_old["date"]).max()
    last_flow  = pd.to_datetime(flow_old["date"]).max()
    last_short = pd.to_datetime(short_old["date"]).max()
    last_date = min(last_ohlcv, last_flow, last_short)
    today = pd.Timestamp.now(tz="Asia/Seoul").normalize().tz_localize(None)
    start_date = last_date + pd.Timedelta(days=1)
    print(f"last_ohlcv={last_ohlcv.date()}, last_flow={last_flow.date()}, last_short={last_short.date()}")
    print(f"fetch from {start_date.date()} to {today.date()}")

    if start_date > today:
        print(f"이미 최신 ({last_date.date()}), fetch 스킵")
        return ohlcv_old, flow_old, short_old, universe

    print(f"Fetch 범위: {start_date.date()} ~ {today.date()}")
    s_str = start_date.strftime("%Y%m%d")
    e_str = today.strftime("%Y%m%d")

    tickers = universe["ticker"].tolist()
    new_ohlcv = []
    new_flow = []
    new_short = []

    for tkr in tickers:
        try:
            o = stock.get_market_ohlcv_by_date(s_str, e_str, tkr, adjusted=True)
            if len(o) > 0:
                o = o.reset_index()
                o["ticker"] = tkr
                o["name"] = stock.get_market_ticker_name(tkr)
                o = o.rename(columns={"날짜":"date"})
                new_ohlcv.append(o)
        except Exception as e:
            print(f"[WARN] OHLCV {tkr}: {e}")

        try:
            f = stock.get_market_trading_value_by_date(s_str, e_str, tkr)
            if len(f) > 0:
                f = f.reset_index()
                f["ticker"] = tkr
                f["name"] = stock.get_market_ticker_name(tkr)
                f = f.rename(columns={"날짜":"date"})
                new_flow.append(f)
        except Exception as e:
            print(f"[WARN] FLOW {tkr}: {e}")

        try:
            sh = stock.get_shorting_volume_by_date(s_str, e_str, tkr)
            if len(sh) > 0:
                sh = sh.reset_index()
                sh["ticker"] = tkr
                sh["name"] = stock.get_market_ticker_name(tkr)
                sh = sh.rename(columns={"날짜":"date"})
                new_short.append(sh)
        except Exception as e:
            print(f"[WARN] SHORT {tkr}: {e}")

    def concat_save(old, new_list, path):
        if not new_list:
            return old
        new_df = pd.concat(new_list, ignore_index=True)
        new_df["date"] = pd.to_datetime(new_df["date"])
        old["date"] = pd.to_datetime(old["date"])
        merged = pd.concat([old, new_df], ignore_index=True)
        merged = merged.drop_duplicates(subset=["date","ticker"], keep="last")
        merged = merged.sort_values(["ticker","date"]).reset_index(drop=True)
        merged.to_parquet(path, index=False)
        return merged

    ohlcv = concat_save(ohlcv_old, new_ohlcv, OHLCV_PATH)
    flow  = concat_save(flow_old, new_flow, FLOW_PATH)
    short = concat_save(short_old, new_short, SHORT_PATH)
    return ohlcv, flow, short, universe

# ===== Feature 계산 =====
def compute_features(ohlcv, flow, short):
    df = ohlcv.merge(flow[["date","ticker","외국인합계","기관합계","개인"]],
                     on=["date","ticker"], how="inner")
    df = df.merge(short[["date","ticker","공매도","비중"]],
                  on=["date","ticker"], how="left")
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

    df = df.groupby("ticker", group_keys=False).apply(per_ticker)

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
    return df

# ===== 시총 분류 =====
def add_cap_class(df, universe):
    univ_sorted = universe.sort_values("시가총액", ascending=False).reset_index(drop=True)
    n = len(univ_sorted)
    univ_sorted["cap_class"] = pd.cut(univ_sorted.index, bins=[-1, n//3, 2*n//3, n],
                                       labels=["대형","중형","소형"])
    cap_map = dict(zip(univ_sorted["ticker"], univ_sorted["cap_class"]))
    df["cap_class"] = df["ticker"].map(cap_map)
    return df

# ===== 신호 산출 (오늘 날짜 기준) =====
def select_signals(df, target_date):
    """target_date의 신호 종목 산출"""
    today_df = df[df["date"] == pd.to_datetime(target_date)]
    today_df = today_df[today_df["n_signals"] >= 3]
    today_df = today_df.dropna(subset=["거래대금_20ma"])
    today_df = today_df[today_df["거래대금_20ma"] >= LIQ_TH]
    if len(today_df) == 0:
        return []
    top = today_df.nlargest(min(5, len(today_df)), "signal_score")
    return top.to_dict("records")

# ===== 보유 추적 =====
def load_holdings():
    if HOLDINGS_PATH.exists():
        return json.loads(HOLDINGS_PATH.read_text())
    return []

def save_holdings(holdings):
    HOLDINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    HOLDINGS_PATH.write_text(json.dumps(holdings, indent=2, ensure_ascii=False, default=str))

def load_results():
    if RESULTS_PATH.exists():
        return json.loads(RESULTS_PATH.read_text())
    return []

def save_results(results):
    RESULTS_PATH.write_text(json.dumps(results, indent=2, ensure_ascii=False, default=str))

def update_holdings(holdings, df, target_date, signals):
    """T+20 도래 종목 종결 + 신규 추가 + 보유 현재가 업데이트"""
    closed = []
    open_h = []
    target_d = pd.to_datetime(target_date)
    for h in holdings:
        entry_date = pd.to_datetime(h["entry_date"])
        days_held = (target_d - entry_date).days
        # T+20 매도 여부 (영업일 카운트는 단순화: 30일 경과 시 강제 종결)
        if days_held >= 28:  # 영업일 20 ≈ 캘린더 28일
            closed.append(h)
        else:
            # 현재가 업데이트
            row = df[(df["ticker"]==h["ticker"]) & (df["date"]==target_d)]
            if len(row) > 0:
                cur_price = float(row["종가"].iloc[0])
                h["current_price"] = cur_price
                h["current_ret"] = cur_price / h["entry_price"] - 1
                h["days_held"] = days_held
            open_h.append(h)

    # 신규 추가
    n_open = len(open_h)
    can_add = K_MAX - n_open
    new_added = []
    if can_add > 0 and len(signals) >= N_PICK_MIN:
        for s in signals[:can_add]:
            new_added.append({
                "entry_date": str(target_date),
                "ticker": s["ticker"],
                "name": s["name"],
                "signal_score": float(s["signal_score"]),
                "n_signals": int(s["n_signals"]),
                "entry_price_planned": "T+1 시가",
                "exit_date_planned": str((target_d + pd.Timedelta(days=28)).date()),
                "weight": MAX_WEIGHT,
                "current_price": None,
                "current_ret": None,
                "days_held": 0,
            })

    return open_h + new_added, closed, new_added

# ===== Telegram 메시지 작성 =====
def format_message(target_date, signals, holdings_after, closed, new_added, n_pick_valid):
    lines = [f"📊 {REPO_LABEL}", f"{target_date} (20:00 산출)", ""]

    if len(signals) > 0:
        lines.append(f"[매수 후보] {len(signals)}개")
        for i, s in enumerate(signals, 1):
            sigs = [k for k in ["SIG1","SIG2","SIG3","SIG4","SIG5","SIG6","SIG7"] if s[k]==1]
            lines.append(f"{i}. [{s['ticker']}] {s['name']}")
            lines.append(f"   score: {s['signal_score']:.2f} | n: {s['n_signals']} | {s.get('cap_class','?')}")
            lines.append(f"   활성: {' '.join(sigs)}")
            lines.append(f"   vol_surge: {s['vol_surge']:.2f} | frgn_z: {s['frgn_z']:+.2f}")
            lines.append(f"   거래대금_20ma: {s['거래대금_20ma']/1e8:.0f}억")
        lines.append("")
        if n_pick_valid:
            lines.append(f"n_pick: {len(signals)}/5 → 매매 권고 (n_pick≥3 충족)")
        else:
            lines.append(f"n_pick: {len(signals)}/5 → 매매 보류 (n_pick<3)")
    else:
        lines.append("[매수 후보] 없음 (신호 미발생)")
    lines.append("")

    if new_added:
        lines.append(f"[신규 진입] {len(new_added)}개")
        for h in new_added:
            lines.append(f"- [{h['ticker']}] {h['name']}: T+1 시가 매수 예정")
        lines.append("")

    open_h = [h for h in holdings_after if h not in new_added]
    if open_h:
        lines.append(f"[보유 모니터링] {len(open_h)}개")
        for h in open_h:
            cur = h.get("current_ret")
            cur_str = f"{cur*100:+.2f}%" if cur is not None else "?"
            lines.append(f"- {h['entry_date']} [{h['ticker']}] {h['name']}: {h.get('days_held',0)}d, {cur_str}")
        lines.append("")

    if closed:
        lines.append(f"[종결 결과] {len(closed)}개")
        for c in closed:
            cur = c.get("current_ret")
            net = (cur - TOTAL_COST) if cur is not None else None
            net_str = f"{net*100:+.2f}%" if net is not None else "?"
            lines.append(f"- {c['entry_date']} [{c['ticker']}] {c['name']}: 종결, net {net_str}")

    return "\n".join(lines)

# ===== 메인 =====
def main():
    SIG_DIR.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)

    target_date_str = pd.Timestamp.now(tz="Asia/Seoul").strftime("%Y-%m-%d")
    target_date = pd.to_datetime(target_date_str)
    print(f"=== 실행: {target_date_str} ===")

    # 1. 데이터 fetch
    print("\n[1] 데이터 fetch...")
    ohlcv, flow, short, universe = fetch_and_append()

    # 2. Feature 계산
    print("\n[2] Feature 계산...")
    df = compute_features(ohlcv, flow, short)
    df = add_cap_class(df, universe)

    # 3. 오늘 날짜 신호 산출
    print(f"\n[3] 신호 산출 ({target_date_str})...")
    # 오늘 데이터 없으면 직전 영업일 사용
    available_dates = sorted(df["date"].unique())
    if target_date not in available_dates:
        signal_date = available_dates[-1]
        print(f"오늘 데이터 없음. 직전 영업일 사용: {signal_date}")
    else:
        signal_date = target_date

    signals = select_signals(df, signal_date)
    n_pick_valid = len(signals) >= N_PICK_MIN
    print(f"신호 종목: {len(signals)} (n_pick_valid={n_pick_valid})")

    # 4. 보유 업데이트
    print("\n[4] 보유 추적 업데이트...")
    holdings = load_holdings()
    new_signals_for_entry = signals if n_pick_valid else []
    holdings_after, closed, new_added = update_holdings(holdings, df, signal_date, new_signals_for_entry)
    save_holdings(holdings_after)

    if closed:
        results = load_results()
        for c in closed:
            results.append(c)
        save_results(results)

    # 5. 신호 JSON 저장
    sig_path = SIG_DIR / f"{target_date_str}.json"
    sig_path.write_text(json.dumps({
        "date": target_date_str,
        "signal_date": str(signal_date),
        "signals": [{k: (str(v) if isinstance(v,(pd.Timestamp,np.generic)) else v) for k,v in s.items() if k not in ["high_20d","high_60d","hl_range"]} for s in signals],
        "n_pick_valid": n_pick_valid,
        "n_open_after": len(holdings_after),
        "n_closed": len(closed),
        "n_new": len(new_added),
    }, indent=2, ensure_ascii=False, default=str))
    print(f"신호 저장: {sig_path}")

    # 6. Telegram 알림
    print("\n[5] Telegram 알림...")
    msg = format_message(target_date_str, signals, holdings_after, closed, new_added, n_pick_valid)
    send_telegram(msg)
    print(msg)

    print("\n=== 완료 ===")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        tb = traceback.format_exc()
        err_msg = f"❌ {REPO_LABEL} 실행 실패\n\n{e}\n\n{tb[:2000]}"
        print(err_msg)
        send_telegram(err_msg)
        sys.exit(1)
