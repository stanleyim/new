# [청산 규칙 검증 셀 — N3 신호 · ELIG · 이벤트 단위 · A(T+10 보유) 대비 B/C/D/E 짝비교]
# 선행: 클론 셀 실행 완료(/content/main/data 존재). 읽기 전용(레포 변경 없음). 공용 함수는 sig_u_near_cell.py에서 원문 그대로 복사.
#
# ===== 사전등록 (실행 전 고정, 결과 후 변경 금지) =====
# 대상: N3(n_signals>=3) & ELIG(거래대금_20ma>=30억) 이벤트, T+1 시가 진입. 데이터는 구간 끝(설계 2022-12-30 / 근접 2024-12-30)까지만 적재.
# 일봉 종가 확인 후 다음 날 시가 청산(EOD 룰). 보유일 j=1..10은 T+1이 1일차. 진입가 P0 = T+1 시가.
#   A: T+10 종가 보유(기준선)
#   B: 종가 <= P0*(1-5%) 마감 시(j=1..9) 다음 날 시가 청산. 없으면 T+10 종가
#   C: 종가 기준 최고점이 P0 대비 +8% 이상 도달한 뒤, 종가 < 최고 종가*(1-4%) 마감 시(j=1..9) 다음 날 시가 청산. 없으면 T+10 종가
#   D: 손절가 = T+1 일봉 저가*(1-2%). j=2..9 종가 < 손절가 마감 시 다음 날 시가 청산. 없으면 T+10 종가
#   E: 종가 >= P0*(1+8%) 마감(j=1..9) 시 다음 날 시가에 절반 청산. 나머지 절반은 이후 j에서 종가 < 5일 종가이동평균 마감 시 다음 날 시가 청산, 없으면 T+10 종가. +8% 미도달이면 A와 동일
# 비용: 왕복 0.206%를 이벤트당 1회 차감(분할 청산도 비례 합계가 같아 1회). 비용은 모든 규칙에 동일하므로 A 대비 차이에는 영향 없음
# 이벤트 유효집합: 1~10일차 시가>0·종가 존재, T+1 저가>0, 1~10일차 5일이동평균 존재. 하나라도 없으면 모든 규칙에서 제외(제외 건수 보고)
# 채택(후보 B~E 각각, 4개 전부 충족): 
#   1) A 대비 일별 평균 차이(규칙-A)>0 이고 블록 부트스트랩 p<0.025 (후보 4개 Bonferroni)
#   2) 상위 1% 제외 후 일별 평균 차이>0 (제외 = A 수익 상위 1% 이벤트 ∪ 해당 규칙 수익 상위 1% 이벤트)
#   3) 반기 블록 중 평균 차이>0 인 블록 비율 > 0.5
#   4) 이벤트 수익률 하위 5% 분위수(규칙) >= 하위 5% 분위수(A)
# 근접구간 2023-01-02~2024-12-30: 설계구간 채택 후보에만 같은 4개 조건을 1회 적용. A(N3 T+10)는 병기용 기술 통계만. 채택 후보가 없으면 A 유지.
# 선행조건: 설계구간 N3 ELIG h10 초과(풀 평균 대비) 0.0177 재현 + 벡터 구현과 스칼라 구현 전 이벤트 일치 + 규칙 A 수익률이 fwd_returns와 일치
import os, time, sys
import numpy as np
import pandas as pd

DESIGN_START = "2018-01-02"
DESIGN_END = "2022-12-30"
NEAR_START = "2023-01-02"
NEAR_END = "2024-12-30"
COST = 0.00206
LIQ_TH = 30e8                 # signal_runner.py와 동일
NBOOT = 2000
BLOCK = 20
SEED = 20261014
MAIN_DIR = "/content/main"
SIGS = ["SIG1", "SIG2", "SIG3", "SIG4", "SIG5", "SIG6", "SIG7"]

# 규칙 파라미터 (사전등록)
STOP_B = -0.05
ARM_C, TRAIL_C = 0.08, 0.04
STOP_D_BUF = 0.02
TP_E = 0.08
HMAX = 10
P_ADOPT = 0.025
TRIM_Q = 0.01
EXPECTED_N3_H10 = 0.0177      # 직전 sig_decomp/sig_u_near 실행 값(설계구간, ELIG, h10, 풀 대비 초과)
RULES = ["B", "C", "D", "E"]


# ===== 원문 복사(sig_u_near_cell.py, 수정 없음) =====

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


