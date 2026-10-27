"""
New5 밤사이 모의 시험 (계획서 New5_overnight_paper_plan_v3.md)

규칙(고정): KRX 일봉 거래대금(종가×거래량) 20거래일 평균(거래 없는 날 0) 상위 20종목,
매수일 종가 동시호가 매수 → 다음 거래일 시초가 매도, 같은 금액, 왕복 비용 0.203%.
순위는 매수일 전날까지 자료만. 다음 거래일이 월요일인 날은 '그림자'(판정 제외 기록),
수능 전날·당일(11-18, 11-19)은 매수 없음. 매수일 상한가(+29.5% 이상) 종목·매도일 시가 없는 종목·
|밤사이| ≥ 30%(권리 변경) 종목은 그날 평균에서 제외하고 표시.
판정: 정식 매수일 10-26~12-03(22회) 하루 순수익 평균 ≥ +0.1% 그리고 6주 중 4주 이상 플러스.

자료: overnight_paper/krx_daily.parquet (첫 실행 전 워크플로가 minute-data 브랜치에서 복사).
기준일: 실행 시각이 KST 21시 전이면 전날(10-28 추가, 예약 실행이 새벽으로 밀려도 같은 결과).
매 실행: 빠진 날짜 KRX 일봉 보충 → 지난 매수 정산 → 매수 기록(빠진 날 포함) → 다음 매수일 명단 → 텔레그램.

ETF 밤사이 모의(10-27 추가): KODEX 200·ACE 200, 매수일 종가 → 다음 거래일 시가, 왕복 비용 0.006%(거래세 없음).
다음 거래일이 월요일이면 쉼. 10-26 매수분부터. 가격은 KRX 날짜별 전 ETF 조회(정규장 값)를
overnight_paper/etf_daily.csv에 저장. ETF 부분이 실패해도 주식 모의는 그대로 진행(경고 한 줄).
"""
import os
import sys
import time
import datetime as dt
from pathlib import Path

import numpy as np
import pandas as pd
import requests

KST = dt.timezone(dt.timedelta(hours=9))
BASE = Path(os.environ.get("OP_DIR", "overnight_paper"))
KRX_PATH = BASE / "krx_daily.parquet"
TRADES_PATH = BASE / "trades.csv"
COST, TOPN, WIN = 0.00203, 20, 20
LIMIT_UP, CA_ABS = 0.295, 0.30
TEST_START, TEST_END, TEST_N = "20261026", "20261203", 22
NO_BUY = {"20261118", "20261119"}                       # 수능 전날·당일
FUTURE_HOLIDAYS = {"20261225", "20261231", "20270101"}  # 자료에 아직 없는 날의 다음 거래일 판단용
DATA_READY_HOUR_KST = 21  # 10-28: GitHub 예약 실행이 새벽(03시대 KST)으로 밀림 → 이 시각 전이면 '전날'을 오늘로 봄
                          # (v8.6 signal_runner.py DATA_READY_HOUR_KST 방식과 같음. 21 = 애프터마켓 20:00 마감 뒤, 예약 21:22)
RETRY_TODAY, RETRY_WAIT = int(os.environ.get("RETRY_TODAY", "6")), int(os.environ.get("RETRY_WAIT", "600"))
ETF_TICKERS = {"069500": "KODEX 200", "105190": "ACE 200"}
ETF_START, ETF_COST = "20261026", 0.00006
ETF_PATH = BASE / "etf_daily.csv"
COLS = ["buy_date", "ticker", "rank", "kind", "buy_close", "status", "sell_date", "sell_open", "ret"]


def now_kst():
    return dt.datetime.now(KST)


def data_day():
    """자료 기준일(KST). DATA_READY_HOUR_KST 전이면 전날, 이후면 당일."""
    n = now_kst()
    return n.date() if n.hour >= DATA_READY_HOUR_KST else n.date() - dt.timedelta(days=1)


def log(msg):
    print(msg, flush=True)


def send_telegram(text):
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        log("[WARN] Telegram 환경변수 없음, 알림 생략\n" + text)
        return
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          data={"chat_id": chat, "text": text}, timeout=30)
        if r.status_code != 200:
            log(f"[WARN] Telegram 실패 {r.status_code} {r.text[:200]}")
    except requests.exceptions.RequestException as e:
        log(f"[WARN] Telegram 오류 {e}")


# ---------------- KRX 일봉 ----------------
def krx_one(ds, market):
    """minute_collect.py의 krx_one과 같은 방식."""
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


def krx_day(ds):
    fr = [x for x in (krx_one(ds, m) for m in ("KOSPI", "KOSDAQ")) if x is not None]
    return pd.concat(fr, ignore_index=True) if len(fr) == 2 else None


