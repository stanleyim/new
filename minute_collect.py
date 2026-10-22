"""분봉 전 종목 수집 (New5, 2026-10-23 Stanley 지시).

근거: minute_probe 실측(005930) — 분봉 일 합계 시·고·저·종이 KRX와 5/5일 일치, 보관 2025-10-14~(251일),
      호출당 120봉·하루 4회·381봉, 초당 20회에서 200회 중 3회 한도 초과, 단일 스레드 실속도 초당 4.6회.

동작
- 종목·거래일: KRX 공식 일봉(pykrx, KOSPI·KOSDAQ 날짜별)으로 만든다. 거래량 > 0인 날만 조회.
  첫 실행에서 krx_daily.parquet를 만들고, 이후 실행은 빠진 날짜만 보충한다.
- 종목마다 최근 날짜부터 과거로 받는다. KRX에 거래가 있는데 분봉이 빈 날이 연속 5일이면 보관 한도 밖으로 보고 멈춘다.
- 저장: 5분봉 = minute_data/m5/{ticker}.parquet (minute-data 브랜치),
        1분봉 원본 = minute1m_run/{ticker}.parquet (워크플로가 zip으로 묶어 릴리스 minute-1m에 올림).
- 품질 점검: 종목·날짜마다 1분봉 일 합계(≤15:30) 시·고·저·종을 KRX와 비교해 일치·불일치 수를 기록.
- 속도: 스레드 4개, 공용 제한 초당 16회. 정규 실행(Daily Signal Run)이나 Shadow ML Record가 돌고 있으면 초당 8회로 낮춘다
  (같은 KIS 키를 쓰므로 합계 20회를 넘지 않게).
- 시간 예산이 끝나면 진행 중인 종목만 마치고 저장. progress.json으로 이어받는다.
"""
import os
import json
import time
import threading
import subprocess
import datetime as dt
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED

import requests
import pandas as pd

KIS_BASE = "https://openapi.koreainvestment.com:9443"
PATH_MIN = "/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice"
TR_MIN = "FHKST03010230"
KST = dt.timezone(dt.timedelta(hours=9))

RATE_NORMAL = 16.0
RATE_SLOW = 8.0
WORKERS = 4
LOOKBACK_DAYS = 380
STOP_EMPTY_STREAK = 5
MAX_CALLS_PER_DAY = 12
MAX_TICKER_ATTEMPTS = 2
TIME_BUDGET_MIN = float(os.environ.get("TIME_BUDGET_MINUTES", "300"))
TEST_TICKER_LIMIT = (os.environ.get("TEST_TICKER_LIMIT") or "").strip()

EXISTING = Path("existing_minute_data/minute_data")
OUT = Path("minute_data")
RAW1 = Path("minute1m_run")
KRX_PATH = "krx_daily.parquet"
PROG_PATH = "progress.json"
WATCH_WORKFLOWS = {"Daily Signal Run", "Shadow ML Record"}


# ---------------- 공용 ----------------
def now_kst():
    return dt.datetime.now(KST)


def send_telegram(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("[WARN] Telegram 환경변수 없음, 알림 스킵")
        return
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          data={"chat_id": chat_id, "text": text}, timeout=30)
        if r.status_code != 200:
            print(f"[WARN] Telegram 실패: {r.status_code} {r.text[:200]}")
    except requests.exceptions.RequestException as e:
        print(f"[WARN] Telegram 오류: {e}")


