# [실제 운용 구조 기준선 셀 — signal_runner.py의 보유·편입 규칙(K_MAX=5, 빈자리 있을 때만 편입, 점수순, T+10 청산) 그대로의 일별 자산곡선]
# 선행: 클론 셀(main + pit-data idx) 실행 완료(/content/main/{data,signal_runner.py}, /content/pit/pit_data_extra/idx_*.parquet). 읽기 전용.
# 공용 함수 원문 복사: sig_u_near_cell.py(compute_features·add_signals·add_atoms·check_no_lookahead), gate2.py(perf), g2_hedge.py(load_idx_returns). 수정 없음.
#
# ===== 목적/사전등록 (실행 전 고정, 결과 후 변경 금지) =====
# 목적: 현재 운용 규칙의 실제 MDD·CAGR 기준선 측정. 합격/탈락 판정 없음(측정 전용). 설계구간 2018-01-02~2022-12-30만. 근접구간은 열지 않음.
# 규칙(signal_runner.py 그대로): 매 거래일 D 종가 후 N3(n_signals>=3)&거래대금_20ma>=30억 중 signal_score 상위 5(nlargest) 신호.
#   보유 중(entry_date 이후 영업일 수 <= 10) 종목수를 센 뒤 빈자리(K_MAX=5-보유수)만큼 신호 순서대로 편입(이미 보유 중인 종목 제외).
#   매수 = 편입일 다음 거래일 시가, 매도 = 편입일 +10거래일 종가(왕복 비용 0.206%를 매도 시 1회 차감). 시가 무효(<=0/결측)면 자금 없이 슬롯만 점유(코드 동작과 동일).
# 자금: 종목당 비중 w x (직전 종가 기준 자산). W20 = 1/K_MAX(합계 100%), W25 = MAX_WEIGHT 25%(합계 125%, 차입 비용 0 가정, 환산 참고용).
# 미반영: 실거래 위험 필터(check_safety, KIS 실시간), 헤지, 슬리피지.
# 선행조건(전부 통과해야 결과 출력): ①Feature 미래 비의존 ②signal_runner.compute_features 결과와 내 계산 일치 ③실제 signal_runner.select_signals·update_holdings를 일자별로 돌린 편입 목록과 내 슬롯 로직의 편입 목록 전 건 일치 ④자산 회계 항등식(최종자산=1+Σ(회수-투입))
import os, time, sys, importlib.util
import numpy as np
import pandas as pd

DESIGN_START, DESIGN_END = "2018-01-02", "2022-12-30"
CAL_START = "2017-01-02"
SEG_SPLIT = "2020-01-01"
COST = 0.00206
LIQ_TH = 30e8
HMAX = 10
K_MAX = 5
N_PICK_MIN = 1
TOP_K = 5
WEIGHTS = {"W20": 1.0 / K_MAX, "W25": 0.25}
SIGS = ["SIG1", "SIG2", "SIG3", "SIG4", "SIG5", "SIG6", "SIG7"]
IDX_NAMES = ("KOSPI200", "KOSDAQ")
MAIN_DIR = "/content/main"
PIT_EXTRA = "/content/pit/pit_data_extra"
RUN_SR_CHECK = True


class C:                      # gate2.py가 참조하는 설정(research/config.py 값과 동일)
    G2_COST_RT = 0.00206
    G2_ANN_DAYS = 252


# ===== 원문 복사 =====

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

    # ticker 보존을 위해 reset_index 후 처리
    df = df.groupby("ticker", group_keys=True).apply(per_ticker, include_groups=False).reset_index(level=0)
    return df


def add_signals(df):
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


def add_atoms(df):
    df["A_B20"] = (df["is_breakout_20"] == 1)
    df["A_B60"] = (df["is_breakout_60"] == 1)
    df["A_VS"] = (df["vol_surge"] > 2.0)
    df["A_FZ"] = (df["frgn_z"] > 2.0)
    df["A_SC"] = (df["short_chg"] < -0.5)
    df["A_GAP"] = (df["gap"] < -5.0)
    df["A_VR"] = (df["vol_ratio"] > 1.5)
    df["N3"] = (df["n_signals"] >= 3)
    df["ELIG"] = (df["거래대금_20ma"] >= LIQ_TH)   # NaN -> False
    for c in SIGS:
        df[c] = df[c].astype(bool)
    return df


