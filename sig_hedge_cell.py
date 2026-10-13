# [N3 포트폴리오 + 지수 베타 헤지 검증 셀 — 마켓뉴트럴 전환 1차]
# 선행: 클론 셀(main + pit-data idx) 실행 완료(/content/main/data, /content/pit/pit_data_extra/idx_*.parquet). 읽기 전용.
# 공용 함수 원문 복사: sig_u_near_cell.py(Feature·수익률·점검), gate2.py(daily_path·perf), g2_hedge.py(load_idx_returns). 수정 없음.
#
# ===== 사전등록 (실행 전 고정, 결과 후 변경 금지) =====
# 질문: N3 신호의 실제 운용 구조(T+1 시가 진입·T+10 종가 청산)에서 시장(지수) 베타를 헤지로 제거하면
#       MDD가 줄면서 수익이 남는가?
# 포트폴리오(일별 트랜치, 슬롯 10개 각 1/10, 신호 없는 날=현금, 왕복 0.206% 트랜치마다 전액·gate2.daily_path 그대로):
#   TOP5 = 매일 N3&ELIG 중 signal_score 상위 5개 동일가중 (signal_runner.select_signals와 동일 규칙)  <- 판정 대상
#   ALL  = 매일 N3&ELIG 전 종목 동일가중 (참조)
# 헤지: 직전 120거래일 OLS β, 20거래일 갱신, 갱신일 β는 그 이전 데이터만 사용(g2_hedge와 동일). 헤지 비용·금융비용은 반영하지 않음(TL-006~013과 동일, 한계)
#   K  = KOSPI200 지수 1개 (인버스 ETF 252670/114800으로 실행 가능한 형태)  <- 판정 대상
#   KK = KOSPI200 + KOSDAQ 2개 (참조: KOSDAQ 인버스 ETF 데이터는 레포에 없음)
# 판정 대상 = TOP5 x K 1개 (전부 충족): ①헤지 후 일평균>0 ②|헤지 후 MDD|<|비헤지 MDD| ③구간 A(2018-19)·B(2020-22) 각각 헤지 후 일평균>0
# 평가구간 = 모든 슬롯 활성 구간 중 β 설정일부터 (s=a+120 ~ b, gate2 perf와 동일 정의)
# 근접 2023~24: 판정 대상이 설계구간 통과 시에만 연속 경로로 1회(평가 2023-01-02~2024-12-30, ①②+③ 2023·2024 각각). 통과 못하면 근접 데이터는 열람하지 않음
# 선행조건: 벡터 트랜치 경로 = 스칼라 독립 구현(전 일자 일치), 수익률 정렬·미래 비의존 점검, 근접 단계는 연속 경로가 설계 경로와 [0,b_d]에서 일치
import os, time
import numpy as np
import pandas as pd

DESIGN_START, DESIGN_END = "2018-01-02", "2022-12-30"
NEAR_START, NEAR_END = "2023-01-02", "2024-12-30"
CAL_START = "2017-01-02"      # 기존 연구 캘린더 시작(research D3과 동일)
SEG_SPLIT = "2020-01-01"
LIQ_TH = 30e8
HMAX = 10
TOP_K = 5
BETA_WIN, BETA_STEP = 120, 20
IDX_NAMES = ("KOSPI200", "KOSDAQ")
SIGS = ["SIG1", "SIG2", "SIG3", "SIG4", "SIG5", "SIG6", "SIG7"]
MAIN_DIR = "/content/main"
PIT_EXTRA = "/content/pit/pit_data_extra"


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


def fwd_returns(df, h):
    assert not df.duplicated(["date", "ticker"]).any(), "(date,ticker) 중복"
    po = df.pivot(index="date", columns="ticker", values="시가").sort_index()
    pc = df.pivot(index="date", columns="ticker", values="종가").sort_index()
    entry = po.shift(-1)
    exit_ = pc.shift(-h)
    ret = exit_ / entry - 1.0
    ret = ret.where(entry > 0)
    long = ret.reset_index().melt(id_vars="date", var_name="ticker", value_name=f"ret{h}")
    return long.dropna(subset=[f"ret{h}"])


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