def update_krx(k, today):
    """마지막 저장일 다음 날부터 오늘까지 빠진 평일을 보충. 오늘 자료가 아직 없으면 재시도."""
    last = max(k["date"])
    d = dt.datetime.strptime(last, "%Y%m%d").date() + dt.timedelta(days=1)
    added, today_missing = [], False
    while d <= today:
        ds = d.strftime("%Y%m%d")
        if d.weekday() < 5:
            x = krx_day(ds)
            if x is None and d == today and ds not in FUTURE_HOLIDAYS:
                for i in range(RETRY_TODAY):
                    log(f"[INFO] {ds} KRX 자료 아직 없음 → {RETRY_WAIT}초 뒤 재시도 ({i + 1}/{RETRY_TODAY})")
                    time.sleep(RETRY_WAIT)
                    x = krx_day(ds)
                    if x is not None:
                        break
                today_missing = x is None
            if x is not None:
                k = pd.concat([k, x], ignore_index=True)
                added.append(ds)
        d += dt.timedelta(days=1)
    k = k.drop_duplicates(["date", "ticker"], keep="last").sort_values(["date", "ticker"]).reset_index(drop=True)
    return k, added, today_missing


# ---------------- 규칙 ----------------
def pivots(k):
    cal = sorted(k["date"].unique())
    p = lambda c: k.pivot_table(index="date", columns="ticker", values=c, aggfunc="last").reindex(cal).astype(float)
    op, cl, vol = p("시가"), p("종가"), p("거래량")
    tv20 = (cl * vol).fillna(0).rolling(WIN, min_periods=WIN).mean()
    return cal, op, cl, tv20


def top_list(tv20, asof):
    """asof 날짜까지 자료로 만든 상위 20(같은 값이면 종목코드 순 = c7 rank method='first'와 같음)."""
    r = tv20.loc[asof].rank(ascending=False, method="first")
    r = r[r <= TOPN].sort_values()
    return [(t, int(v)) for t, v in r.items()]


def next_trading(ds, cal):
    later = [d for d in cal if d > ds]
    if later:
        return later[0]
    d = dt.datetime.strptime(ds, "%Y%m%d").date() + dt.timedelta(days=1)
    while d.weekday() >= 5 or d.strftime("%Y%m%d") in FUTURE_HOLIDAYS:
        d += dt.timedelta(days=1)
    return d.strftime("%Y%m%d")


def kind_of(ds, cal):
    if ds in NO_BUY:
        return "매수없음"
    nd = dt.datetime.strptime(next_trading(ds, cal), "%Y%m%d").date()
    return "그림자" if nd.weekday() == 0 else "정식"


def load_trades():
    if TRADES_PATH.exists():
        t = pd.read_csv(TRADES_PATH, dtype={"buy_date": str, "ticker": str, "sell_date": str, "kind": str, "status": str})
        return t[COLS]
    return pd.DataFrame(columns=COLS)


def record_buys(tr, cal, cl, tv20):
    """정식 시작일 이후 아직 기록 안 된 모든 거래일의 매수 기록(실패한 날도 다음 실행에서 메움)."""
    done = set(tr["buy_date"])
    new = []
    for i, d in enumerate(cal):
        if d < TEST_START or d in done or i == 0:
            continue
        kd = kind_of(d, cal)
        if kd == "매수없음":
            continue
        prev = cal[i - 1]
        for t, rk in top_list(tv20, prev):
            c, c0 = cl.at[d, t], cl.at[prev, t]
            if not (c > 0):
                st = "종가없음"
            elif c0 > 0 and c / c0 - 1 >= LIMIT_UP:
                st = "상한가제외"
            else:
                st = "보유"
            new.append({"buy_date": d, "ticker": t, "rank": rk, "kind": kd, "buy_close": c, "status": st,
                        "sell_date": "", "sell_open": np.nan, "ret": np.nan})
    if new:
        tr = pd.concat([tr, pd.DataFrame(new)], ignore_index=True)
    return tr, sorted({r["buy_date"] for r in new})


def settle(tr, cal, op):
    for i in tr.index[tr["status"] == "보유"]:
        b = tr.at[i, "buy_date"]
        later = [d for d in cal if d > b]
        if not later:
            continue
        s, t = later[0], tr.at[i, "ticker"]
        o = op.at[s, t] if t in op.columns else np.nan
        tr.at[i, "sell_date"] = s
        if not (o > 0):
            tr.at[i, "status"] = "시가없음"
            continue
        r = o / tr.at[i, "buy_close"] - 1
        tr.at[i, "sell_open"] = o
        tr.at[i, "ret"] = r
        tr.at[i, "status"] = "권리변경제외" if abs(r) >= CA_ABS else "완료"
    return tr