def block_boot_p(x, nboot, block, rng):
    """순환 블록 부트스트랩 양측 p (평균 0 중심화 귀무). 일별 시계열 x 대상."""
    x = np.asarray(x, dtype=float)
    n = len(x)
    if n < 30:
        return np.nan
    B = min(block, n)
    nb = int(np.ceil(n / B))
    obs = x.mean()
    xc = x - obs
    starts = rng.integers(0, n, size=(nboot, nb))
    idx = (starts[:, :, None] + np.arange(B)[None, None, :]) % n
    idx = idx.reshape(nboot, -1)[:, :n]
    means = xc[idx].mean(axis=1)
    return float((1 + np.sum(np.abs(means) >= abs(obs))) / (nboot + 1))


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


def daily_ex(f, name, trim=0.0, extra_mask=None):
    s = f[f[name]]
    if extra_mask is not None:
        s = s[extra_mask(s)]
    if trim and len(s):
        s = s[s["ex"] <= s["ex"].quantile(1 - trim)]
    return s.groupby("date")["ex"].mean().sort_index()

# ===== 청산 규칙 (벡터 구현) =====
def build_bars(feats, events):
    """events: date,ticker 열. 반환 dict: 이벤트별 1~10일차 시가/종가, T+1 저가, 1~10일차 MA5 (n x 10)."""
    po = feats.pivot(index="date", columns="ticker", values="시가").sort_index()
    pc = feats.pivot(index="date", columns="ticker", values="종가").sort_index()
    pl = feats.pivot(index="date", columns="ticker", values="저가").sort_index()
    ma5 = pc.rolling(5).mean()
    cal = po.index
    di = cal.get_indexer(pd.DatetimeIndex(events["date"]))
    ti = po.columns.get_indexer(events["ticker"])
    assert (di >= 0).all() and (ti >= 0).all()
    ok = (di + HMAX) < len(cal)
    di, ti = di[ok], ti[ok]
    ev = events.loc[ok].reset_index(drop=True)
    cols = di[:, None] + np.arange(1, HMAX + 1)[None, :]
    O = po.values[cols, ti[:, None]]
    C = pc.values[cols, ti[:, None]]
    M = ma5.values[cols, ti[:, None]]
    L1 = pl.values[di + 1, ti]
    return ev, O, C, L1, M


def valid_mask(O, C, L1, M):
    return (np.isfinite(O).all(1) & (O > 0).all(1) & np.isfinite(C).all(1) &
            np.isfinite(L1) & (L1 > 0) & np.isfinite(M).all(1))


def _first(trig):
    """trig: n x m 불리언. 반환 (any, 첫 True의 열 인덱스)."""
    anyt = trig.any(1)
    return anyt, trig.argmax(1)


def simulate_exits(O, C, L1, M):
    """반환 dict: 규칙별 총수익률(비용 전), 보유일, 발동 여부. 인덱스 k=0..9 는 j=k+1 일차."""
    n = len(O)
    P0 = O[:, 0]
    r = np.arange(n)
    out = {}
    retA = C[:, 9] / P0 - 1.0
    out["A"] = dict(ret=retA, days=np.full(n, float(HMAX)), trig=np.zeros(n, bool))

    def exit_rule(trig9):
        anyt, k = _first(trig9)
        px = np.where(anyt, O[r, np.minimum(k + 1, 9)], C[:, 9])
        days = np.where(anyt, k + 2, HMAX).astype(float)
        return dict(ret=px / P0 - 1.0, days=days, trig=anyt)

    # B
    out["B"] = exit_rule(C[:, :9] <= P0[:, None] * (1.0 + STOP_B))
    # C
    peak = np.maximum.accumulate(C, axis=1)
    armed = (peak / P0[:, None] - 1.0) >= ARM_C
    out["C"] = exit_rule(armed[:, :9] & (C[:, :9] < peak[:, :9] * (1.0 - TRAIL_C)))
    # D (j=2..9, 즉 k=1..8)
    trigD = np.zeros((n, 9), bool)
    trigD[:, 1:] = C[:, 1:9] < (L1 * (1.0 - STOP_D_BUF))[:, None]
    out["D"] = exit_rule(trigD)
    # E
    tp = (C[:, :9] / P0[:, None] - 1.0) >= TP_E
    anyE, k8 = _first(tp)
    half1 = O[r, np.minimum(k8 + 1, 9)] / P0 - 1.0
    later = np.arange(9)[None, :] > k8[:, None]
    brk = later & (C[:, :9] < M[:, :9])
    anyb, kb = _first(brk)
    px2 = np.where(anyb, O[r, np.minimum(kb + 1, 9)], C[:, 9])
    d2 = np.where(anyb, kb + 2, HMAX).astype(float)
    half2 = px2 / P0 - 1.0
    retE = np.where(anyE, 0.5 * half1 + 0.5 * half2, retA)
    daysE = np.where(anyE, 0.5 * (k8 + 2) + 0.5 * d2, float(HMAX))
    out["E"] = dict(ret=retE, days=daysE, trig=anyE)
    return out


