# [SIG 원자 조합 재검증 셀 — 공용 함수는 sig_decomp_cell.py와 동일 복사]
# (원 헤더) [SIG 조건 분해 진단 셀] 439 유니버스 · 설계구간 2018-01-02~2022-12-30 전용 · 읽기 전용(레포 변경 없음)
# 선행: sig 분해용 클론 셀 실행 완료(/content/main/data 존재). 2023 이후 데이터는 적재 즉시 제거(미열람).
# 사전등록(실행 전 고정): 임계값 = signal_runner.py 그대로 · BH q=0.10 · 원자 7 x horizon 2 = 14 trial/모드 · 결과 후 변경 금지
import os, json, time, subprocess
import numpy as np
import pandas as pd

DESIGN_START = "2018-01-02"
DESIGN_END = "2022-12-30"
SEG_SPLIT = "2020-01-01"      # 구간 A: 2018-19 / 구간 B: 2020-22
COST = 0.00206                # 왕복 비용(트랜치 전액)
LIQ_TH = 30e8                 # signal_runner.py L32와 동일
HORIZONS = (5, 10)            # T+1 시가 진입 -> T+h 종가 청산
NBOOT = 2000
BLOCK = 20
SEED = 20261013
Q = 0.10
MAIN_DIR = "/content/main"

ATOMS = ["A_B20", "A_B60", "A_VS", "A_FZ", "A_SC", "A_GAP", "A_VR"]
SIGS = ["SIG1", "SIG2", "SIG3", "SIG4", "SIG5", "SIG6", "SIG7"]
COMPOSITES = SIGS + ["N3"]    # N3 = n_signals>=3 (기술용, BH 패밀리 제외)


# ===== Feature 계산: signal_runner.py compute_features / SIG 정의와 동일(복사) =====
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


# ===== 원자 조건(임계값 코드 그대로, NaN 비교는 False) =====
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


def check_atoms_vs_sigs(df):
    """원자 조합으로 재구성한 SIG가 코드의 SIG와 일치하는지(정의 이식 검증)."""
    rec = {
        "SIG1": df["A_B20"] & df["A_VS"] & df["A_FZ"],
        "SIG2": df["A_B60"] & df["A_VS"],
        "SIG3": df["A_VS"] & df["A_FZ"],
        "SIG4": df["A_GAP"],
        "SIG5": df["A_VS"] & df["A_FZ"] & df["A_SC"],
        "SIG6": df["A_B20"] & df["A_VS"],
        "SIG7": df["A_VR"],
    }
    for k, v in rec.items():
        assert (v.values == df[k].values).all(), f"원자 재구성 불일치: {k}"


# ===== 미래수익률: T+1 시가 진입 -> T+h 종가 (마스터 캘린더 기준) =====
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


# ===== 통계 =====
def bh_qvalues(p):
    p = np.asarray(p, dtype=float)
    q = np.full(p.shape, np.nan)
    ok = ~np.isnan(p)
    if ok.sum() == 0:
        return q
    pv = p[ok]
    m = len(pv)
    order = np.argsort(pv)
    ranked = pv[order] * m / (np.arange(m) + 1)
    ranked = np.minimum.accumulate(ranked[::-1])[::-1]
    out = np.empty(m)
    out[order] = np.minimum(ranked, 1.0)
    q[ok] = out
    return q


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


def cond_stats(f, name, ret_col, nboot, block, rng, seg_split):
    """f: ex(풀 대비 초과), ret 열 포함 프레임(이미 모드 필터 적용). name: 불리언 열."""
    s = f[f[name]]
    out = {"cond": name, "n_events": int(len(s))}
    ndays_all = f["date"].nunique()
    if len(s) == 0:
        out.update({"n_days": 0, "day_share": 0.0, "avg_per_day": np.nan, "mean_raw": np.nan,
                    "mean_ex": np.nan, "mean_ex_net": np.nan, "p": np.nan, "pos_half": np.nan,
                    "n_half": 0, "segA": np.nan, "segB": np.nan})
        return out
    daily = s.groupby("date")["ex"].mean().sort_index()
    cnt = s.groupby("date").size()
    half = daily.index.year * 2 + (daily.index.month > 6).astype(int)
    hm = daily.groupby(half).mean()
    segA = daily[daily.index < pd.Timestamp(seg_split)]
    segB = daily[daily.index >= pd.Timestamp(seg_split)]
    out.update({
        "n_days": int(len(daily)),
        "day_share": float(len(daily) / ndays_all),
        "avg_per_day": float(cnt.mean()),
        "mean_raw": float(s[ret_col].mean()),
        "mean_ex": float(daily.mean()),
        "mean_ex_net": float(daily.mean() - COST),
        "p": block_boot_p(daily.values, nboot, block, rng),
        "pos_half": float((hm > 0).mean()),
        "n_half": int(len(hm)),
        "segA": float(segA.mean()) if len(segA) else np.nan,
        "segB": float(segB.mean()) if len(segB) else np.nan,
    })
    return out


