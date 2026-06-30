"""
v7 final forward test runner — KIS API 버전
매일 KST 20:00 GitHub Actions cron 실행.
"""
import os
import json
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import numpy as np
import requests

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

TOTAL_COST = 0.00206
LIQ_TH = 30e8
K_MAX = 5
MAX_WEIGHT = 0.25
N_PICK_MIN = 3
HOLD_DAYS = 20

REPO_LABEL = "stanleyim/new"

KIS_BASE = "https://openapi.koreainvestment.com:9443"
KIS_RPS = 10  # 초당 호출 (안전 마진)

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

# ===== KIS 토큰 =====
def get_kis_token():
    app_key = os.environ.get("KIS_APP_KEY")
    app_secret = os.environ.get("KIS_APP_SECRET")
    if not app_key or not app_secret:
        raise RuntimeError("KIS_APP_KEY/SECRET 환경변수 없음")
    r = requests.post(
        f"{KIS_BASE}/oauth2/tokenP",
        json={"grant_type":"client_credentials","appkey":app_key,"appsecret":app_secret},
        timeout=30
    )
    if r.status_code != 200:
        raise RuntimeError(f"KIS 토큰 발급 실패: {r.status_code} {r.text}")
    data = r.json()
    if "access_token" not in data:
        raise RuntimeError(f"KIS 토큰 응답 이상: {data}")
    return data["access_token"], app_key, app_secret

# ===== KIS API 호출 (rate limit 적용) =====
class KisClient:
    def __init__(self):
        self.token, self.app_key, self.app_secret = get_kis_token()
        self.last_call = 0.0
        self.min_interval = 1.0 / KIS_RPS
        self.session = requests.Session()

    def _throttle(self):
        elapsed = time.time() - self.last_call
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self.last_call = time.time()

    def _get(self, path, tr_id, params, retries=5):
        last_exc = None
        for attempt in range(retries):
            self._throttle()
            headers = {
                "content-type": "application/json",
                "authorization": f"Bearer {self.token}",
                "appkey": self.app_key,
                "appsecret": self.app_secret,
                "tr_id": tr_id
            }
            try:
                r = self.session.get(f"{KIS_BASE}{path}", headers=headers, params=params, timeout=30)
                if r.status_code == 200:
                    data = r.json()
                    if data.get("rt_cd") == "0":
                        return data
                    if data.get("msg_cd") == "EGW00201":  # rate limit
                        time.sleep(2.0)
                        continue
                    return data
                else:
                    time.sleep(1.0 + attempt)
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout, requests.exceptions.ChunkedEncodingError) as e:
                last_exc = e
                # Session 재생성
                self.session = requests.Session()
                time.sleep(2.0 + attempt * 2)
                continue
        if last_exc:
            print(f"[WARN] {path} {params.get('FID_INPUT_ISCD','?')}: {last_exc}")
        return None

    def get_ohlcv(self, ticker, start_date, end_date):
        """일별 OHLCV. start/end = YYYYMMDD"""
        data = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            "FHKST03010100",
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": ticker,
                "FID_INPUT_DATE_1": start_date,
                "FID_INPUT_DATE_2": end_date,
                "FID_PERIOD_DIV_CODE": "D",
                "FID_ORG_ADJ_PRC": "0"
            }
        )
        if data is None or "output2" not in data:
            return []
        rows = []
        for o in data["output2"]:
            d = o.get("stck_bsop_date","")
            if not d: continue
            try:
                rows.append({
                    "date": pd.to_datetime(d, format="%Y%m%d"),
                    "ticker": ticker,
                    "시가": float(o.get("stck_oprc",0) or 0),
                    "고가": float(o.get("stck_hgpr",0) or 0),
                    "저가": float(o.get("stck_lwpr",0) or 0),
                    "종가": float(o.get("stck_clpr",0) or 0),
                    "거래량": float(o.get("acml_vol",0) or 0),
                    "등락률": float(o.get("prdy_vrss",0) or 0) / max(float(o.get("stck_clpr",1) or 1) - float(o.get("prdy_vrss",0) or 0), 1) * 100 if float(o.get("prdy_vrss",0) or 0) != 0 else 0,
                })
            except (ValueError, TypeError):
                continue
        return rows

    def get_investor(self, ticker):
        """외인/기관/개인 매매동향 (최근 N일)"""
        data = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-investor",
            "FHKST01010900",
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": ticker
            }
        )
        if data is None or "output" not in data:
            return []
        rows = []
        for o in data["output"]:
            d = o.get("stck_bsop_date","")
            if not d: continue
            try:
                rows.append({
                    "date": pd.to_datetime(d, format="%Y%m%d"),
                    "ticker": ticker,
                    "외국인합계": float(o.get("frgn_ntby_tr_pbmn",0) or 0) * 1e6,  # KIS = 백만원 단위로 추정, 확인 필요
                    "기관합계": float(o.get("orgn_ntby_tr_pbmn",0) or 0) * 1e6,
                    "개인": float(o.get("prsn_ntby_tr_pbmn",0) or 0) * 1e6,
                })
            except (ValueError, TypeError):
                continue
        return rows

    def check_safety(self, ticker):
        """종목 위험 상태 확인. 위험 = True, 안전 = False"""
        data = self._get(
            "/uapi/domestic-stock/v1/quotations/inquire-price",
            "FHKST01010100",
            {"FID_COND_MRKT_DIV_CODE":"J","FID_INPUT_ISCD":ticker}
        )
        if data is None or "output" not in data:
            return True, "API 응답 없음"
        o = data["output"]
        warnings = []
        if o.get("temp_stop_yn") == "Y": warnings.append("거래정지")
        if o.get("mang_issu_cls_code") == "Y": warnings.append("관리종목")
        if o.get("invt_caful_yn") == "Y": warnings.append("투자주의")
        if o.get("sltr_yn") == "Y": warnings.append("정리매매")
        if o.get("mrkt_warn_cls_code","00") != "00": warnings.append(f"시장경고({o.get('mrkt_warn_cls_code')})")
        if o.get("short_over_yn") == "Y": warnings.append("공매도과열")
        if warnings:
            return True, ", ".join(warnings)
        return False, "안전"

    def get_short(self, ticker, start_date, end_date):
        """공매도 일별"""
        data = self._get(
            "/uapi/domestic-stock/v1/quotations/daily-short-sale",
            "FHPST04830000",
            {
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": ticker,
                "FID_INPUT_DATE_1": start_date,
                "FID_INPUT_DATE_2": end_date
            }
        )
        if data is None or "output2" not in data:
            return []
        rows = []
        for o in data["output2"]:
            d = o.get("stck_bsop_date","")
            if not d: continue
            try:
                rows.append({
                    "date": pd.to_datetime(d, format="%Y%m%d"),
                    "ticker": ticker,
                    "공매도": float(o.get("ssts_cntg_qty",0) or 0),
                    "비중": float(o.get("ssts_vol_rlim",0) or 0),
                })
            except (ValueError, TypeError):
                continue
        return rows