def check_return_alignment(feats, rets, h, n=5, seed=11):
    """수익률을 마스터 캘린더 위치로 독립 재계산해 대조."""
    cal = pd.DatetimeIndex(sorted(feats["date"].unique()))
    pos = {d: i for i, d in enumerate(cal)}
    idx = feats.set_index(["date", "ticker"])
    r = rets.sample(n=min(n, len(rets)), random_state=seed)
    for _, row in r.iterrows():
        i = pos[row["date"]]
        d_entry, d_exit = cal[i + 1], cal[i + h]
        o = idx.loc[(d_entry, row["ticker"]), "시가"]
        c = idx.loc[(d_exit, row["ticker"]), "종가"]
        assert abs((c / o - 1.0) - row[f"ret{h}"]) < 1e-12, "수익률 정렬 불일치"
    return len(r)


def daily_path(W, O, Clf, tpos, h, cost=None):
    """트랜치 일별 경로. W: 신호일×종목 bool(선택 ∧ T1 유효). 신호일 t마다 T+1 시가 동일가중 진입,
    T+k 종가(휴장·무거래는 직전 유효 종가 이월=ffill, L2 청산가와 동일)로 평가, T+h 종가 청산 후 왕복 비용 전액 부과.
    슬롯 h개(각 1/h), 일 수익률 = (1/h)*Σ 활성 슬롯 일수익률(빈 슬롯=현금 0). 반환 길이 N의 일별 수익률."""
    cost = C.G2_COST_RT if cost is None else cost
    N = O.shape[0]
    cnt = W.sum(axis=1); ex = cnt > 0
    denom = np.where(ex, cnt, 1)
    E0 = O[tpos + 1]
    ret = np.zeros(N)
    Vprev = np.ones(len(tpos))
    for k in range(1, h + 1):
        with np.errstate(invalid="ignore", divide="ignore"):
            R = np.where(W, Clf[tpos + k] / E0, 0.0)
        V = R.sum(axis=1) / denom
        if k == h:
            V = V * (1.0 - cost)
        with np.errstate(invalid="ignore", divide="ignore"):
            dr = np.where(ex, V / Vprev - 1.0, 0.0)
        ret[tpos + k] += dr / h
        Vprev = np.where(ex, V, 1.0)
    assert np.isfinite(ret).all(), "경로에 NaN(유효 선택 종목의 중간 가격 결측)"
    return ret


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

# ===== 행렬 구성 / 트랜치 경로 =====
def build_inputs(ohlcv, flow, short, end, start_cal=CAL_START):
    endt = pd.Timestamp(end)
    ohlcv = ohlcv.assign(date=pd.to_datetime(ohlcv["date"]))
    flow = flow.assign(date=pd.to_datetime(flow["date"]))
    short = short.assign(date=pd.to_datetime(short["date"]))
    ohlcv, flow, short = ohlcv[ohlcv["date"] <= endt], flow[flow["date"] <= endt], short[short["date"] <= endt]
    feats = add_atoms(add_signals(compute_features(ohlcv, flow, short)))
    assert feats["date"].max() <= endt, "구간 이후 데이터 적재됨"
    checks = (check_no_lookahead(ohlcv, flow, short, feats), None)
    feats = feats[feats["date"] >= pd.Timestamp(start_cal)].reset_index(drop=True)
    rets = fwd_returns(feats, HMAX)
    checks = (checks[0], check_return_alignment(feats, rets, HMAX))
    return feats, rets, checks