def check_no_lookahead(ohlcv, flow, short, feats, n_tickers=4, seed=7):
    """과거 구간만으로 재계산한 값이 전체 계산의 같은 날짜 행과 일치(미래 의존 없음)."""
    rng = np.random.default_rng(seed)
    cols = ["vol_surge", "frgn_z", "short_chg", "vol_ratio", "is_breakout_20", "is_breakout_60",
            "거래대금_20ma", "gap"] + SIGS
    tickers = rng.choice(feats["ticker"].unique(), size=min(n_tickers, feats["ticker"].nunique()), replace=False)
    nchk = 0
    for t in tickers:
        ft = feats[feats["ticker"] == t].sort_values("date")
        if len(ft) < 200:
            continue
        for frac in (0.4, 0.6, 0.8):
            d = ft["date"].iloc[int(len(ft) * frac)]
            o = ohlcv[(ohlcv["ticker"] == t) & (ohlcv["date"] <= d)]
            fl = flow[(flow["ticker"] == t) & (flow["date"] <= d)]
            sh = short[(short["ticker"] == t) & (short["date"] <= d)]
            part = add_signals(compute_features(o, fl, sh))
            a = part[part["date"] == d][cols].astype(float).values
            b = ft[ft["date"] == d][cols].astype(float).values
            assert a.shape == b.shape and np.allclose(a, b, equal_nan=True), f"미래 의존 의심: {t} {d.date()}"
            nchk += 1
    return nchk


def perf(ret, start, end):
    """ret[start:end+1] 구간(모든 슬롯 활성 구간) 성과. CAGR·Sharpe(일, √252)·MDD."""
    r = ret[start:end + 1]
    eq = np.cumprod(1.0 + r)
    n = len(r)
    sd = r.std(ddof=1)
    return {"CAGR": float(eq[-1] ** (C.G2_ANN_DAYS / n) - 1.0),
            "Sharpe": float(r.mean() / sd * np.sqrt(C.G2_ANN_DAYS)) if sd > 0 else np.nan,
            "MDD": float((eq / np.maximum.accumulate(eq) - 1.0).min()), "n_days": n}


def load_idx_returns(pit_extra_dir, cal):
    """cal(DatetimeIndex, 마스터 거래일) 위 지수 종가→일수익률 (N×2, 열=IDX_NAMES). 결측·중복·이름 부재는 즉시 중단.
    첫 행은 이전 종가가 없어 0으로 두며(평가에서 미사용 assert), 이후 행은 직전 캘린더일 종가 대비."""
    cal = pd.DatetimeIndex(cal)
    frames = [pd.read_parquet(f"{pit_extra_dir}/idx_{y}.parquet", columns=["date", "index", "종가"])
              for y in range(cal[0].year, cal[-1].year + 1)]
    d = pd.concat(frames, ignore_index=True)
    assert not d.duplicated(["date", "index"]).any(), "idx (date,index) 중복"
    px = d.pivot(index="date", columns="index", values="종가")
    for n in IDX_NAMES:
        assert n in px.columns, f"지수 {n} 없음: {list(px.columns)}"
    px = px[list(IDX_NAMES)].reindex(cal)
    assert px.notna().all().all(), "마스터 거래일 중 지수 종가 결측"
    assert (px.values > 0).all(), "지수 종가 <= 0"
    r = np.array(px.pct_change().values, dtype=float)
    r[0] = 0.0
    return r

# ===== 입력 =====
def build_feats(ohlcv, flow, short, end):
    endt = pd.Timestamp(end)
    ohlcv = ohlcv.assign(date=pd.to_datetime(ohlcv["date"]))
    flow = flow.assign(date=pd.to_datetime(flow["date"]))
    short = short.assign(date=pd.to_datetime(short["date"]))
    ohlcv, flow, short = ohlcv[ohlcv["date"] <= endt], flow[flow["date"] <= endt], short[short["date"] <= endt]
    feats0 = add_atoms(add_signals(compute_features(ohlcv, flow, short)))
    assert feats0["date"].max() <= endt, "구간 이후 데이터 적재됨"
    n_lookahead = check_no_lookahead(ohlcv, flow, short, feats0)
    return ohlcv, flow, short, feats0, n_lookahead