def simulate_one(o, c, l1, m):
    """스칼라 참조 구현(검증용). o,c,m: 길이 10 리스트(1~10일차)."""
    p0 = o[0]
    res = {"A": (c[9] / p0 - 1.0, 10.0, False)}
    # B
    x = (c[9] / p0 - 1.0, 10.0, False)
    for j in range(1, 10):
        if c[j - 1] <= p0 * (1.0 + STOP_B):
            x = (o[j] / p0 - 1.0, float(j + 1), True); break
    res["B"] = x
    # C
    x = (c[9] / p0 - 1.0, 10.0, False); peak = -1e18
    for j in range(1, 10):
        peak = max(peak, c[j - 1])
        if (peak / p0 - 1.0) >= ARM_C and c[j - 1] < peak * (1.0 - TRAIL_C):
            x = (o[j] / p0 - 1.0, float(j + 1), True); break
    res["C"] = x
    # D
    x = (c[9] / p0 - 1.0, 10.0, False)
    for j in range(2, 10):
        if c[j - 1] < l1 * (1.0 - STOP_D_BUF):
            x = (o[j] / p0 - 1.0, float(j + 1), True); break
    res["D"] = x
    # E
    x = (c[9] / p0 - 1.0, 10.0, False)
    for j in range(1, 10):
        if (c[j - 1] / p0 - 1.0) >= TP_E:
            h1 = o[j] / p0 - 1.0; d1 = float(j + 1)
            px2, d2 = c[9], 10.0
            for jj in range(j + 1, 10):
                if c[jj - 1] < m[jj - 1]:
                    px2, d2 = o[jj], float(jj + 1); break
            x = (0.5 * h1 + 0.5 * (px2 / p0 - 1.0), 0.5 * d1 + 0.5 * d2, True); break
    res["E"] = x
    return res


def check_vec_vs_scalar(O, C, L1, M, sim):
    n = len(O)
    for i in range(n):
        ref = simulate_one(O[i].tolist(), C[i].tolist(), float(L1[i]), M[i].tolist())
        for k in ["A"] + RULES:
            a = (sim[k]["ret"][i], sim[k]["days"][i], bool(sim[k]["trig"][i]))
            b = ref[k]
            assert abs(a[0] - b[0]) < 1e-12 and abs(a[1] - b[1]) < 1e-12 and a[2] == b[2], f"벡터/스칼라 불일치 {k} 이벤트{i}: {a} vs {b}"
    return n


# ===== 평가 =====
def half_id(idx):
    return idx.year * 2 + (idx.month > 6).astype(int)


def eval_rule(ev, rA, rR, daysR, trigR, nboot, block, seed):
    """ev: date 열. rA, rR: 비용 전 수익률. 비용은 동일하므로 차이는 비용 무관."""
    d = pd.DataFrame({"date": ev["date"].values, "rA": rA, "rR": rR})
    d["diff"] = d["rR"] - d["rA"]
    daily = d.groupby("date")["diff"].mean().sort_index()
    p = block_boot_p(daily.values, nboot, block, np.random.default_rng(seed))
    qa = d["rA"].quantile(1 - TRIM_Q); qr = d["rR"].quantile(1 - TRIM_Q)
    kept = d[(d["rA"] <= qa) & (d["rR"] <= qr)]
    trim_diff = float(kept.groupby("date")["diff"].mean().mean())
    hm = daily.groupby(half_id(daily.index)).mean()
    share_pos = float((hm > 0).mean())
    q05A, q05R = float(np.quantile(rA, 0.05)), float(np.quantile(rR, 0.05))
    c1 = bool(daily.mean() > 0 and p < P_ADOPT) if not np.isnan(p) else False
    c2 = bool(trim_diff > 0)
    c3 = bool(share_pos > 0.5)
    c4 = bool(q05R >= q05A)
    why = [lab for lab, ok in (("1 평균·p", c1), ("2 상위1%제외", c2), ("3 반기블록", c3), ("4 하위5%", c4)) if not ok]
    return {"n_events": int(len(d)), "n_days": int(len(daily)), "mean_net_A": float(rA.mean() - COST),
            "mean_net_rule": float(rR.mean() - COST), "diff_mean_daily": float(daily.mean()), "p": p,
            "trim_diff": trim_diff, "n_blocks": int(len(hm)), "share_pos_blocks": share_pos,
            "q05_A": q05A, "q05_rule": q05R, "median_A": float(np.median(rA)), "median_rule": float(np.median(rR)),
            "win_A": float((rA - COST > 0).mean()), "win_rule": float((rR - COST > 0).mean()),
            "trig_share": float(np.mean(trigR)), "mean_days": float(np.mean(daysR)),
            "worst_A": float(rA.min()), "worst_rule": float(rR.min()),
            "c1": c1, "c2": c2, "c3": c3, "c4": c4,
            "판정": "채택" if not why else "탈락:" + ",".join(why)}