def get_kis_token():
    app_key = os.environ.get("KIS_APP_KEY")
    app_secret = os.environ.get("KIS_APP_SECRET")
    if not app_key or not app_secret:
        raise RuntimeError("KIS_APP_KEY/SECRET 환경변수 없음")
    last = None
    for attempt, wait_s in enumerate([30, 60, 120]):
        try:
            r = requests.post(f"{KIS_BASE}/oauth2/tokenP",
                              json={"grant_type": "client_credentials", "appkey": app_key, "appsecret": app_secret},
                              timeout=30)
            if r.status_code == 200 and "access_token" in r.json():
                return r.json()["access_token"], app_key, app_secret
            last = RuntimeError(f"토큰 실패: {r.status_code} {r.text[:200]}")
        except requests.exceptions.RequestException as e:
            last = e
        print(f"[WARN] KIS 토큰 실패 (attempt {attempt + 1}/3): {last}")
        if attempt < 2:
            time.sleep(wait_s)
    raise RuntimeError(f"KIS 토큰 최종 실패: {last}")


class RateLimiter:
    """전 스레드 공용. rate는 실행 중 바뀔 수 있다."""

    def __init__(self, rate):
        self.rate = rate
        self.lock = threading.Lock()
        self.next_t = 0.0

    def acquire(self):
        with self.lock:
            t = time.time()
            start = max(t, self.next_t)
            self.next_t = start + 1.0 / self.rate
        if start > t:
            time.sleep(start - t)


class Kis:
    def __init__(self, limiter):
        self.token, self.app_key, self.app_secret = get_kis_token()
        self.limiter = limiter
        self.local = threading.local()
        self.lock = threading.Lock()
        self.n_calls = 0
        self.n_ratelimit = 0

    def _session(self):
        if not hasattr(self.local, "s"):
            self.local.s = requests.Session()
        return self.local.s

    def get(self, params):
        headers = {"content-type": "application/json", "authorization": f"Bearer {self.token}",
                   "appkey": self.app_key, "appsecret": self.app_secret, "tr_id": TR_MIN}
        last = None
        for attempt in range(6):
            self.limiter.acquire()
            with self.lock:
                self.n_calls += 1
            try:
                r = self._session().get(f"{KIS_BASE}{PATH_MIN}", headers=headers, params=params, timeout=30)
                d = r.json() if r.content else None
            except (requests.exceptions.RequestException, ValueError) as e:
                self.local.s = requests.Session()
                last = {"msg_cd": "CONN", "msg1": str(e)[:150]}
                time.sleep(1.0 + attempt)
                continue
            if r.status_code == 200 and d and d.get("rt_cd") == "0":
                return d
            last = d or {"msg_cd": f"HTTP{r.status_code}"}
            if (d or {}).get("msg_cd") == "EGW00201":
                with self.lock:
                    self.n_ratelimit += 1
                time.sleep(1.0 + 0.5 * attempt)
                continue
            if r.status_code != 200:
                time.sleep(1.0 + attempt)
                continue
            return d  # 업무 오류는 재시도하지 않음
        return last


def watch_other_runs(limiter, stop_evt):
    """정규 실행·Shadow ML이 돌면 속도를 낮춘다. 조회 실패 시 현재 속도 유지."""
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo or not os.environ.get("GH_TOKEN"):
        print("[WARN] GH_TOKEN/GITHUB_REPOSITORY 없음 — 속도 조절 감시 끔(초당 낮은 값으로 고정)")
        limiter.rate = RATE_SLOW
        return
    while not stop_evt.is_set():
        busy = None
        try:
            out = subprocess.run(
                ["gh", "api", f"repos/{repo}/actions/runs?status=in_progress&per_page=50", "--jq", ".workflow_runs[].name"],
                capture_output=True, text=True, timeout=30)
            if out.returncode == 0:
                names = set(x.strip() for x in out.stdout.splitlines() if x.strip())
                busy = bool(names & WATCH_WORKFLOWS)
        except Exception as e:
            print(f"[WARN] 실행 감시 실패: {e}")
        if busy is not None:
            new = RATE_SLOW if busy else RATE_NORMAL
            if new != limiter.rate:
                print(f"[INFO] {now_kst():%H:%M} 속도 {limiter.rate} → {new} (다른 실행 {'있음' if busy else '없음'})")
                limiter.rate = new
        stop_evt.wait(60)