def build_weights(feats, rets, start, end):
    """반환: cal, tick, O, Clf, tpos, W_all, W_top (신호일×종목 bool), 이벤트 표(스칼라 검증용)."""
    po = feats.pivot(index="date", columns="ticker", values="시가").sort_index()
    pc = feats.pivot(index="date", columns="ticker", values="종가").sort_index()
    cal, tick = po.index, po.columns
    N = len(cal)
    O = po.values
    Clf = pc.ffill().values
    ret_col = f"ret{HMAX}"
    ev = feats.loc[feats["N3"] & feats["ELIG"], ["date", "ticker", "signal_score"]].merge(
        rets[["date", "ticker", ret_col]], on=["date", "ticker"], how="inner")   # 라벨 유효(진입>0·청산종가 존재)만
    ev = ev[(ev["date"] >= pd.Timestamp(start)) & (ev["date"] <= pd.Timestamp(end))].copy()
    ev["rank"] = ev.groupby("date")["signal_score"].rank(method="first", ascending=False)
    # select_signals: today_df.nlargest(5, "signal_score") 와 동일 순서 규칙 확인용 별도 계산
    top_idx = ev.groupby("date", group_keys=False).apply(lambda g: g.nlargest(TOP_K, "signal_score")).index
    ev["top"] = ev.index.isin(top_idx)
    di_all = np.arange(N)
    s0 = int(np.searchsorted(cal.values, np.datetime64(pd.Timestamp(start))))
    tpos = di_all[s0:][(di_all[s0:] + HMAX < N) & (cal.values[s0:] <= np.datetime64(pd.Timestamp(end)))]
    pos_of = {d: i for i, d in enumerate(cal)}
    col_of = {t: j for j, t in enumerate(tick)}
    row_of_t = {int(p): r for r, p in enumerate(tpos)}
    W_all = np.zeros((len(tpos), len(tick)), bool)
    W_top = np.zeros_like(W_all)
    for d, t, tp in zip(ev["date"], ev["ticker"], ev["top"]):
        r = row_of_t.get(pos_of[d])
        if r is None:
            continue
        j = col_of[t]
        W_all[r, j] = True
        if tp:
            W_top[r, j] = True
    return cal, tick, O, Clf, tpos, W_all, W_top, ev


def scalar_path(O, Clf, tpos, W, N, h=HMAX, cost=C.G2_COST_RT):
    """daily_path의 독립 스칼라 구현(검증용). 슬롯 h개(1/h), 트랜치=선택 종목 동일가중."""
    ret = np.zeros(N)
    for r, T in enumerate(tpos):
        js = np.where(W[r])[0]
        if len(js) == 0:
            continue
        vprev = 1.0
        for k in range(1, h + 1):
            v = float(np.mean([Clf[T + k, j] / O[T + 1, j] for j in js]))
            if k == h:
                v *= (1.0 - cost)
            ret[T + k] += (v / vprev - 1.0) / h
            vprev = v
    return ret


# ===== 헤지 =====
def hedge_path_k(rq, ridx, a, b, L=BETA_WIN, step=BETA_STEP):
    """g2_hedge_near.hedge_path와 같은 β 규칙을 열 수 k에 일반화. 반환 rh, beta(N×k), s."""
    N, k = len(rq), ridx.shape[1]
    s = a + L
    assert a >= 1 and s <= b < N, "평가구간 부족"
    beta = np.zeros((N, k)); prev = np.zeros(k)
    for u in range(s, b + 1, step):
        X = np.column_stack([np.ones(L), ridx[u - L:u]])
        prev = np.linalg.lstsq(X, rq[u - L:u], rcond=None)[0][1:]
        beta[u:min(u + step, b + 1)] = prev
    return rq - (beta * ridx).sum(axis=1), beta, s


def eval_hedge(rq, rh, beta, ridx, e0, e1, seg_masks):
    """[e0,e1] 평가. seg_masks: {이름: 길이 N 불리언}. 판정 ①헤지후 일평균>0 ②|헤지후 MDD|<|비헤지 MDD| ③구간별 헤지후 일평균>0."""
    assert (beta[e0:e1 + 1] != 0).any(axis=1).all(), "평가구간에 β 미설정일 존재"
    pu, ph = perf(rq, e0, e1), perf(rh, e0, e1)
    segm = {}
    for nm, m in seg_masks.items():
        mm = np.zeros(len(rq), bool); mm[e0:e1 + 1] = m[e0:e1 + 1]
        assert mm.any(), f"구간 {nm} 평가일 없음"
        segm[nm] = (float(rh[mm].mean()), float(rq[mm].mean()))
    X = np.column_stack([np.ones(e1 + 1 - e0), ridx[e0:e1 + 1]])
    resid = np.linalg.lstsq(X, rh[e0:e1 + 1], rcond=None)[0][1:]
    c1 = bool(rh[e0:e1 + 1].mean() > 0)
    c2 = bool(abs(ph["MDD"]) < abs(pu["MDD"]))
    c3 = bool(all(v[0] > 0 for v in segm.values()))
    out = {"n_days": e1 + 1 - e0, "u_mean": float(rq[e0:e1 + 1].mean()), "h_mean": float(rh[e0:e1 + 1].mean()),
           "u_CAGR": pu["CAGR"], "u_Sharpe": pu["Sharpe"], "u_MDD": pu["MDD"],
           "h_CAGR": ph["CAGR"], "h_Sharpe": ph["Sharpe"], "h_MDD": ph["MDD"],
           "beta_mean": [float(x) for x in beta[e0:e1 + 1].mean(axis=0)],
           "resid_beta": [float(x) for x in resid],
           "seg": {k: {"h_mean": v[0], "u_mean": v[1]} for k, v in segm.items()},
           "c1": c1, "c2": c2, "c3": c3}
    out["판정"] = "통과" if (c1 and c2 and c3) else "탈락:" + ",".join(
        l for l, ok in (("①헤지후 일평균", c1), ("②MDD", c2), ("③구간", c3)) if not ok)
    return out