# ===== 데이터 fetch + append =====
def fetch_and_append():
    universe = pd.read_parquet(UNIVERSE_PATH)
    ohlcv_old = pd.read_parquet(OHLCV_PATH)
    flow_old  = pd.read_parquet(FLOW_PATH)
    short_old = pd.read_parquet(SHORT_PATH)

    last_ohlcv = pd.to_datetime(ohlcv_old["date"]).max()
    last_flow  = pd.to_datetime(flow_old["date"]).max()
    last_short = pd.to_datetime(short_old["date"]).max()
    last_date = min(last_ohlcv, last_flow, last_short)
    today = pd.Timestamp.now(tz="Asia/Seoul").normalize().tz_localize(None)
    print(f"last_ohlcv={last_ohlcv.date()}, last_flow={last_flow.date()}, last_short={last_short.date()}")
    print(f"target today={today.date()}")

    if last_date >= today:
        print("이미 최신, fetch 스킵")
        return ohlcv_old, flow_old, short_old, universe

    start_date = (last_date + pd.Timedelta(days=1)).strftime("%Y%m%d")
    end_date = today.strftime("%Y%m%d")
    print(f"Fetch 범위: {start_date} ~ {end_date}")

    kis = KisClient()
    tickers = universe["ticker"].tolist()
    print(f"총 {len(tickers)} 종목, KIS API 호출 시작 (초당 {KIS_RPS})")

    new_ohlcv, new_flow, new_short = [], [], []
    target_dates = pd.date_range(start_date, end_date, freq="D")
    target_date_set = set(target_dates.normalize())

    for i, tkr in enumerate(tickers):
        # OHLCV
        rows = kis.get_ohlcv(tkr, start_date, end_date)
        for r in rows:
            if r["date"].normalize() in target_date_set:
                new_ohlcv.append(r)
        # Flow (외인/기관)
        rows = kis.get_investor(tkr)
        for r in rows:
            if r["date"].normalize() in target_date_set:
                new_flow.append(r)
        # Short
        rows = kis.get_short(tkr, start_date, end_date)
        for r in rows:
            if r["date"].normalize() in target_date_set:
                new_short.append(r)

        if (i+1) % 50 == 0:
            print(f"  {i+1}/{len(tickers)}")

    def merge_save(old, new_rows, path):
        if not new_rows:
            return old
        new_df = pd.DataFrame(new_rows)
        new_df["date"] = pd.to_datetime(new_df["date"])
        old["date"] = pd.to_datetime(old["date"])
        merged = pd.concat([old, new_df], ignore_index=True)
        merged = merged.drop_duplicates(subset=["date","ticker"], keep="last")
        merged = merged.sort_values(["ticker","date"]).reset_index(drop=True)
        merged.to_parquet(path, index=False)
        return merged

    ohlcv = merge_save(ohlcv_old, new_ohlcv, OHLCV_PATH)
    flow  = merge_save(flow_old, new_flow, FLOW_PATH)
    short = merge_save(short_old, new_short, SHORT_PATH)
    
    # universe.name으로 결측 name 채움 (KIS fetch에서 name 누락)
    name_map = dict(zip(universe["ticker"], universe["name"]))
    for d, p in [(ohlcv, OHLCV_PATH), (flow, FLOW_PATH), (short, SHORT_PATH)]:
        if d["name"].isna().any():
            d["name"] = d["name"].fillna(d["ticker"].map(name_map))
            d.to_parquet(p, index=False)
    
    return ohlcv, flow, short, universe

