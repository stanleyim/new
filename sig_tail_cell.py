# [N3 이벤트 꼬리 손실 특성 진단 셀] 설계구간 2018-01-02~2022-12-30 · 읽기 전용 · 기술 통계 전용(판정·임계값 선택 없음)
# 선행: 클론 셀 실행 완료(/content/main/data). 공용 함수는 sig_u_near_cell.py에서 원문 그대로 복사(수정 없음).
# 질문: 순수익률 -20% 이하 "꼬리" 이벤트가 신호 시점 특성(거래대금·변동폭·가격·직전 수익률·신호 강도)과 관계가 있는가.
# 대상: N3 & ELIG 이벤트 전부(T+1 시가 진입 -> T+10 종가, 비용 0.206% 차감, 상위5 선정·슬롯 한도 미적용 = 표본 확대용).
# 특성은 모두 T일 종가까지의 값(미래 비의존). 구간(터실) 경계는 설계구간 이벤트 분포에서 산출. 이 결과로 규칙·임계값을 정하지 않으며,
# 후보 규칙이 나오면 별도 사전등록 후 설계->근접 순서로 검증한다.
# 선행조건: N3 ELIG h10 풀 대비 초과 0.0177 재현.
import os, time
import numpy as np
import pandas as pd

DESIGN_START, DESIGN_END = "2018-01-02", "2022-12-30"
COST = 0.00206
LIQ_TH = 30e8
HMAX = 10
TAIL = -0.20
SIGS = ["SIG1", "SIG2", "SIG3", "SIG4", "SIG5", "SIG6", "SIG7"]
EXPECTED_N3_H10 = 0.0177
MAIN_DIR = "/content/main"
FEATURES = {"거래대금_20ma": "20일 평균 거래대금", "hl20": "20일 평균 일중변동폭((고-저)/종가)", "종가": "종가(가격)",
            "ret5": "직전 5일 수익률", "ret20": "직전 20일 수익률", "vol_surge": "거래량 급증비(5일/20일)",
            "n_signals": "신호 개수"}


# ===== 원문 복사(sig_u_near_cell.py) =====

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


def daily_ex(f, name, trim=0.0, extra_mask=None):
    s = f[f[name]]
    if extra_mask is not None:
        s = s[extra_mask(s)]
    if trim and len(s):
        s = s[s["ex"] <= s["ex"].quantile(1 - trim)]
    return s.groupby("date")["ex"].mean().sort_index()


def add_pit_features(feats):
    """T일 종가까지의 정보만 사용하는 추가 특성."""
    g = feats.groupby("ticker", group_keys=False)
    feats["hl20"] = g["hl_range"].transform(lambda s: s.rolling(20).mean())
    feats["ret5"] = g["종가"].transform(lambda s: s / s.shift(5) - 1.0)
    feats["ret20"] = g["종가"].transform(lambda s: s / s.shift(20) - 1.0)
    return feats


def tercile_table(ev, col, ret_col="net"):
    """col 3분위(설계구간 이벤트 분포 기준)별 기술 통계."""
    x = ev[col]
    try:
        b = pd.qcut(x, 3, labels=["저", "중", "고"], duplicates="drop")
    except ValueError:
        return None
    rows = []
    for lab, g in ev.groupby(b, observed=True):
        rows.append({"특성": col, "구간": str(lab), "하한": float(g[col].min()), "상한": float(g[col].max()), "건수": int(len(g)),
                     "평균": float(g[ret_col].mean()), "중앙값": float(g[ret_col].median()),
                     "꼬리비율(<=-20%)": float((g[ret_col] <= TAIL).mean()), "하위5%": float(g[ret_col].quantile(0.05)),
                     "풀대비초과": float(g["ex"].mean())})
    return pd.DataFrame(rows)


def spearman(x, y):
    return float(pd.Series(x).rank().corr(pd.Series(y).rank()))