def analyze(frame, h, mode, design_start, design_end, seg_split, nboot, block, seed):
    ret_col = f"ret{h}"
    f = frame[(frame["date"] >= pd.Timestamp(design_start)) & (frame["date"] <= pd.Timestamp(design_end))
              & frame[ret_col].notna()].copy()
    if mode == "ELIG":
        f = f[f["ELIG"]]
    f["ex"] = f[ret_col] - f.groupby("date")[ret_col].transform("mean")
    rows = []
    for i, name in enumerate(ATOMS + COMPOSITES):
        rng = np.random.default_rng(seed + 1000 * h + i)
        r = cond_stats(f, name, ret_col, nboot, block, rng, seg_split)
        r["family"] = "원자(BH)" if name in ATOMS else "복합(기술)"
        rows.append(r)
    res = pd.DataFrame(rows)
    mask = res["family"] == "원자(BH)"
    res["q"] = np.nan
    res.loc[mask, "q"] = bh_qvalues(res.loc[mask, "p"].values)
    def lab(r):
        if r["family"] != "원자(BH)" or np.isnan(r["q"]):
            return ""
        if r["q"] <= Q:
            sgn = np.sign(r["mean_ex"])
            ok = (np.sign(r["segA"]) == sgn) and (np.sign(r["segB"]) == sgn)
            return ("유의(+)" if sgn > 0 else "유의(-)") + ("/구간일관" if ok else "/구간불일치")
        return "비유의"
    res["판정"] = res.apply(lab, axis=1)
    res.insert(0, "h", h)
    res.insert(0, "mode", mode)
    return res