VARIANTS = {"K": [0], "KK": [0, 1]}


def stage(ohlcv, flow, short, start, end, seg_defs, eval_from, label, verbose=True):
    P = print if verbose else (lambda *a, **k: None)
    feats, rets, ck = build_inputs(ohlcv, flow, short, end)
    P(f"[{label}점검1] 미래 비의존 OK · {ck[0]}건 / 수익률 독립재계산 OK · {ck[1]}건 · 행 {len(feats):,} · 종목 {feats['ticker'].nunique()}")
    cal, tick, O, Clf, tpos, W_all, W_top, ev = build_weights(feats, rets, start, end)
    N = len(cal)
    paths = {}
    for nm, W in (("TOP5", W_top), ("ALL", W_all)):
        paths[nm] = daily_path(W, O, Clf, tpos, HMAX)
        sc = scalar_path(O, Clf, tpos, W, N)
        assert np.allclose(paths[nm], sc, rtol=1e-9, atol=1e-12), f"트랜치 경로 스칼라 불일치 {nm}"
    P(f"[{label}점검2] 트랜치 경로 벡터=스칼라 전 일자 일치 OK (TOP5/ALL) · 신호일 {len(tpos)} · "
      f"이벤트일 TOP5 {int((W_top.sum(1) > 0).sum())} ALL {int((W_all.sum(1) > 0).sum())} · 종목수/일 TOP5 평균 {W_top.sum(1)[W_top.sum(1)>0].mean():.2f}")
    ridx_all = load_idx_returns(PIT_EXTRA, cal)
    a, b = int(tpos[0]) + HMAX, int(tpos[-1]) + 1
    seg_masks = {nm: fn(cal) for nm, fn in seg_defs.items()}
    e0_ = max(a + BETA_WIN, int(np.searchsorted(cal.values, np.datetime64(pd.Timestamp(eval_from)))))
    res, hp = {}, {}
    for pn in ("TOP5", "ALL"):
        for vn, cols in VARIANTS.items():
            ridx = ridx_all[:, cols]
            rh, beta, s = hedge_path_k(paths[pn], ridx, a, b)
            hp[(pn, vn)] = (rh, beta, ridx)
            res[(pn, vn)] = eval_hedge(paths[pn], rh, beta, ridx, e0_, b, seg_masks)
    return dict(cal=cal, tpos=tpos, a=a, b=b, e0=e0_, paths=paths, hp=hp, res=res, ridx=ridx_all)


def fmt_table(res):
    rows = []
    for (pn, vn), r in res.items():
        row = {"포트": pn, "헤지": vn, "일수": r["n_days"], "비헤지 일평균": r["u_mean"], "헤지후 일평균": r["h_mean"],
               "비헤지 CAGR": r["u_CAGR"], "헤지후 CAGR": r["h_CAGR"], "비헤지 Sharpe": r["u_Sharpe"],
               "헤지후 Sharpe": r["h_Sharpe"], "비헤지 MDD": r["u_MDD"], "헤지후 MDD": r["h_MDD"],
               "평균β": "/".join(f"{x:.2f}" for x in r["beta_mean"]),
               "잔차β": "/".join(f"{x:.2f}" for x in r["resid_beta"]), "판정": r["판정"]}
        for k, v in r["seg"].items():
            row[f"{k} 헤지후 일평균"] = v["h_mean"]
        rows.append(row)
    return pd.DataFrame(rows)