def select_by_day(feats, start, end):
    """일자별 신호 종목(점수 내림차순). signal_runner.select_signals와 동일 규칙: n>=3, 거래대금_20ma>=30억, nlargest(5)."""
    m = feats["N3"] & feats["ELIG"]
    c = feats.loc[m & (feats["date"] >= pd.Timestamp(start)) & (feats["date"] <= pd.Timestamp(end)),
                  ["date", "ticker", "signal_score"]]
    out = {}
    for d, g in c.groupby("date", sort=True):
        out[d] = g.nlargest(TOP_K, "signal_score")["ticker"].tolist()
    return out


def slot_logic(cal, sel, run_idx):
    """update_holdings의 편입 규칙. 반환 entries[(편입일 인덱스, ticker)], 용량으로 건너뛴 신호 수, 일별 보유 종목수."""
    open_pos, entries, skipped = [], [], 0
    n_open_by_day = {}
    for i in run_idx:
        open_pos = [p for p in open_pos if i - p[0] < HMAX + 1]       # days_held >= HOLD_DAYS+1 이면 종결
        sigs = sel.get(cal[i], [])
        can_add = K_MAX - len(open_pos)
        if can_add > 0 and len(sigs) >= N_PICK_MIN:
            ot = {t for _, t in open_pos}
            ded = [t for t in sigs if t not in ot]
            add = ded[:can_add]
            for t in add:
                open_pos.append((i, t)); entries.append((i, t))
            skipped += len(ded) - len(add)
        elif sigs:
            skipped += len(sigs)
        n_open_by_day[i] = len(open_pos)
    return entries, skipped, n_open_by_day


def simulate_equity(O, Clf, col_of, entries, w, N, cost=COST):
    """편입 목록 -> 일별 자산곡선. 매수=E+1 시가(자산 직전 종가 x w), 매도=E+HMAX 종가(x(1-cost))."""
    by_buy = {}
    for E, tk in entries:
        by_buy.setdefault(E + 1, []).append((E, tk))
    first = min(by_buy)
    cash, eq_prev = 1.0, 1.0
    eq = np.ones(N); ret = np.zeros(N); invested = {}; trades = []; no_fill = 0; npos = np.zeros(N, int)
    for t in range(first, N):
        for (E, tk) in by_buy.get(t, []):
            o = O[t, col_of[tk]]
            if np.isfinite(o) and o > 0:
                alloc = w * eq_prev
                invested[(E, tk)] = (alloc / o, alloc)
                cash -= alloc
            else:
                no_fill += 1
        for key in list(invested):
            E, tk = key
            if t == E + HMAX:
                units, alloc = invested.pop(key)
                proceeds = units * Clf[t, col_of[tk]] * (1.0 - cost)
                cash += proceeds
                trades.append((E, tk, proceeds / alloc - 1.0, alloc, proceeds))
        eq_t = cash + sum(u * Clf[t, col_of[k[1]]] for k, (u, _) in invested.items())
        eq[t] = eq_t; ret[t] = eq_t / eq_prev - 1.0; eq_prev = eq_t; npos[t] = len(invested)
    assert not invested, "미청산 포지션 존재"
    return dict(eq=eq, ret=ret, first=first, trades=trades, no_fill=no_fill, npos=npos, cash=cash)


def mdd_anatomy(eq, first, cal, ridx, npos):
    e = eq[first:]
    peak = np.maximum.accumulate(e)
    dd = e / peak - 1.0
    tr = int(dd.argmin()); pk = int(e[:tr + 1].argmax())
    a, b = first + pk, first + tr
    idx_ret = [float(np.prod(1 + ridx[a + 1:b + 1, k]) - 1) for k in range(ridx.shape[1])]
    return {"peak": str(cal[a].date()), "trough": str(cal[b].date()), "MDD": float(dd[tr]),
            "kospi200": idx_ret[0], "kosdaq": idx_ret[1], "npos_at_trough": int(npos[b]),
            "avg_npos_in_dd": float(npos[a:b + 1].mean()), "days": int(b - a)}