# ===== 중복·분해 =====
def overlap_tables(rows, cols):
    X = rows[cols].values.astype(bool)
    Xi = X.astype(np.int64)
    cnt = Xi.T @ Xi
    d = np.diag(cnt).astype(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        cond = cnt / d[None, :]            # [A,B] = P(A|B) = |A&B|/|B|
        jac = cnt / (d[:, None] + d[None, :] - cnt)
    pa = pd.DataFrame(cond, index=cols, columns=cols)
    jc = pd.DataFrame(jac, index=cols, columns=cols)
    subs = []
    for i, a in enumerate(cols):
        for j, b in enumerate(cols):
            if i != j and cnt[i, i] > 0 and cnt[i, j] == cnt[i, i]:
                subs.append((a, b, int(cnt[i, i]), int(cnt[j, j])))
    return pa, jc, pd.Series(d.astype(int), index=cols, name="n_rows"), subs


def n3_decomposition(rows):
    r = rows[rows["N3"]]
    out = {"n3_rows": int(len(r))}
    if len(r) == 0:
        return out, pd.DataFrame(), pd.DataFrame(), pd.Series(dtype=float)
    atom_share = r[ATOMS].mean().rename("share_in_N3")
    n_atoms = r[ATOMS].sum(axis=1).astype(int).rename("n_atoms")
    xt = pd.crosstab(r["n_signals"].astype(int), n_atoms)
    pat = r[ATOMS].astype(int).astype(str).agg("".join, axis=1).value_counts().head(10).rename("count")
    pat.index = [" ".join(a.replace("A_", "") for a, c in zip(ATOMS, s) if c == "1") for s in pat.index]
    return out, atom_share, xt, pat


def daily_availability(rows):
    d = rows[rows["N3"]].groupby("date").size()
    ndays = rows["date"].nunique()
    return {"days": int(ndays), "days_with_N3": int(len(d)),
            "share_days_with_N3": float(len(d) / ndays) if ndays else np.nan,
            "mean_N3_per_active_day": float(d.mean()) if len(d) else np.nan,
            "share_days_N3_ge5": float((d >= 5).sum() / ndays) if ndays else np.nan}


# ===== 사전 점검 =====
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


# ===== 원자 조합 재검증 (사전등록: 실행 전 고정, 결과 후 변경 금지) =====
# 후보 = {B20, VS, FZ, VR} 중 2개 이상 동시 충족 11개 (임계값 코드 그대로) x h(5,10) = 22 trial, BH q=0.10
# 1차 판정 모드 = ELIG(거래대금_20ma>=30억, v7 실제 후보군), ALL은 참조(동일 방식 별도 계산)
# "유지" = n_events>=300 ∧ q<=0.10 ∧ 초과>0 ∧ 구간A·B 모두 초과>0 ∧ 비용(0.206%) 차감 후 초과>0 (전부 충족)
# 참조(BH 제외): 원자 4개·N3(현행 n>=3). 기술 지표: 적중률(초과>0 비율)·±10% 꼬리 비율·N3와의 겹침
import itertools

COMBO_ATOMS = ["A_B20", "A_VS", "A_FZ", "A_VR"]
REFS = ["A_B20", "A_VS", "A_FZ", "A_VR", "N3"]
MIN_EVENTS = 300
TAIL = 0.10
# 직전 sig_decomp 실행(ELIG) 재현 기대값: (이름, h) -> mean_ex (소수 4자리)
EXPECTED = {("A_VS", 5): 0.0038, ("A_VS", 10): 0.0062, ("A_FZ", 10): -0.0005, ("A_VR", 5): 0.0048,
            ("A_VR", 10): 0.0091, ("A_B20", 10): 0.0004, ("N3", 5): 0.0105, ("N3", 10): 0.0177}


def combo_defs():
    out = []
    for k in (2, 3, 4):
        for c in itertools.combinations(COMBO_ATOMS, k):
            out.append(("C_" + "+".join(a[2:] for a in c), c))
    return out


def add_combos(df):
    for name, c in combo_defs():
        m = df[c[0]].copy()
        for a in c[1:]:
            m = m & df[a]
        df[name] = m
    return df


def verdict(r):
    if r["fam"] != "후보(BH)":
        return ""
    why = []
    if not (r["n_events"] >= MIN_EVENTS): why.append("표본<300")
    if not (r["q"] <= Q): why.append("q>0.10")
    if not (r["mean_ex"] > 0): why.append("초과<=0")
    if not (r["segA"] > 0 and r["segB"] > 0): why.append("구간불일치")
    if not (r["mean_ex_net"] > 0): why.append("비용후<=0")
    return "유지" if not why else "탈락:" + ",".join(why)


def analyze_list(frame, h, mode, names, bh_names, design_start, design_end, seg_split, nboot, block, seed):
    ret_col = f"ret{h}"
    f = frame[(frame["date"] >= pd.Timestamp(design_start)) & (frame["date"] <= pd.Timestamp(design_end))
              & frame[ret_col].notna()].copy()
    if mode == "ELIG":
        f = f[f["ELIG"]]
    f["ex"] = f[ret_col] - f.groupby("date")[ret_col].transform("mean")
    base = {"base_hit": float((f["ex"] > 0).mean()), "base_up": float((f[ret_col] >= TAIL).mean()),
            "base_dn": float((f[ret_col] <= -TAIL).mean())}
    rows = []
    for i, name in enumerate(names):
        rng = np.random.default_rng(seed + 1000 * h + i)
        r = cond_stats(f, name, ret_col, nboot, block, rng, seg_split)
        s = f[f[name]]
        if len(s):
            r.update(hit=float((s["ex"] > 0).mean()), up=float((s[ret_col] >= TAIL).mean()),
                     dn=float((s[ret_col] <= -TAIL).mean()), in_N3=float(s["N3"].mean()))
        else:
            r.update(hit=np.nan, up=np.nan, dn=np.nan, in_N3=np.nan)
        r.update(base)
        r["fam"] = "후보(BH)" if name in bh_names else "참조"
        rows.append(r)
    res = pd.DataFrame(rows)
    m = res["fam"] == "후보(BH)"
    res["q"] = np.nan
    res.loc[m, "q"] = bh_qvalues(res.loc[m, "p"].values)
    res["판정"] = res.apply(verdict, axis=1)
    res.insert(0, "h", h)
    res.insert(0, "mode", mode)
    return res


def ticker_coverage(universe_tickers, ohlcv_t, flow_t, short_t, feats):
    u = set(universe_tickers)
    o, fl, sh, ft = set(ohlcv_t["ticker"]), set(flow_t["ticker"]), set(short_t["ticker"]), set(feats["ticker"])
    return {"universe": len(u), "ohlcv(설계구간까지)": len(u & o), "flow": len(u & fl), "short": len(u & sh),
            "feats": len(u & ft), "ohlcv 없음": sorted(u - o)[:12], "ohlcv 있고 flow 없음": sorted((u & o) - fl)[:12],
            "flow 있고 ohlcv 없음": sorted((u & fl) - o)[:12],
            "n_ohlcv없음": len(u - o), "n_flow없음": len((u & o) - fl)}


# ===== N3 대비 비교 (사전등록, 실행 전 고정) =====
# 대상: ELIG · h10 · 설계구간만. 후보 3개를 현행 N3(n_signals>=3)와 비교.
# 합격 = [후보-N3 일별 초과 짝비교(양 쪽 모두 신호 있는 날) 차이>0 ∧ 부트스트랩 p<=0.10]
#      ∧ [상위 1% 이벤트 제외 후 초과>0] ∧ [N3 밖 이벤트만의 초과>0]  (전부 충족)
CANDS = ["C_FZ+VR", "C_VS+FZ+VR", "C_VS+FZ"]
H_CMP = 10
TRIM = 0.01
PAIR_P = 0.10
EXPECTED_CMP = {"C_FZ+VR": 0.0220, "C_VS+FZ+VR": 0.0217, "C_VS+FZ": 0.0172, "N3": 0.0177}


def daily_ex(f, name, trim=0.0, extra_mask=None):
    s = f[f[name]]
    if extra_mask is not None:
        s = s[extra_mask(s)]
    if trim and len(s):
        s = s[s["ex"] <= s["ex"].quantile(1 - trim)]
    return s.groupby("date")["ex"].mean().sort_index()


def cmp_one(f, cand, nboot, block, seed, seg_split):
    dc, dn = daily_ex(f, cand), daily_ex(f, "N3")
    common = dc.index.intersection(dn.index)
    diff = (dc[common] - dn[common])
    out = {"cand": cand, "n_days_cand": len(dc), "n_common_days": len(common),
           "mean_ex_cand": float(dc.mean()), "mean_ex_N3": float(dn.mean()),
           "pair_diff": float(diff.mean()) if len(diff) else np.nan,
           "pair_p": block_boot_p(diff.values, nboot, block, np.random.default_rng(seed)) if len(diff) else np.nan,
           "pair_diff_segA": float(diff[diff.index < pd.Timestamp(seg_split)].mean()),
           "pair_diff_segB": float(diff[diff.index >= pd.Timestamp(seg_split)].mean())}
    dtc, dtn = daily_ex(f, cand, TRIM), daily_ex(f, "N3", TRIM)
    out["trim_ex_cand"], out["trim_ex_N3"] = float(dtc.mean()), float(dtn.mean())
    do = daily_ex(f, cand, extra_mask=lambda s: ~s["N3"])
    out["n_events_outN3"] = int((f[cand] & ~f["N3"]).sum())
    out["outN3_ex"] = float(do.mean()) if len(do) else np.nan
    out["outN3_p"] = block_boot_p(do.values, nboot, block, np.random.default_rng(seed + 1)) if len(do) else np.nan
    why = []
    if not (out["pair_diff"] > 0 and out["pair_p"] <= PAIR_P): why.append("N3대비 미달")
    if not (out["trim_ex_cand"] > 0): why.append("상위1%제외 후<=0")
    if not (out["outN3_ex"] > 0): why.append("N3밖<=0")
    out["판정"] = "합격" if not why else "탈락:" + ",".join(why)
    return out


# ===== 합집합 규칙 U 검증 (사전등록, 실행 전 고정) =====
# U = N3 ∪ (FZ+VR) ∪ (VS+FZ) · ELIG · h10 · 설계구간만 · 시험 1개
# 합격 = [초과>0 ∧ p<=0.10 ∧ 비용(0.206%) 후>0] ∧ [구간 A·B 모두 초과>0] ∧ [상위 1% 제외 후 초과>0]
#      ∧ [N3 대비 짝비교에서 (차이<0 ∧ p<=0.10) 이 아님]
U_PARTS = ["N3", "C_FZ+VR", "C_VS+FZ"]
EXPECTED_U = {"N3": 0.0177, "C_FZ+VR": 0.0220, "C_VS+FZ": 0.0172}


def u_verdict(r):
    why = []
    if not (r["mean_ex"] > 0 and r["p"] <= 0.10 and r["mean_ex_net"] > 0): why.append("초과/p/비용후 미달")
    if not (r["segA"] > 0 and r["segB"] > 0): why.append("구간불일치")
    if not (r["trim_ex_cand"] > 0): why.append("상위1%제외 후<=0")
    if r["pair_diff"] < 0 and r["pair_p"] <= PAIR_P: why.append("N3보다 유의하게 낮음")
    return "합격" if not why else "탈락:" + ",".join(why)


def availability(rows, name):
    d = rows[rows[name]].groupby("date").size()
    n = rows["date"].nunique()
    return {"cond": name, "n_rows": int(rows[name].sum()), "days": int(n), "days_with": int(len(d)),
            "day_share": float(len(d) / n) if n else np.nan,
            "avg_per_active_day": float(d.mean()) if len(d) else np.nan,
            "share_days_ge5": float((d >= 5).sum() / n) if n else np.nan}


def u_stats(f, nboot, block, seed, seg_split):
    rng = np.random.default_rng(seed)
    r = cond_stats(f, "U", f"ret{H_CMP}", nboot, block, rng, seg_split)
    r.update(cmp_one(f, "U", nboot, block, seed + 7, seg_split))
    for nm in ("U", "N3"):
        s = f[f[nm]]
        r[f"hit_{nm}"] = float((s["ex"] > 0).mean())
        r[f"up_{nm}"] = float((s[f"ret{H_CMP}"] >= TAIL).mean())
        r[f"dn_{nm}"] = float((s[f"ret{H_CMP}"] <= -TAIL).mean())
    r["cond"] = "U"
    r["판정"] = u_verdict(r)
    return r


def run_u(ohlcv, flow, short, out_dir, design_start=DESIGN_START, design_end=DESIGN_END, seg_split=SEG_SPLIT,
          nboot=NBOOT, block=BLOCK, seed=SEED, expected=None, verbose=True):
    t0 = time.time()
    P = print if verbose else (lambda *a, **k: None)
    end = pd.Timestamp(design_end)
    ohlcv = ohlcv.assign(date=pd.to_datetime(ohlcv["date"]))
    flow = flow.assign(date=pd.to_datetime(flow["date"]))
    short = short.assign(date=pd.to_datetime(short["date"]))
    ohlcv, flow, short = ohlcv[ohlcv["date"] <= end], flow[flow["date"] <= end], short[short["date"] <= end]
    feats = add_combos(add_atoms(add_signals(compute_features(ohlcv, flow, short))))
    assert feats["date"].max() <= end, "설계구간 이후 데이터 적재됨"
    check_atoms_vs_sigs(feats)
    feats["U"] = feats["N3"] | feats["C_FZ+VR"] | feats["C_VS+FZ"]
    assert (feats["U"] >= feats["N3"]).all()
    P(f"[점검1] 원자 재구성 OK · 행 {len(feats):,} · 종목 {feats['ticker'].nunique()}")
    P(f"[점검2] 미래 비의존 OK · {check_no_lookahead(ohlcv, flow, short, feats)}건")
    rets = fwd_returns(feats, H_CMP)
    P(f"[점검3] 수익률 독립재계산 OK · {check_return_alignment(feats, rets, H_CMP)}건")
    ret_col = f"ret{H_CMP}"
    frame = feats[["date", "ticker", "ELIG", "U"] + U_PARTS].loc[:, lambda d: ~d.columns.duplicated()].merge(
        rets, on=["date", "ticker"], how="left")
    inwin = (frame["date"] >= pd.Timestamp(design_start)) & (frame["date"] <= end)
    f = frame[inwin & frame[ret_col].notna() & frame["ELIG"]].copy()
    f["ex"] = f[ret_col] - f.groupby("date")[ret_col].transform("mean")
    if expected:
        for nm, ev in expected.items():
            got = float(daily_ex(f, nm).mean())
            assert abs(got - ev) < 6e-5, f"직전 결과 재현 불일치: {nm} {got:.5f} vs {ev}"
        P(f"[점검4] 직전 결과 {len(expected)}항목 재현 OK")
    res = u_stats(f, nboot, block, seed, seg_split)
    rows = frame[inwin & frame["ELIG"]]
    av = pd.DataFrame([availability(rows, "N3"), availability(rows, "U")])
    os.makedirs(out_dir, exist_ok=True)
    pd.Series(res).to_csv(f"{out_dir}/u_result.csv")
    av.to_csv(f"{out_dir}/u_availability.csv", index=False)
    keys = ["n_events", "n_days", "avg_per_day", "mean_ex", "mean_ex_net", "p", "segA", "segB", "pos_half",
            "trim_ex_cand", "trim_ex_N3", "pair_diff", "pair_p", "pair_diff_segA", "pair_diff_segB", "n_common_days",
            "hit_U", "hit_N3", "up_U", "up_N3", "dn_U", "dn_N3", "판정"]
    P(f"\n[U = N3 ∪ FZ+VR ∪ VS+FZ · ELIG · h10 · {design_start}~{design_end}] 소수 비율")
    P(pd.Series({k: res[k] for k in keys}).to_string(float_format=lambda v: f"{v:.4f}"))
    P(f"\n[신호 가용성 (ELIG, {design_start}~{design_end} 거래일 기준)]")
    P(av.to_string(index=False, float_format=lambda v: f"{v:.4f}"))
    P(f"\n완료 {time.time()-t0:.0f}s · 저장: {out_dir}")
    return res, av


# ===== U 규칙 근접구간(2023~24) 일관성 점검 (사전등록, 실행 전 고정) =====
# 데이터 상한 2024-12-30(이후 미적재) · 평가 신호일 2023-01-02~2024-12-30 · 구간 A=2023, B=2024
# 판정 = 설계구간과 동일한 u_verdict(초과>0∧p<=0.10∧비용후>0, 2023·2024 각각 초과>0, 상위1% 제외 후>0, N3보다 유의하게 낮지 않음)
# 선행조건 = 같은 코드로 설계구간(2018~22) 결과가 직전 실행과 일치할 때만 근접 결과 산출
NEAR_START, NEAR_END, NEAR_SPLIT = "2023-01-02", "2024-12-30", "2024-01-01"
DESIGN_EXPECT = {"n_events": 1926, "mean_ex": 0.0178, "p": 0.0020, "pair_diff": 0.0005, "판정": "합격", "U_day_share": 0.6623}


def check_design(res, av, exp):
    assert int(res["n_events"]) == exp["n_events"], f"설계 재현 불일치 n_events {res['n_events']}"
    for k in ("mean_ex", "p", "pair_diff"):
        assert abs(float(res[k]) - exp[k]) < 6e-5, f"설계 재현 불일치 {k} {res[k]:.5f} vs {exp[k]}"
    assert res["판정"] == exp["판정"], f"설계 재현 불일치 판정 {res['판정']}"
    ds = float(av[av["cond"] == "U"]["day_share"].iloc[0])
    assert abs(ds - exp["U_day_share"]) < 6e-5, f"설계 재현 불일치 day_share {ds:.5f}"


def run_near(ohlcv, flow, short, out_dir, design_expect=DESIGN_EXPECT, nboot=NBOOT, verbose=True):
    P = print if verbose else (lambda *a, **k: None)
    P("===== [선행조건] 설계구간 재현 =====")
    res_d, av_d = run_u(ohlcv, flow, short, f"{out_dir}/design_repro", nboot=nboot, verbose=False)
    if design_expect:
        check_design(res_d, av_d, design_expect)
        P(f"설계구간 재현 OK (n={int(res_d['n_events'])}, 초과 {res_d['mean_ex']:.4f}, p {res_d['p']:.4f}, 판정 {res_d['판정']})")
    P("\n===== 근접구간 2023~24 일관성 점검 =====")
    res_n, av_n = run_u(ohlcv, flow, short, f"{out_dir}/near", design_start=NEAR_START, design_end=NEAR_END,
                        seg_split=NEAR_SPLIT, nboot=nboot, verbose=verbose)
    P(f"\n[근접 판정] {res_n['판정']}  (segA=2023, segB=2024 · 설계 대비 초과 {res_d['mean_ex']:.4f} -> {res_n['mean_ex']:.4f})")
    return res_d, res_n, av_n


def main():
    t0 = time.time()
    d = f"{MAIN_DIR}/data"
    ohlcv = pd.read_parquet(f"{d}/ohlcv_full.parquet")
    flow = pd.read_parquet(f"{d}/flow_full.parquet")
    short = pd.read_parquet(f"{d}/short_full.parquet")
    out_dir = os.environ.get("SIGUNEAR_OUT")
    if not out_dir:
        try:
            from google.colab import drive
            drive.mount("/content/drive")
            out_dir = "/content/drive/MyDrive/new_sigu_near_out"
        except Exception as e:
            out_dir = "/content/new_sigu_near_out"
            print(f"Drive 마운트 실패({e}) -> 로컬 {out_dir}")
    run_near(ohlcv, flow, short, out_dir)
    print(f"총 {time.time()-t0:.0f}s")


if os.environ.get("SIGUNEAR_NO_MAIN") != "1":
    main()