# ---------------- KRX 일봉(달력·종목·검증 기준) ----------------
def krx_one(ds, market):
    from pykrx import stock
    last = None
    for k in range(3):
        try:
            x = stock.get_market_ohlcv(ds, market=market)
            break
        except Exception as e:
            last = e
            time.sleep(2 + 2 * k)
    else:
        raise RuntimeError(f"KRX 조회 실패 {ds} {market}: {type(last).__name__}: {str(last)[:150]}")
    if x is None or len(x) == 0:
        return None
    x = x.reset_index()
    x = x.rename(columns={x.columns[0]: "ticker"})
    x["ticker"] = x["ticker"].astype(str)
    x = x[["ticker", "시가", "고가", "저가", "종가", "거래량"]].copy()
    for c in ["시가", "고가", "저가", "종가", "거래량"]:
        x[c] = pd.to_numeric(x[c], errors="coerce").astype(float)
    if not (x["종가"] > 0).any():
        return None
    x["date"] = ds
    x["market"] = market
    return x


def build_krx_daily(existing):
    end = now_kst().date() - dt.timedelta(days=1)
    start = now_kst().date() - dt.timedelta(days=LOOKBACK_DAYS)
    have = set(existing["date"]) if existing is not None and len(existing) else set()
    frames = [existing] if existing is not None and len(existing) else []
    d, added = start, 0
    while d <= end:
        ds = d.strftime("%Y%m%d")
        if d.weekday() < 5 and ds not in have:
            for mk in ("KOSPI", "KOSDAQ"):
                x = krx_one(ds, mk)
                if x is not None:
                    frames.append(x)
                    added += 1
        d += dt.timedelta(days=1)
    k = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if len(k):
        k = k.drop_duplicates(["date", "ticker"], keep="last").sort_values(["date", "ticker"]).reset_index(drop=True)
    print(f"[INFO] KRX 일봉 {len(k)}행, 이번에 추가 {added}개(날짜×시장)")
    return k


# ---------------- 분봉 ----------------
def min_params(ticker, ds, hour):
    return {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker, "FID_INPUT_HOUR_1": hour,
            "FID_INPUT_DATE_1": ds, "FID_PW_DATA_INCU_YN": "N", "FID_FAKE_TICK_INCU_YN": "N"}


def fetch_day(kis, ticker, ds):
    """하루치 1분봉 (minute_probe와 같은 페이지 방식). 반환 (bars dict, 호출 수, 마지막 오류 코드)."""
    bars, hour, n, err = {}, "180000", 0, None
    for _ in range(MAX_CALLS_PER_DAY):
        d = kis.get(min_params(ticker, ds, hour)) or {}
        n += 1
        if d.get("rt_cd") != "0":
            err = d.get("msg_cd") or "NONE"
        out2 = d.get("output2") or []
        same = [r for r in out2 if r.get("stck_bsop_date") == ds and r.get("stck_cntg_hour")]
        other = len(out2) - len(same)
        new = 0
        for r in same:
            if r["stck_cntg_hour"] not in bars:
                bars[r["stck_cntg_hour"]] = r
                new += 1
        if not same or new == 0 or other > 0:
            break
        earliest = min(r["stck_cntg_hour"] for r in same)
        if earliest <= "090000":
            break
        hour = earliest
    return bars, n, err


def bars_to_df(ds, bars):
    df = pd.DataFrame(bars.values())
    num = lambda c: pd.to_numeric(df[c], errors="coerce") if c in df else pd.Series(float("nan"), index=df.index)
    out = pd.DataFrame({"date": ds, "time": df["stck_cntg_hour"].astype(str),
                        "open": num("stck_oprc"), "high": num("stck_hgpr"), "low": num("stck_lwpr"),
                        "close": num("stck_prpr"), "volume": num("cntg_vol")})
    return out.sort_values("time").reset_index(drop=True)