# ===== Feature 계산 (기존과 동일) =====
def compute_features(ohlcv, flow, short):
    df = ohlcv.merge(flow[["date","ticker","외국인합계","기관합계","개인"]],
                     on=["date","ticker"], how="inner")
    df = df.merge(short[["date","ticker","공매도","short_ratio"]],
                  on=["date","ticker"], how="left")
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

    # ticker 보존을 위해 reset_index 후 처리
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
    return df

def add_cap_class(df, universe):
    univ_sorted = universe.sort_values("시가총액", ascending=False).reset_index(drop=True)
    n = len(univ_sorted)
    univ_sorted["cap_class"] = pd.cut(univ_sorted.index, bins=[-1, n//3, 2*n//3, n], labels=["대형","중형","소형"])
    cap_map = dict(zip(univ_sorted["ticker"], univ_sorted["cap_class"]))
    df["cap_class"] = df["ticker"].map(cap_map)
    name_map = dict(zip(universe["ticker"], universe["name"]))
    df["name"] = df["ticker"].map(name_map)
    return df

def select_signals(df, target_date):
    today_df = df[df["date"] == pd.to_datetime(target_date)]
    today_df = today_df[today_df["n_signals"] >= 3]
    today_df = today_df.dropna(subset=["거래대금_20ma"])
    today_df = today_df[today_df["거래대금_20ma"] >= LIQ_TH]
    if len(today_df) == 0:
        return []
    top = today_df.nlargest(min(5, len(today_df)), "signal_score")
    return top.to_dict("records")

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

def update_holdings(holdings, df, target_date, signals, trade_dates):
    closed, open_h = [], []
    target_d = pd.to_datetime(target_date)
    # trade_dates = 정렬된 거래일 list
    date_idx = {d: i for i, d in enumerate(trade_dates)}
    
    for h in holdings:
        entry_date = pd.to_datetime(h["entry_date"])
        # T+1 = entry_date 다음 거래일에 매수 → +20영업일 후 종가 매도
        # 즉, entry_date 거래일 인덱스 + 21 = 종결일 (T+1 매수 + 20일 보유)
        if entry_date in date_idx and target_d in date_idx:
            entry_i = date_idx[entry_date]
            target_i = date_idx[target_d]
            days_held = target_i - entry_i  # 영업일 카운트
        else:
            days_held = (target_d - entry_date).days  # fallback
        
        if days_held >= 21:  # T+1 매수 후 T+20 종가 도달 = entry_date에서 21영업일
            closed.append(h)
        else:
            row = df[(df["ticker"]==h["ticker"]) & (df["date"]==target_d)]
            if len(row) > 0:
                cur_price = float(row["종가"].iloc[0])
                h["current_price"] = cur_price
                h["current_ret"] = cur_price / h["entry_price"] - 1 if h.get("entry_price") else None
                h["days_held"] = days_held
                h["bdays_remaining"] = max(0, 21 - days_held)
            open_h.append(h)

    n_open = len(open_h)
    can_add = K_MAX - n_open
    new_added = []
    if can_add > 0 and len(signals) >= N_PICK_MIN:
        for s in signals[:can_add]:
            new_added.append({
                "entry_date": str(target_date),
                "ticker": s["ticker"],
                "name": s.get("name","?"),
                "signal_score": float(s["signal_score"]),
                "n_signals": int(s["n_signals"]),
                "entry_price": None,  # T+1 시가에 결정됨
                "exit_date_planned": str(trade_dates[min(date_idx[target_d] + 21, len(trade_dates)-1)].date()) if target_d in date_idx else str((target_d + pd.Timedelta(days=28)).date()),
                "weight": MAX_WEIGHT,
                "current_price": None,
                "current_ret": None,
                "days_held": 0,
            })

    return open_h + new_added, closed, new_added

def format_message(target_date, signals, holdings_after, closed, new_added, n_pick_valid):
    lines = [f"📊 {REPO_LABEL}", f"{target_date} (20:00 산출)", ""]

    if len(signals) > 0:
        lines.append(f"[매수 후보] {len(signals)}개")
        for i, s in enumerate(signals, 1):
            sigs = [k for k in ["SIG1","SIG2","SIG3","SIG4","SIG5","SIG6","SIG7"] if s[k]==1]
            lines.append(f"{i}. [{s['ticker']}] {s.get('name','?')}")
            lines.append(f"   score: {s['signal_score']:.2f} | n: {s['n_signals']} | {s.get('cap_class','?')}")
            lines.append(f"   활성: {' '.join(sigs)}")
            lines.append(f"   vol_surge: {s['vol_surge']:.2f} | frgn_z: {s['frgn_z']:+.2f}")
            lines.append(f"   거래대금_20ma: {s['거래대금_20ma']/1e8:.0f}억")
        lines.append("")
        if n_pick_valid:
            lines.append(f"n_pick: {len(signals)}/5 → 매매 권고")
        else:
            lines.append(f"n_pick: {len(signals)}/5 → 매매 보류")
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

def main():
    SIG_DIR.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)

    target_date_str = pd.Timestamp.now(tz="Asia/Seoul").strftime("%Y-%m-%d")
    target_date = pd.to_datetime(target_date_str)
    print(f"=== 실행: {target_date_str} ===")

    print("\n[1] KIS 데이터 fetch...")
    ohlcv, flow, short, universe = fetch_and_append()

    print("\n[2] Feature 계산...")
    df = compute_features(ohlcv, flow, short)
    df = add_cap_class(df, universe)

    print(f"\n[3] 신호 산출 ({target_date_str})...")
    available_dates = sorted(df["date"].unique())
    if target_date not in available_dates:
        signal_date = available_dates[-1]
        print(f"오늘 데이터 없음, 직전: {signal_date}")
    else:
        signal_date = target_date

    signals = select_signals(df, signal_date)
    print(f"신호 산출: {len(signals)} 종목")

    # 위험 종목 필터링 (신호 발생 종목만 추가 검증)
    if signals:
        print("위험 종목 검증...")
        kis_check = KisClient()
        safe_signals = []
        for s in signals:
            is_risky, reason = kis_check.check_safety(s["ticker"])
            if is_risky:
                print(f"  ⚠️ [{s['ticker']}] {s.get('name','?')} 제외: {reason}")
            else:
                safe_signals.append(s)
        signals = safe_signals
        print(f"위험 필터 후: {len(signals)} 종목")

    n_pick_valid = len(signals) >= N_PICK_MIN
    print(f"신호: {len(signals)} (valid={n_pick_valid})")

    print("\n[4] 보유 추적...")
    holdings = load_holdings()
    new_for_entry = signals if n_pick_valid else []
    trade_dates_list = sorted(df["date"].unique())
    holdings_after, closed, new_added = update_holdings(holdings, df, signal_date, new_for_entry, trade_dates_list)
    save_holdings(holdings_after)

    if closed:
        results = load_results()
        for c in closed:
            results.append(c)
        save_results(results)

    sig_path = SIG_DIR / f"{target_date_str}.json"
    sig_path.write_text(json.dumps({
        "date": target_date_str,
        "signal_date": str(signal_date),
        "signals": [{k:(str(v) if isinstance(v,(pd.Timestamp,np.generic)) else v) for k,v in s.items() if k not in ["high_20d","high_60d","hl_range"]} for s in signals],
        "n_pick_valid": n_pick_valid,
        "n_open_after": len(holdings_after),
        "n_closed": len(closed),
        "n_new": len(new_added),
    }, indent=2, ensure_ascii=False, default=str))
    print(f"저장: {sig_path}")

    print("\n[5] Telegram...")
    msg = format_message(target_date_str, signals, holdings_after, closed, new_added, n_pick_valid)
    send_telegram(msg)
    print(msg)
    print("\n=== 완료 ===")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        tb = traceback.format_exc()
        err_msg = f"❌ {REPO_LABEL} 실패\n\n{e}\n\n{tb[:2000]}"
        print(err_msg)
        send_telegram(err_msg)
        sys.exit(1)