def pipeline_window(ohlcv, flow, short, start, end, event_col="N3", verbose=True, label=""):
    """구간 [start,end]의 이벤트(event_col & ELIG)와 규칙 시뮬레이션. 데이터는 end까지만 적재."""
    P = print if verbose else (lambda *a, **k: None)
    endt = pd.Timestamp(end)
    ohlcv = ohlcv.assign(date=pd.to_datetime(ohlcv["date"]))
    flow = flow.assign(date=pd.to_datetime(flow["date"]))
    short = short.assign(date=pd.to_datetime(short["date"]))
    ohlcv, flow, short = ohlcv[ohlcv["date"] <= endt], flow[flow["date"] <= endt], short[short["date"] <= endt]
    feats = add_atoms(add_signals(compute_features(ohlcv, flow, short)))
    assert feats["date"].max() <= endt, "구간 이후 데이터 적재됨"
    P(f"[{label}점검1] 행 {len(feats):,} · 종목 {feats['ticker'].nunique()}")
    P(f"[{label}점검2] 미래 비의존 OK · {check_no_lookahead(ohlcv, flow, short, feats)}건")
    rets = fwd_returns(feats, HMAX)
    P(f"[{label}점검3] 수익률 독립재계산 OK · {check_return_alignment(feats, rets, HMAX)}건")
    ret_col = f"ret{HMAX}"
    frame = feats[["date", "ticker", "ELIG", event_col]].loc[:, lambda d: ~d.columns.duplicated()].merge(
        rets, on=["date", "ticker"], how="left")
    inwin = (frame["date"] >= pd.Timestamp(start)) & (frame["date"] <= endt)
    pool = frame[inwin & frame[ret_col].notna() & frame["ELIG"]].copy()
    pool["ex"] = pool[ret_col] - pool.groupby("date")[ret_col].transform("mean")
    A_full = pool[pool[event_col]].copy()
    mean_ex = float(daily_ex(pool, event_col).mean())
    events = A_full[["date", "ticker", ret_col]].reset_index(drop=True)
    ev, O, C, L1, M = build_bars(feats, events[["date", "ticker"]])
    assert len(ev) == len(events), "이벤트 윈도우 부족 행 발생"
    ok = valid_mask(O, C, L1, M)
    sim = simulate_exits(O[ok], C[ok], L1[ok], M[ok])
    evv = ev.loc[ok].reset_index(drop=True)
    P(f"[{label}점검4] 규칙 A 수익률 = fwd_returns 일치: ", end="")
    ref = events.loc[ok, ret_col].values
    assert np.abs(sim["A"]["ret"] - ref).max() < 1e-12, "규칙 A와 fwd_returns 불일치"
    P("OK")
    nchk = check_vec_vs_scalar(O[ok], C[ok], L1[ok], M[ok], sim)
    P(f"[{label}점검5] 벡터/스칼라 구현 {nchk}건 전부 일치 OK")
    info = {"n_events_A_full": int(len(events)), "n_events_valid": int(ok.sum()), "n_excluded": int((~ok).sum()),
            "mean_ex_event_vs_pool": mean_ex}
    P(f"[{label}이벤트] A 전체 {info['n_events_A_full']} · 유효 {info['n_events_valid']} · 제외 {info['n_excluded']} · 풀 대비 초과 {mean_ex:.4f}")
    return evv, sim, info


def run_rules(evv, sim, seed, nboot, block):
    rows = []
    for i, k in enumerate(RULES):
        r = eval_rule(evv, sim["A"]["ret"], sim[k]["ret"], sim[k]["days"], sim[k]["trig"], nboot, block, seed + 100 * i)
        r["rule"] = k
        rows.append(r)
    return pd.DataFrame(rows).set_index("rule")