def run_all(ohlcv, flow, short, out_dir, verbose=True):
    t0 = time.time()
    P = print if verbose else (lambda *a, **k: None)
    os.makedirs(out_dir, exist_ok=True)
    split = np.datetime64(pd.Timestamp(SEG_SPLIT))
    seg_d = {"A(2018-19)": lambda cal: cal.values < split, "B(2020-22)": lambda cal: cal.values >= split}
    P("===== 설계구간 2018~22 =====")
    d = stage(ohlcv, flow, short, DESIGN_START, DESIGN_END, seg_d, DESIGN_START, "설계 ", verbose)
    tab = fmt_table(d["res"])
    tab.to_csv(f"{out_dir}/design_table.csv", index=False)
    P(f"\n평가구간 {d['cal'][d['e0']].date()} ~ {d['cal'][d['b']].date()} ({d['b']+1-d['e0']}일) · 수익률은 소수")
    P(tab.to_string(index=False, float_format=lambda v: f"{v:.4f}"))
    main_res = d["res"][("TOP5", "K")]
    P(f"\n[판정 대상 TOP5 x K] {main_res['판정']}")
    if main_res["판정"] != "통과":
        P("-> 설계구간 탈락: 근접구간 데이터는 열람하지 않음. N3 마켓뉴트럴(지수 베타 헤지)은 이 구성으로 종결.")
        P(f"\n총 {time.time()-t0:.0f}s · 저장: {out_dir}")
        return d, None
    P("\n===== 근접구간 2023~24 (1회, 연속 경로) =====")
    seg_n = {"2023": lambda cal: pd.DatetimeIndex(cal).year == 2023, "2024": lambda cal: pd.DatetimeIndex(cal).year == 2024}
    n = stage(ohlcv, flow, short, DESIGN_START, NEAR_END, seg_n, NEAR_START, "근접 ", verbose)
    bd = d["b"]
    for pn in ("TOP5", "ALL"):
        assert np.allclose(n["paths"][pn][:bd + 1], d["paths"][pn][:bd + 1], rtol=1e-9, atol=1e-12), "연속 경로≠설계 경로"
        for vn in VARIANTS:
            assert np.allclose(n["hp"][(pn, vn)][0][:bd + 1], d["hp"][(pn, vn)][0][:bd + 1], rtol=1e-9, atol=1e-12), "연속 헤지경로≠설계"
    P("[근접 선행] 연속 경로가 설계 경로와 [0,b_d]에서 일치 OK")
    tabn = fmt_table(n["res"])
    tabn.to_csv(f"{out_dir}/near_table.csv", index=False)
    P(f"평가구간 {n['cal'][n['e0']].date()} ~ {n['cal'][n['b']].date()} ({n['b']+1-n['e0']}일)")
    P(tabn.to_string(index=False, float_format=lambda v: f"{v:.4f}"))
    P(f"\n[근접 판정 대상 TOP5 x K] {n['res'][('TOP5','K')]['판정']}")
    P(f"\n총 {time.time()-t0:.0f}s · 저장: {out_dir}")
    return d, n


def main():
    d = f"{MAIN_DIR}/data"
    ohlcv = pd.read_parquet(f"{d}/ohlcv_full.parquet")
    flow = pd.read_parquet(f"{d}/flow_full.parquet")
    short = pd.read_parquet(f"{d}/short_full.parquet")
    out_dir = os.environ.get("SIGHEDGE_OUT")
    if not out_dir:
        try:
            from google.colab import drive
            drive.mount("/content/drive")
            out_dir = "/content/drive/MyDrive/new_sighedge_out"
        except Exception as e:
            out_dir = "/content/new_sighedge_out"
            print(f"Drive 마운트 실패({e}) -> 로컬 {out_dir}")
    run_all(ohlcv, flow, short, out_dir)


if os.environ.get("SIGHEDGE_NO_MAIN") != "1":
    main()