def daily_table(tr):
    x = tr[tr["status"] == "완료"]
    if not len(x):
        return pd.DataFrame(columns=["buy_date", "kind", "sell_date", "n", "up", "net"])
    g = x.groupby(["buy_date", "kind", "sell_date"])["ret"]
    d = pd.DataFrame({"n": g.size(), "up": g.apply(lambda s: int((s > 0).sum())), "net": g.mean() - COST}).reset_index()
    pend = tr[tr["status"] == "보유"]["buy_date"].unique()
    return d[~d["buy_date"].isin(pend)].sort_values("buy_date").reset_index(drop=True)   # 정산 다 끝난 날만


def judge(dly):
    o = dly[(dly["kind"] == "정식") & (dly["buy_date"] >= TEST_START) & (dly["buy_date"] <= TEST_END)].copy()
    if not len(o):
        return o, None
    o["wk"] = pd.to_datetime(o["buy_date"]).dt.strftime("%G-%V")
    wk = o.groupby("wk")["net"].apply(lambda s: (1 + s).prod() - 1)
    res = {"n": len(o), "mean": o["net"].mean(), "cum": (1 + o["net"]).prod() - 1,
           "wk_plus": int((wk > 0).sum()), "wk_n": len(wk), "wk": wk}
    return o, res


def fmt_pct(v, nd=2):
    return f"{v:+.{nd}%}"


# ---------------- ETF 밤사이 ----------------
def update_etf(cal):
    """ETF_START 이후 주식 달력의 거래일 중 etf_daily.csv에 없는 날을 KRX 날짜별 전 ETF 조회로 보충."""
    from pykrx import stock
    if ETF_PATH.exists():
        e = pd.read_csv(ETF_PATH, dtype={"date": str, "ticker": str})
    else:
        e = pd.DataFrame(columns=["date", "ticker", "시가", "종가"])
    have = set(e["date"])
    rows = []
    for ds in [d for d in cal if d >= ETF_START and d not in have]:
        last = None
        for k in range(3):
            try:
                x = stock.get_etf_ohlcv_by_ticker(ds)
                break
            except Exception as ex:
                last = ex
                time.sleep(2 + 2 * k)
        else:
            raise RuntimeError(f"ETF 조회 실패 {ds}: {type(last).__name__}: {str(last)[:150]}")
        for t in ETF_TICKERS:
            if x is not None and t in x.index:
                o, c = float(x.at[t, "시가"]), float(x.at[t, "종가"])
                if o > 0 and c > 0:
                    rows.append({"date": ds, "ticker": t, "시가": o, "종가": c})
    if rows:
        e = pd.concat([e, pd.DataFrame(rows)], ignore_index=True)
    e = e.drop_duplicates(["date", "ticker"], keep="last").sort_values(["date", "ticker"]).reset_index(drop=True)
    e.to_csv(ETF_PATH, index=False)
    return e


def etf_nights(e, cal):
    """ETF별 (매수일, 매도일, 순수익) 목록. 다음 거래일이 월요일인 밤은 쉼."""
    out = {}
    for t in ETF_TICKERS:
        s = e[e["ticker"] == t].set_index("date")
        rs = []
        for i, d in enumerate(cal[:-1]):
            nd = cal[i + 1]
            if d < ETF_START or dt.datetime.strptime(nd, "%Y%m%d").weekday() == 0:
                continue
            if d in s.index and nd in s.index:
                rs.append((d, nd, float(s.at[nd, "시가"]) / float(s.at[d, "종가"]) - 1 - ETF_COST))
        out[t] = rs
    return out


def etf_lines(nights, cal):
    L = ["\n📈 ETF 밤사이 모의 (종가 매수 → 다음날 시가 매도, 비용 0.006%)"]
    for t, nm in ETF_TICKERS.items():
        rs = nights.get(t, [])
        if not rs:
            L.append(f"  {nm}: 아직 정산 없음")
            continue
        eq = np.concatenate([[1.0], np.cumprod([1 + r for _, _, r in rs])])
        mdd = (eq / np.maximum.accumulate(eq) - 1).min()
        b, sd, r = rs[-1]
        last = f"{b[4:6]}-{b[6:]}→{sd[4:6]}-{sd[6:]} {fmt_pct(r)}" if sd == cal[-1] else "오늘 정산 없음(쉼)"
        L.append(f"  {nm}: {last} · 누적 {len(rs)}밤 {fmt_pct(eq[-1] - 1)} · MDD {mdd:.2%}")
    return L


def names_of(tks):
    try:
        from pykrx import stock
        return {t: stock.get_market_ticker_name(t) for t in tks}
    except Exception as e:
        log(f"[WARN] 종목명 조회 실패: {e}")
        return {}