def describe_A(evv, sim):
    rA = sim["A"]["ret"]
    daily = pd.Series(rA - COST).groupby(evv["date"].values).mean()
    return {"n_events": int(len(rA)), "mean_net": float(rA.mean() - COST), "median_net": float(np.median(rA) - COST),
            "win": float((rA - COST > 0).mean()), "q05": float(np.quantile(rA, 0.05)), "worst": float(rA.min()),
            "n_days": int(daily.shape[0])}


SHOW = ["n_events", "mean_net_A", "mean_net_rule", "diff_mean_daily", "p", "trim_diff", "share_pos_blocks",
        "q05_A", "q05_rule", "win_A", "win_rule", "trig_share", "mean_days", "판정"]


def run_all(ohlcv, flow, short, out_dir, nboot=NBOOT, block=BLOCK, seed=SEED, event_col="N3",
            expected=EXPECTED_N3_H10, verbose=True):
    t0 = time.time()
    P = print if verbose else (lambda *a, **k: None)
    os.makedirs(out_dir, exist_ok=True)
    P("===== 설계구간 2018~22 =====")
    evv, sim, info = pipeline_window(ohlcv, flow, short, DESIGN_START, DESIGN_END, event_col, verbose, "설계 ")
    if expected is not None:
        assert abs(info["mean_ex_event_vs_pool"] - expected) < 6e-5, \
            f"설계 재현 불일치: {info['mean_ex_event_vs_pool']:.5f} vs {expected}"
        P(f"[선행조건] N3 ELIG h10 초과 {info['mean_ex_event_vs_pool']:.4f} = 직전 {expected} 재현 OK")
    res = run_rules(evv, sim, seed, nboot, block)
    res.to_csv(f"{out_dir}/design_rules.csv")
    pd.Series(describe_A(evv, sim)).to_csv(f"{out_dir}/design_A.csv")
    P("\n[설계구간: 규칙 vs A] 수익률은 소수(0.01=1%), mean_net=비용 0.206% 차감")
    P(res[SHOW].to_string(float_format=lambda v: f"{v:.4f}"))
    P("\n[A 기술] " + str(describe_A(evv, sim)))
    adopted = [k for k in RULES if res.loc[k, "판정"] == "채택"]
    P(f"\n[설계 채택 후보] {adopted if adopted else '없음 -> A(T+10 보유) 유지'}")
    P("\n===== 근접구간 2023~24 (1회) =====")
    evn, simn, infon = pipeline_window(ohlcv, flow, short, NEAR_START, NEAR_END, event_col, verbose, "근접 ")
    An = describe_A(evn, simn)
    pd.Series(An).to_csv(f"{out_dir}/near_A.csv")
    P("[근접 A 병기] " + str(An))
    resn = None
    if adopted:
        resn = run_rules(evn, simn, seed + 7, nboot, block).loc[adopted]
        resn.to_csv(f"{out_dir}/near_rules.csv")
        P("\n[근접구간: 설계 채택 후보만]")
        P(resn[SHOW].to_string(float_format=lambda v: f"{v:.4f}"))
        fin = [k for k in adopted if resn.loc[k, "판정"] == "채택"]
        P(f"\n[최종] 설계·근접 모두 채택: {fin if fin else '없음 -> A(T+10 보유) 유지'}")
    else:
        P("[근접] 설계 채택 후보 없음 -> 규칙 검정 생략, A 병기만")
    P(f"\n총 {time.time()-t0:.0f}s · 저장: {out_dir}")
    return res, resn


def main():
    d = f"{MAIN_DIR}/data"
    ohlcv = pd.read_parquet(f"{d}/ohlcv_full.parquet")
    flow = pd.read_parquet(f"{d}/flow_full.parquet")
    short = pd.read_parquet(f"{d}/short_full.parquet")
    out_dir = os.environ.get("SIGEXIT_OUT")
    if not out_dir:
        try:
            from google.colab import drive
            drive.mount("/content/drive")
            out_dir = "/content/drive/MyDrive/new_sigexit_out"
        except Exception as e:
            out_dir = "/content/new_sigexit_out"
            print(f"Drive 마운트 실패({e}) -> 로컬 {out_dir}")
    run_all(ohlcv, flow, short, out_dir)


if os.environ.get("SIGEXIT_NO_MAIN") != "1":
    main()