# ===== 실제 signal_runner 대조 =====
def load_sr(path):
    spec = importlib.util.spec_from_file_location("signal_runner_live", path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    return m


def check_against_sr(sr, ohlcv, flow, short, feats0, cal_all, run_dates, my_entries_dates, P):
    assert (sr.HOLD_DAYS, sr.K_MAX, sr.N_PICK_MIN, sr.LIQ_TH, sr.TOTAL_COST) == (HMAX, K_MAX, N_PICK_MIN, LIQ_TH, COST), \
        f"signal_runner 상수 불일치: {(sr.HOLD_DAYS, sr.K_MAX, sr.N_PICK_MIN, sr.LIQ_TH, sr.TOTAL_COST)}"
    dfsr = sr.compute_features(ohlcv, flow, short)
    assert dfsr.shape[0] == feats0.shape[0], "행 수 불일치"
    for c in ["n_signals", "signal_score", "거래대금_20ma", "시가", "종가"]:
        a, b = dfsr[c].astype(float).values, feats0[c].astype(float).values
        assert np.allclose(a, b, equal_nan=True, rtol=1e-12, atol=1e-12), f"signal_runner Feature 불일치: {c}"
    P(f"[점검2] signal_runner.compute_features = 내 계산 일치 OK · 행 {len(dfsr):,}")
    trade_dates = sorted(dfsr["date"].unique())
    holdings, sr_entries = [], []
    my_tickers = {t for _, t in my_entries_dates}
    dfu = dfsr[dfsr["ticker"].isin(my_tickers)]
    for D in run_dates:
        sigs = sr.select_signals(dfsr, D)
        valid = len(sigs) >= sr.N_PICK_MIN
        holdings, closed, added = sr.update_holdings(holdings, dfu, D, sigs if valid else [], trade_dates)
        for h in added:
            sr_entries.append((pd.Timestamp(h["entry_date"]), h["ticker"]))
    mine = sorted(my_entries_dates)
    assert sorted(sr_entries) == mine, f"편입 목록 불일치: SR {len(sr_entries)}건 vs 내 {len(mine)}건"
    P(f"[점검3] 실제 select_signals·update_holdings 일자별 실행 = 내 슬롯 로직 편입 {len(mine)}건 전부 일치 OK")


# ===== 성과 =====
def stats_block(sim, cal, split):
    first, N = sim["first"], len(sim["ret"])
    p = perf(sim["ret"], first, N - 1)
    yrs = pd.DatetimeIndex(cal[first:]).year
    r = sim["ret"][first:]
    yearly = {int(y): float(np.prod(1 + r[yrs == y]) - 1) for y in np.unique(yrs)}
    m = np.asarray(cal[first:].values) < np.datetime64(pd.Timestamp(split))
    seg = {}
    for nm, mm in (("A(2018-19)", m), ("B(2020-22)", ~m)):
        rr = r[mm]; eq = np.cumprod(1 + rr)
        seg[nm] = {"CAGR": float(eq[-1] ** (252 / len(rr)) - 1), "MDD": float((eq / np.maximum.accumulate(eq) - 1).min()), "일수": int(len(rr))}
    tr = pd.DataFrame(sim["trades"], columns=["E", "ticker", "net_ret", "alloc", "proceeds"])
    return p, yearly, seg, tr


def run_all(ohlcv, flow, short, out_dir, sr_path=None, verbose=True):
    t0 = time.time()
    P = print if verbose else (lambda *a, **k: None)
    os.makedirs(out_dir, exist_ok=True)
    ohlcv, flow, short, feats0, nlk = build_feats(ohlcv, flow, short, DESIGN_END)
    P(f"[점검1] Feature 미래 비의존 OK · {nlk}건 · 행 {len(feats0):,} · 종목 {feats0['ticker'].nunique()}")
    feats = feats0[feats0["date"] >= pd.Timestamp(CAL_START)].reset_index(drop=True)
    po = feats.pivot(index="date", columns="ticker", values="시가").sort_index()
    pc = feats.pivot(index="date", columns="ticker", values="종가").sort_index()
    cal, tick = po.index, po.columns
    N = len(cal)
    O, Clf = po.values, pc.ffill().values
    col_of = {t: j for j, t in enumerate(tick)}
    sel = select_by_day(feats, DESIGN_START, DESIGN_END)
    s0 = int(np.searchsorted(cal.values, np.datetime64(pd.Timestamp(DESIGN_START))))
    run_idx = [i for i in range(s0, N) if i + HMAX < N]
    entries, skipped, n_open_by_day = slot_logic(cal, sel, run_idx)
    if RUN_SR_CHECK and sr_path:
        sr = load_sr(sr_path)
        run_dates = [cal[i] for i in run_idx]
        check_against_sr(sr, ohlcv, flow, short, feats0, None, run_dates, [(cal[i], t) for i, t in entries], P)
    else:
        P("[점검2·3] signal_runner 대조 생략(경로 없음)")
    ridx = load_idx_returns(PIT_EXTRA, cal)
    rows, res = [], {}
    for nm, w in WEIGHTS.items():
        sim = simulate_equity(O, Clf, col_of, entries, w, N)
        gap = sim["cash"] - (1.0 + sum(t[4] - t[3] for t in sim["trades"]))
        assert abs(gap) < 1e-9, f"자산 회계 항등식 불일치 {nm}: {gap}"
        res[nm] = sim
    P(f"[점검4] 자산 회계 항등식 OK (W20/W25)")
    first = res["W20"]["first"]
    sig_days = sum(1 for i in run_idx if sel.get(cal[i]))
    P(f"\n운용일 {len(run_idx)} · 신호 발생일 {sig_days} · 편입 {len(entries)}건 · 용량으로 건너뛴 신호 {skipped}건 · 시가 무효 미체결 {res['W20']['no_fill']}건")
    P(f"평가구간 {cal[first].date()} ~ {cal[N-1].date()} ({N-first}일) · 수익률은 소수 · 비용 0.206%/건 반영 · 헤지/슬리피지/위험필터 미반영")
    out = {}
    for nm, sim in res.items():
        p, yearly, seg, tr = stats_block(sim, cal, SEG_SPLIT)
        dd = mdd_anatomy(sim["eq"], first, cal, ridx, sim["npos"])
        exposure = float(np.mean(sim["npos"][first:] > 0))
        out[nm] = dict(perf=p, yearly=yearly, seg=seg, tr=tr, dd=dd, exposure=exposure,
                       avg_npos=float(sim["npos"][first:].mean()))
        P(f"\n[{nm} · 종목당 {WEIGHTS[nm]*100:.0f}%]")
        P(f"  CAGR {p['CAGR']:.4f} · Sharpe {p['Sharpe']:.3f} · MDD {p['MDD']:.4f}")
        P(f"  평균 보유 종목수 {out[nm]['avg_npos']:.2f} · 1종목 이상 보유일 비중 {exposure:.3f} · 거래 {len(tr)}건 · 거래 평균 순수익 {tr['net_ret'].mean():.4f} · 승률 {(tr['net_ret']>0).mean():.3f}")
        P("  연도별 수익률: " + " ".join(f"{y}:{v:+.3f}" for y, v in yearly.items()))
        P("  구간별: " + " | ".join(f"{k} CAGR {v['CAGR']:.3f} MDD {v['MDD']:.3f}" for k, v in seg.items()))
        P(f"  MDD 구간: 고점 {dd['peak']} -> 저점 {dd['trough']} ({dd['days']}거래일) MDD {dd['MDD']:.4f} · 같은 기간 KOSPI200 {dd['kospi200']:+.3f} KOSDAQ {dd['kosdaq']:+.3f} · 저점일 보유 {dd['npos_at_trough']}종목")
        tr.to_csv(f"{out_dir}/trades_{nm}.csv", index=False)
        pd.DataFrame({"date": cal, "eq": sim["eq"], "ret": sim["ret"], "npos": sim["npos"]}).to_csv(f"{out_dir}/equity_{nm}.csv", index=False)
    bm = {k: perf(ridx[:, j], first, N - 1) for j, k in enumerate(["KOSPI200", "KOSDAQ"])}
    P("\n[참고: 같은 구간 지수 buy&hold] " + " | ".join(f"{k} CAGR {v['CAGR']:.3f} MDD {v['MDD']:.3f}" for k, v in bm.items()))
    P(f"\n총 {time.time()-t0:.0f}s · 저장: {out_dir}")
    return out


def main():
    d = f"{MAIN_DIR}/data"
    ohlcv = pd.read_parquet(f"{d}/ohlcv_full.parquet")
    flow = pd.read_parquet(f"{d}/flow_full.parquet")
    short = pd.read_parquet(f"{d}/short_full.parquet")
    out_dir = os.environ.get("SIGBASE_OUT")
    if not out_dir:
        try:
            from google.colab import drive
            drive.mount("/content/drive")
            out_dir = "/content/drive/MyDrive/new_sigbase_out"
        except Exception as e:
            out_dir = "/content/new_sigbase_out"
            print(f"Drive 마운트 실패({e}) -> 로컬 {out_dir}")
    run_all(ohlcv, flow, short, out_dir, sr_path=f"{MAIN_DIR}/signal_runner.py")


if os.environ.get("SIGBASE_NO_MAIN") != "1":
    main()