def main():
    today = (dt.datetime.strptime(os.environ["OP_TODAY"], "%Y%m%d").date()
             if os.environ.get("OP_TODAY") else data_day())
    log(f"[INFO] 실행 {now_kst():%Y-%m-%d %H:%M} KST · 자료 기준일 {today:%Y-%m-%d} ({DATA_READY_HOUR_KST}시 전이면 전날)")
    if not KRX_PATH.exists():
        raise SystemExit(f"{KRX_PATH} 없음 (워크플로 복사 단계 확인)")
    k = pd.read_parquet(KRX_PATH)
    k["ticker"] = k["ticker"].astype(str).str.zfill(6)
    k["date"] = k["date"].astype(str).str.replace("-", "").str[:8]
    k, added, today_missing = update_krx(k, today)
    k.to_parquet(KRX_PATH)
    cal, op, cl, tv20 = pivots(k)
    log(f"[INFO] KRX 일봉 {cal[0]}~{cal[-1]} · 이번 보충 {added or '없음'}")

    tr = load_trades()
    tr = settle(tr, cal, op)
    tr, newbuys = record_buys(tr, cal, cl, tv20)
    tr = settle(tr, cal, op)
    tr.to_csv(TRADES_PATH, index=False)
    dly = daily_table(tr)
    dly.to_csv(BASE / "daily.csv", index=False)

    lines = [f"🌙 밤사이 모의 시험 · {today:%m-%d} 실행"]
    if today_missing:
        lines.append(f"⚠️ {today:%Y%m%d} KRX 자료를 받지 못함 → 오늘 매수 기록·내일 명단은 다음 실행에서 보충. 수동 재실행 권장")
    newly = dly[dly["sell_date"] == cal[-1]] if len(dly) else dly          # 가장 최근 거래일에 판 결과(재실행해도 표시)
    for _, r in newly.iterrows():
        tag = "정식" if r["kind"] == "정식" else "그림자(주말 넘김·판정 제외)"
        lines.append(f"[{tag}] {r['buy_date'][4:6]}-{r['buy_date'][6:]} 매수 → {r['sell_date'][4:6]}-{r['sell_date'][6:]} 시가 매도: "
                     f"{fmt_pct(r['net'])} ({int(r['n'])}종목, 오른 {int(r['up'])})")
    ex = tr[(tr["sell_date"] == cal[-1]) & tr["status"].isin(["시가없음", "권리변경제외"])]
    for _, r in ex.iterrows():
        lines.append(f"  제외 {r['ticker']} ({r['status']})")
    o, res = judge(dly)
    if res:
        lines.append(f"판정 진행 {res['n']}/{TEST_N}회 · 하루 평균 {fmt_pct(res['mean'], 3)} · 복리 {fmt_pct(res['cum'])} · "
                     f"플러스 주 {res['wk_plus']}/{res['wk_n']}")
        if res["n"] >= TEST_N:
            ok = res["mean"] >= 0.001 and res["wk_plus"] >= 4
            lines.append(f"■ 판정: {'합격' if ok else '불합격'} (기준: 평균 ≥ +0.100%, 플러스 주 ≥ 4/6)")
    sh = dly[dly["kind"] == "그림자"]
    if len(sh):
        lines.append(f"그림자 누적 {len(sh)}회 · 평균 {fmt_pct(sh['net'].mean(), 3)}")
    try:
        lines += etf_lines(etf_nights(update_etf(cal), cal), cal)
        if not today_missing:
            nb = next_trading(cal[-1], cal)
            nd = dt.datetime.strptime(next_trading(nb, cal), "%Y%m%d").date()
            lines.append(f"  {nb[4:6]}-{nb[6:]}: " + ("쉼 (다음 거래일 월요일)" if nd.weekday() == 0 else "종가 동시호가 매수"))
    except Exception as ex:
        log(f"[WARN] ETF 부분 실패: {type(ex).__name__}: {ex}")
        lines.append(f"\n⚠️ ETF 모의 실패({type(ex).__name__}) → 다음 실행에서 보충")

    if not today_missing:
        nb = next_trading(cal[-1], cal)
        kd = kind_of(nb, cal)
        nb_s = f"{nb[4:6]}-{nb[6:]}"
        if kd == "매수없음":
            lines.append(f"\n{nb_s}: 수능 관련 매수 없음")
        else:
            lst = top_list(tv20, cal[-1])
            nm = names_of([t for t, _ in lst])
            head = "정식 — 종가 동시호가 매수, 다음 거래일 시가 매도" if kd == "정식" else "그림자 — 실제로 사지 않음(다음 거래일 월요일)"
            lines.append(f"\n{nb_s} 매수 명단 [{head}] · 15:20에 상한가 근처면 제외")
            lines += [f"{r:>2}. {t} {nm.get(t, '')}" for t, r in lst]
    send_telegram("\n".join(lines))
    log("\n".join(lines))
    if today_missing:
        sys.exit(1)


if __name__ == "__main__":
    main()