def run_all(ohlcv, flow, short, out_dir, expected=EXPECTED_N3_H10, verbose=True):
    t0 = time.time()
    P = print if verbose else (lambda *a, **k: None)
    os.makedirs(out_dir, exist_ok=True)
    endt = pd.Timestamp(DESIGN_END)
    ohlcv = ohlcv.assign(date=pd.to_datetime(ohlcv["date"]))
    flow = flow.assign(date=pd.to_datetime(flow["date"]))
    short = short.assign(date=pd.to_datetime(short["date"]))
    ohlcv, flow, short = ohlcv[ohlcv["date"] <= endt], flow[flow["date"] <= endt], short[short["date"] <= endt]
    feats = add_pit_features(add_atoms(add_signals(compute_features(ohlcv, flow, short))))
    assert feats["date"].max() <= endt
    P(f"[점검1] Feature 미래 비의존 OK · {check_no_lookahead(ohlcv, flow, short, feats)}건 · 행 {len(feats):,} · 종목 {feats['ticker'].nunique()}")
    rets = fwd_returns(feats, HMAX)
    P(f"[점검2] 수익률 독립재계산 OK · {check_return_alignment(feats, rets, HMAX)}건")
    ret_col = f"ret{HMAX}"
    cols = ["date", "ticker", "ELIG", "N3"] + list(FEATURES)
    frame = feats[cols].loc[:, lambda d: ~d.columns.duplicated()].merge(rets, on=["date", "ticker"], how="left")
    inwin = (frame["date"] >= pd.Timestamp(DESIGN_START)) & (frame["date"] <= endt)
    pool = frame[inwin & frame[ret_col].notna() & frame["ELIG"]].copy()
    pool["ex"] = pool[ret_col] - pool.groupby("date")[ret_col].transform("mean")
    got = float(daily_ex(pool, "N3").mean())
    if expected is not None:
        assert abs(got - expected) < 6e-5, f"설계 재현 불일치: {got:.5f} vs {expected}"
        P(f"[선행조건] N3 ELIG h10 초과 {got:.4f} = 직전 {expected} 재현 OK")
    ev = pool[pool["N3"]].copy()
    ev["net"] = ev[ret_col] - COST
    n0 = len(ev)
    ev = ev.dropna(subset=list(FEATURES))
    P(f"\n이벤트 {n0}건 중 특성 결측 제외 {n0-len(ev)}건 -> {len(ev)}건 · 순수익률 평균 {ev['net'].mean():+.4f} · 중앙값 {ev['net'].median():+.4f} · "
      f"꼬리(<=-20%) {int((ev['net']<=TAIL).sum())}건({(ev['net']<=TAIL).mean():.3f})")
    tabs = []
    P("\n[특성별 3분위 · 소수 비율 · 순수익률 기준]")
    for c, lab in FEATURES.items():
        t = tercile_table(ev, c)
        if t is None:
            P(f" {c}: 3분위 분할 불가"); continue
        tabs.append(t)
        P(f"\n {c} ({lab})  Spearman: 순수익률 {spearman(ev[c], ev['net']):+.3f} · 꼬리여부 {spearman(ev[c], (ev['net']<=TAIL).astype(int)):+.3f}")
        P(t.drop(columns=["특성"]).to_string(index=False, float_format=lambda v: f"{v:.4f}"))
    pd.concat(tabs).to_csv(f"{out_dir}/tail_terciles.csv", index=False)
    tail = ev[ev["net"] <= TAIL]; rest = ev[ev["net"] > TAIL]
    P("\n[꼬리 vs 나머지 · 특성 중앙값]")
    cmp_ = pd.DataFrame({"꼬리": tail[list(FEATURES)].median(), "나머지": rest[list(FEATURES)].median()})
    P(cmp_.to_string(float_format=lambda v: f"{v:,.4f}"))
    P(f"\n총 {time.time()-t0:.0f}s · 저장: {out_dir}")
    return ev, pd.concat(tabs)


def main():
    d = f"{MAIN_DIR}/data"
    ohlcv = pd.read_parquet(f"{d}/ohlcv_full.parquet")
    flow = pd.read_parquet(f"{d}/flow_full.parquet")
    short = pd.read_parquet(f"{d}/short_full.parquet")
    out_dir = os.environ.get("SIGTAIL_OUT")
    if not out_dir:
        try:
            from google.colab import drive
            drive.mount("/content/drive")
            out_dir = "/content/drive/MyDrive/new_sigtail_out"
        except Exception as e:
            out_dir = "/content/new_sigtail_out"
            print(f"Drive 마운트 실패({e}) -> 로컬 {out_dir}")
    run_all(ohlcv, flow, short, out_dir)


if os.environ.get("SIGTAIL_NO_MAIN") != "1":
    main()