def to_5min(df):
    hh = df["time"].str[:2].astype(int)
    mm = df["time"].str[2:4].astype(int)
    b = ((hh * 60 + mm) // 5) * 5
    x = df.assign(t5=(b // 60).astype(str).str.zfill(2) + (b % 60).astype(str).str.zfill(2) + "00")
    g = x.groupby(["date", "t5"], sort=True)
    return pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(), "low": g["low"].min(),
                         "close": g["close"].last(), "volume": g["volume"].sum()}).reset_index().rename(columns={"t5": "time"})


def day_check(df, k):
    """1분봉 하루(df)와 KRX 일봉(k) 비교.
    - 정규장 끝: 보통 15:30. 첫 봉이 10:00 이후인 날(수능일 등 1시간 늦춘 날)은 16:30.
    - match: 시·고·저·종 정확히 일치
    - adjusted: 네 값 모두 같은 배율(±1%)로 다름 → KIS 분봉이 수정주가(액면병합·분할 등), KRX는 원주가. 자료 오류 아님
    - mismatch: 그 밖
    (10-23 3종목 시험: 000040 액면병합 5:1로 정확히 ×5인 날 176일, 000050 수능일 종가를 15:30에서 끊어 1일 → 이 규칙으로 수정)"""
    end = "163000" if df["time"].iloc[0] >= "100000" else "153000"
    reg = df[df["time"] <= end]
    if not len(reg):
        return "none"
    m = [reg["open"].iloc[0], reg["high"].max(), reg["low"].min(), reg["close"].iloc[-1]]
    x = [k["시가"], k["고가"], k["저가"], k["종가"]]
    if m == x:
        return "match"
    if all(v and v > 0 for v in x) and all(v == v for v in m):
        r = [a / b for a, b in zip(m, x)]
        med = sorted(r)[1]
        if abs(med - 1) > 0.01 and all(abs(v / med - 1) <= 0.01 for v in r):
            return "adjusted"
    return "mismatch"


def process_ticker(kis, ticker, krx_t):
    """krx_t: 이 종목의 KRX 일봉(거래량>0), 날짜 내림차순."""
    frames, calls, empty_streak, n_empty, errs = [], 0, 0, 0, {}
    match = adjusted = mismatch = 0
    for _, k in krx_t.iterrows():
        ds = k["date"]
        bars, n, err = fetch_day(kis, ticker, ds)
        calls += n
        if err:
            errs[err] = errs.get(err, 0) + 1
        if not bars:
            n_empty += 1
            empty_streak += 1
            if empty_streak >= STOP_EMPTY_STREAK:
                break
            continue
        empty_streak = 0
        df = bars_to_df(ds, bars)
        frames.append(df)
        res = day_check(df, k)
        match += int(res == "match")
        adjusted += int(res == "adjusted")
        mismatch += int(res == "mismatch")
    m1 = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    meta = {"n_days": int(m1["date"].nunique()) if len(m1) else 0,
            "first": str(m1["date"].min()) if len(m1) else None, "last": str(m1["date"].max()) if len(m1) else None,
            "krx_days_tried": int(len(frames) + n_empty), "empty_days": n_empty, "calls": calls,
            "krx_match": match, "krx_adjusted": adjusted, "krx_mismatch": mismatch, "errors": errs}
    return m1, meta


# ---------------- 메인 ----------------
def load_progress():
    p = EXISTING / PROG_PATH
    if p.exists():
        return json.loads(p.read_text())
    return {"done": {}, "failed": {}, "runs": 0, "completed": False}


def main():
    t0 = time.time()
    OUT.mkdir(exist_ok=True)
    (OUT / "m5").mkdir(exist_ok=True)
    RAW1.mkdir(exist_ok=True)
    prog = load_progress()
    prog["runs"] = prog.get("runs", 0) + 1

    old_krx = pd.read_parquet(EXISTING / KRX_PATH) if (EXISTING / KRX_PATH).exists() else None
    krx = build_krx_daily(old_krx)
    krx.to_parquet(OUT / KRX_PATH, index=False)
    krx = krx[krx["거래량"] > 0]

    tickers = sorted(krx["ticker"].unique())
    if TEST_TICKER_LIMIT:
        tickers = tickers[:int(TEST_TICKER_LIMIT)]
    todo = [t for t in tickers if t not in prog["done"]
            and prog.get("failed", {}).get(t, 0) < MAX_TICKER_ATTEMPTS]
    print(f"[INFO] 종목 {len(tickers)}, 완료 {len(prog['done'])}, 이번 대상 {len(todo)}")
    by_t = {t: g.sort_values("date", ascending=False) for t, g in krx.groupby("ticker")}

    limiter = RateLimiter(RATE_NORMAL)
    stop_evt = threading.Event()
    threading.Thread(target=watch_other_runs, args=(limiter, stop_evt), daemon=True).start()
    kis = Kis(limiter)

    run_done, run_mis, run_match, run_adj, run_err = 0, 0, 0, 0, 0
    budget_s = TIME_BUDGET_MIN * 60
    it = iter(todo)
    futs = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        def submit_next():
            if time.time() - t0 > budget_s:
                return False
            t = next(it, None)
            if t is None:
                return False
            futs[ex.submit(process_ticker, kis, t, by_t[t])] = t
            return True

        for _ in range(WORKERS):
            if not submit_next():
                break
        while futs:
            done, _ = wait(list(futs), return_when=FIRST_COMPLETED)
            for f in done:
                t = futs.pop(f)
                try:
                    m1, meta = f.result()
                    if len(m1):
                        m1.to_parquet(RAW1 / f"{t}.parquet", index=False)
                        to_5min(m1).to_parquet(OUT / "m5" / f"{t}.parquet", index=False)
                    prog["done"][t] = meta
                    run_done += 1
                    run_match += meta["krx_match"]
                    run_mis += meta["krx_mismatch"]
                    run_adj += meta["krx_adjusted"]
                except Exception as e:
                    run_err += 1
                    prog.setdefault("failed", {})[t] = prog.get("failed", {}).get(t, 0) + 1
                    print(f"[WARN] {t} 실패: {type(e).__name__}: {str(e)[:200]}")
                (OUT / PROG_PATH).write_text(json.dumps(prog, ensure_ascii=False))
                if run_done % 50 == 0 and run_done:
                    print(f"[INFO] {now_kst():%H:%M} 이번 {run_done}종목, 호출 {kis.n_calls}, 한도초과 {kis.n_ratelimit}")
                submit_next()
    stop_evt.set()

    remaining = [t for t in tickers if t not in prog["done"]
                 and prog.get("failed", {}).get(t, 0) < MAX_TICKER_ATTEMPTS]
    prog["completed"] = len(remaining) == 0
    prog["last_run_done"] = run_done  # 0이면 워크플로가 자동 재실행하지 않음(같은 오류 무한 반복 방지)
    (OUT / PROG_PATH).write_text(json.dumps(prog, ensure_ascii=False))
    mins = (time.time() - t0) / 60
    tot = run_match + run_adj + run_mis
    msg = [f"📥 분봉 수집 실행 #{prog['runs']}",
           f"이번 {run_done}종목 · 누적 {len(prog['done'])}/{len(tickers)} · 남음 {len(remaining)}",
           f"KRX 대조(종목·일): 일치 {run_match} · 수정주가 {run_adj} · 불일치 {run_mis}" + (f" ({run_mis / tot:.2%})" if tot else ""),
           f"호출 {kis.n_calls} · 한도초과 {kis.n_ratelimit} · 종목 실패 {run_err} · {mins:.0f}분",
           "완료" if prog["completed"] else "10분 뒤 자동 이어서 실행"]
    print("\n".join(msg))
    send_telegram("\n".join(msg))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        send_telegram(f"📥 분봉 수집 오류: {type(e).__name__}: {str(e)[:300]}")
        raise
