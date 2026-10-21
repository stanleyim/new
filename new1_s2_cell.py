# [New1 2단계 셀] 미열람 확인 2015~2017(1회) — 사전등록 New1_preregistration_v1.md §4·§5(2단계). 1단계 셀과 같은 코드·규칙, 구간·임계만 다름. 읽기 전용, 레포 무변경.
# 실행은 1회. 결과를 보고 수정·재실행하지 않음.
# 구간: 신호일 2015-01-02 ~ (청산일 ≤ 2017-12-28). 가격은 2014-04-03 ~ 2017-12-28만 적재(2014년 = 지표 룩백 입력 전용).
#        2018년 이후 가격은 읽지 않음(종목별 마지막 날짜 메타데이터만 폐지 판정에 사용).
# 판정(사전 고정): 분기 평균 D의 평균 > 0.206% ∧ min(t_Q[분기 NW 시차1], t_D[일별 NW 시차10]) ≥ 1.80 (12분기, 단측 5%)
# 진단(판정 미사용): 거래대금 분위 유니버스(매일 30억 대신 20일 평균 거래대금 상위 30%) D, 연도별 일평균 유니버스 수
#   D_T = mean(ret10 | 점수 상위 20%) − mean(ret10 | 유니버스 전체), ret10 = close_X/close_E − 1, E = T+1거래일, X = E+10거래일
# 세부 규칙(사전등록 문구의 구현 해석, 실행 전 고정):
#   상위 20% = (Score 오름차순, 20일 평균 거래대금 내림차순) 정렬 후 앞 ceil(0.2·n)종목
#   E일 종가 없음·거래량 0 → 그 종목은 그날 지표에서 제외(매수 불가) / X일 거래량 0·가격 없음 → X 이후 첫 거래일(거래량>0) 종가,
#   구간 안에 없으면: 그 종목 데이터가 구간 끝 이전에 끝났으면(폐지) −100%, 아니면 제외 / E~X 사이 하루 |수익률|>30.5%면 제외
#   업종 키 = T 이하 최신 스냅샷의 (시장, 업종명), 그 스냅샷에 없으면 키 없음 → 같은 시장 평균(시장은 마지막으로 알려진 값),
#   시장도 모르면 유니버스 전체 평균(본인 제외). 스팩 판정도 같은 스냅샷 기준(없으면 비스팩).
#   선행 확인: KIS 행 중 439 달력에 없는 날짜가 이 적재 구간에 있으면 원인 미확인 상태이므로 판정 없이 중단.
import os, sys, json, time, glob, math, subprocess
import numpy as np
import pandas as pd

LOAD0, END = pd.Timestamp("2014-04-03"), pd.Timestamp("2017-12-28")
SIG0 = pd.Timestamp("2015-01-02")
T_MIN = 1.80
H, JUMP, TV_MIN, COST, TOP = 10, 0.305, 3e9, 0.00206, 0.20
OUT = os.environ.get("NEW1_S2_OUT", "/content/drive/MyDrive/new1_s2")
BASE = os.environ.get("NEW1_BASE", "/content")
REPO = os.environ.get("NEW1_REPO", "https://github.com/stanleyim/new.git")
DT = "datetime64[ns]"


def sh(c):
    subprocess.run(c, shell=True, check=True)


def clone():
    if not os.path.exists(f"{BASE}/main/data/ohlcv_full.parquet"):
        sh(f"rm -rf {BASE}/main; git clone -q --depth 1 --single-branch -b main {REPO} {BASE}/main")
    if not glob.glob(f"{BASE}/delisted/delisted_data/*.parquet"):
        sh(f"rm -rf {BASE}/delisted; git clone -q --depth 1 --single-branch -b delisted-data {REPO} {BASE}/delisted")
    if len(glob.glob(f"{BASE}/pit/pit_data_extra/sector_*.parquet")) < 13:
        sh(f"rm -rf {BASE}/pit; git clone -q --depth 1 --single-branch --filter=blob:none --sparse -b pit-data {REPO} {BASE}/pit")
        sh(f"git -C {BASE}/pit sparse-checkout set --no-cone 'pit_data_extra/sector_*.parquet'")
    n = (os.path.exists(f"{BASE}/main/data/ohlcv_full.parquet"),
         len(glob.glob(f"{BASE}/delisted/delisted_data/*.parquet")),
         len(glob.glob(f"{BASE}/pit/pit_data_extra/sector_*.parquet")))
    print(f"클론: ohlcv_full {n[0]} · KIS parquet {n[1]:,} · sector {n[2]}")
    assert n == (True, 2916, 13), "클론 기대값 불일치 — 중단"


def load():
    o = pd.read_parquet(f"{BASE}/main/data/ohlcv_full.parquet", columns=["date", "ticker", "종가", "거래량"])
    o = o.rename(columns={"종가": "close", "거래량": "volume"})
    o["date"] = pd.to_datetime(o["date"]).astype(DT)
    o["ticker"] = o["ticker"].astype(str)
    cal_all = np.unique(o["date"].to_numpy(dtype=DT))
    ks, last_date, off_cal = [], {}, []
    for f in sorted(glob.glob(f"{BASE}/delisted/delisted_data/*.parquet")):
        k = pd.read_parquet(f, columns=["date", "close", "volume"])
        k["date"] = pd.to_datetime(k["date"]).astype(DT)
        t = os.path.basename(f)[:-8]
        last_date[t] = k["date"].max()                         # 메타데이터(날짜)만
        d = k["date"].to_numpy(dtype=DT)
        off_cal.append(pd.Series(d[~np.isin(d, cal_all)]))
        k = k[(k["date"] >= LOAD0) & (k["date"] <= END)]
        k["ticker"] = t
        ks.append(k)
    for t, g in o.groupby("ticker"):
        last_date[t] = g["date"].max()
    off = pd.concat(off_cal) if off_cal else pd.Series(dtype=DT)
    print("[선행 확인] KIS 행 중 439 달력에 없는 날짜: 총 " + f"{len(off):,} · 연도별 " +
          " ".join(f"{y}:{n:,}" for y, n in off.dt.year.value_counts().sort_index().items()))
    inwin = off[(off >= LOAD0) & (off <= END)]
    if len(inwin):
        print("  적재 구간 안 해당 날짜 상위: " + ", ".join(f"{d.date()}({n})" for d, n in inwin.value_counts().head(10).items()))
        raise SystemExit("적재 구간에 달력 밖 날짜가 있음 — 원인 확인 전 판정 불가, 중단")
    o = o[(o["date"] >= LOAD0) & (o["date"] <= END)]
    p = pd.concat([o, pd.concat(ks)], ignore_index=True)
    p["close"] = p["close"].astype(float)
    p["volume"] = p["volume"].astype(float)
    assert not p.duplicated(["date", "ticker"]).any(), "(date,ticker) 중복"
    s = pd.concat([pd.read_parquet(f, columns=["date", "ticker", "종목명", "업종명", "market"])
                   for f in sorted(glob.glob(f"{BASE}/pit/pit_data_extra/sector_*.parquet"))], ignore_index=True)
    s["date"] = pd.to_datetime(s["date"]).astype(DT)
    s["ticker"] = s["ticker"].astype(str)
    s = s.drop_duplicates(["date", "ticker"], keep="last")
    return p, s, pd.Series(last_date)


def nw_t(x, lag):
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 3:
        return np.nan
    e = x - x.mean()
    lrv = (e @ e) / n
    for l in range(1, lag + 1):
        lrv += 2 * (1 - l / (lag + 1)) * (e[l:] @ e[:-l]) / n
    return x.mean() / math.sqrt(lrv / n) if lrv > 0 else np.nan


def pct_rank(a):
    """1차원 배열의 백분위 순위(평균 순위/n), NaN은 NaN."""
    r = pd.Series(a).rank(method="average").to_numpy()
    return r / np.isfinite(a).sum()


def build(p, s):
    C = p.pivot(index="date", columns="ticker", values="close").sort_index()
    V = p.pivot(index="date", columns="ticker", values="volume").reindex(C.index)
    cal = C.index.to_numpy(dtype=DT)
    tick = C.columns.to_numpy()
    Cv = C.to_numpy(dtype=float, copy=True)
    Vv = V.to_numpy(dtype=float, copy=True)
    Cv[~(Cv > 0)] = np.nan
    R = np.full_like(Cv, np.nan)
    R[1:] = Cv[1:] / Cv[:-1] - 1
    J = np.abs(np.nan_to_num(R)) > JUMP
    Jc = np.vstack([np.zeros((1, Cv.shape[1])), np.cumsum(J, axis=0)])          # Jc[i+1] = 0..i 단차 수

    def jump_in(a, b):  # 행 a+1..b(포함) 단차 여부, a<b
        return (Jc[b + 1] - Jc[a + 1]) > 0

    Rdf = pd.DataFrame(R)
    pos20 = pd.DataFrame((Vv > 0).astype(float)).rolling(20, min_periods=20).sum().to_numpy()
    tv20 = pd.DataFrame(Cv * Vv).rolling(20, min_periods=20).mean().to_numpy()
    jr = pd.DataFrame(J.astype(float))
    V60 = Rdf.rolling(60, min_periods=48).std(ddof=1).to_numpy(copy=True)
    V60[jr.rolling(60, min_periods=1).sum().to_numpy() > 0] = np.nan
    # M3: 21일 중 상위 3개 평균(유효 ≥17), 날짜 청크로 계산
    M3 = np.full_like(Cv, np.nan)
    W = 21
    for a in range(W - 1, len(cal), 150):
        b = min(len(cal), a + 150)
        win = np.lib.stride_tricks.sliding_window_view(R[a - W + 1:b], W, axis=0)   # (b-a, tickers, 21)
        cnt = np.isfinite(win).sum(axis=-1)
        w2 = np.where(np.isfinite(win), win, -np.inf)
        top3 = -np.partition(-w2, 2, axis=-1)[..., :3]
        m = top3.mean(axis=-1)
        m[cnt < 17] = np.nan
        M3[a:b] = m
    M3[jr.rolling(21, min_periods=1).sum().to_numpy() > 0] = np.nan
    R5 = np.full_like(Cv, np.nan)
    R5[5:] = Cv[5:] / Cv[:-5] - 1
    R5[jr.rolling(5, min_periods=1).sum().to_numpy() > 0] = np.nan
    # 스냅샷 매핑(T 이하 최신 스냅샷)
    snaps = np.unique(s["date"].to_numpy(dtype=DT))
    si = np.searchsorted(snaps, cal, side="right") - 1
    key_s = s.assign(key=np.where(s["업종명"].notna() & s["market"].notna(),
                                  s["market"].astype(str) + "|" + s["업종명"].astype(str), None))
    KEY = key_s.pivot(index="date", columns="ticker", values="key").reindex(index=snaps, columns=tick)
    NAME = s.pivot(index="date", columns="ticker", values="종목명").reindex(index=snaps, columns=tick)
    MK = s.pivot(index="date", columns="ticker", values="market").reindex(index=snaps, columns=tick).ffill()
    KEYv, NAMEv, MKv = KEY.to_numpy(object), NAME.to_numpy(object), MK.to_numpy(object)
    common = np.array([str(t)[-1] == "0" for t in tick])
    return dict(cal=cal, tick=tick, C=Cv, V=Vv, R=R, jump_in=jump_in, pos20=pos20, tv20=tv20, V60=V60, M3=M3, R5=R5,
                si=si, KEY=KEYv, NAME=NAMEv, MK=MKv, common=common, J=J)


def fwd_close(X, i_from, i_to, j, last_date, cal):
    """i_from일(포함)부터 i_to일까지 첫 거래일(거래량>0) 종가 행 번호. 없으면 None."""
    for i in range(i_from, i_to + 1):
        if X["V"][i, j] > 0 and np.isfinite(X["C"][i, j]):
            return i
    return None


def ret_window(X, Tn, idx, last_date):
    """신호일 행 Tn, 종목 열 idx → (ret10, ret_1_5, ret_6_10, day1, 상태코드)."""
    cal, Cv, Vv = X["cal"], X["C"], X["V"]
    E, Xi = Tn + 1, Tn + 1 + H
    out = np.full((len(idx), 4), np.nan)
    st = np.zeros(len(idx), int)   # 0 정상, 1 매수불가, 2 단차, 3 폐지(-100%), 4 구간내 미확정 제외
    for k, j in enumerate(idx):
        if not (Vv[E, j] > 0 and np.isfinite(Cv[E, j])):
            st[k] = 1; continue
        xi = fwd_close(X, Xi, len(cal) - 1, j, last_date, cal)
        if xi is None:
            ld = last_date.get(X["tick"][j], pd.NaT)
            if pd.notna(ld) and ld <= cal[-1]:
                out[k] = [-1.0, np.nan, np.nan, Cv[E, j] / Cv[Tn, j] - 1]; st[k] = 3
            else:
                st[k] = 4
            continue
        if X["jump_in"](E, xi)[j]:
            st[k] = 2; continue
        m = E + 5
        r15 = Cv[m, j] / Cv[E, j] - 1 if np.isfinite(Cv[m, j]) else np.nan
        r610 = Cv[xi, j] / Cv[m, j] - 1 if np.isfinite(Cv[m, j]) else np.nan
        out[k] = [Cv[xi, j] / Cv[E, j] - 1, r15, r610, Cv[E, j] / Cv[Tn, j] - 1]
    return out, st


def day_factors(X, Tn, cnt, tv_q=None):
    """신호일 행 Tn의 유니버스·F1·F2·점수. 반환 None(유니버스<5) 또는 dict."""
    tick = X["tick"]
    si = X["si"][Tn]
    key = X["KEY"][si] if si >= 0 else np.full(len(tick), None, object)
    name = X["NAME"][si] if si >= 0 else np.full(len(tick), None, object)
    mk = X["MK"][si] if si >= 0 else np.full(len(tick), None, object)
    spac = np.array([isinstance(nm, str) and "스팩" in nm for nm in name])
    pre = np.isfinite(X["C"][Tn]) & X["common"] & ~spac & (X["pos20"][Tn] == 20) & np.isfinite(X["tv20"][Tn])
    if tv_q is None:
        base = pre & (X["tv20"][Tn] >= TV_MIN)
    else:  # 진단: 그날 후보 중 20일 평균 거래대금 상위 (1−tv_q)
        thr = np.quantile(X["tv20"][Tn][pre], tv_q) if pre.any() else np.inf
        base = pre & (X["tv20"][Tn] >= thr)
    r5 = X["R5"][Tn]
    bb = base & np.isfinite(r5)
    df = pd.DataFrame({"r5": np.where(bb, r5, np.nan), "key": np.where(bb, key, None), "mk": np.where(bb, mk, None)})
    f1 = np.full(len(tick), np.nan)
    gk = df.groupby("key")["r5"].agg(["sum", "count"])
    gm = df.groupby("mk")["r5"].agg(["sum", "count"])
    tot_s, tot_n = df["r5"].sum(), df["r5"].count()
    for j in np.where(bb)[0]:
        kk, mm = key[j], mk[j]
        if isinstance(kk, str) and kk in gk.index and gk.at[kk, "count"] - 1 >= 5:
            f1[j] = r5[j] - (gk.at[kk, "sum"] - r5[j]) / (gk.at[kk, "count"] - 1)
            continue
        if not isinstance(kk, str):
            cnt["nokey"] += 1
        if isinstance(mm, str) and mm in gm.index and gm.at[mm, "count"] - 1 >= 1:
            f1[j] = r5[j] - (gm.at[mm, "sum"] - r5[j]) / (gm.at[mm, "count"] - 1); cnt["fb_mkt"] += 1
        elif tot_n - 1 >= 1:
            f1[j] = r5[j] - (tot_s - r5[j]) / (tot_n - 1); cnt["fb_all"] += 1
    v60, m3 = X["V60"][Tn], X["M3"][Tn]
    U = bb & np.isfinite(f1) & np.isfinite(v60) & np.isfinite(m3)
    idx = np.where(U)[0]
    if len(idx) < 5:
        return None
    f2 = (pct_rank(v60[idx]) + pct_rank(m3[idx])) / 2
    p1, p2 = pct_rank(f1[idx]), pct_rank(f2)
    return dict(idx=idx, f1=f1[idx], v60=v60[idx], m3=m3[idx], p1=p1, p2=p2, score=(p1 + p2) / 2,
                base=base, key=key)


def run():
    t0 = time.time()
    os.makedirs(OUT, exist_ok=True)
    clone()
    p, s, last_date = load()
    X = build(p, s)
    cal, tick = X["cal"], X["tick"]
    last_date = last_date.to_dict()
    nT = len(cal)
    sig_rows = [i for i in range(nT) if cal[i] >= np.datetime64(SIG0) and i + 1 + H < nT]
    print(f"적재 {pd.Timestamp(cal[0]).date()}~{pd.Timestamp(cal[-1]).date()} · 거래일 {nT} · 종목 {len(tick):,} · "
          f"신호일 {len(sig_rows)} ({pd.Timestamp(cal[sig_rows[0]]).date()}~{pd.Timestamp(cal[sig_rows[-1]]).date()})")
    recs, cnt = [], dict(fb_mkt=0, fb_all=0, nokey=0, n_univ=0, st1=0, st2=0, st3=0, st4=0)
    corr12 = []
    for Tn in sig_rows:
        dfac = day_factors(X, Tn, cnt)
        if dfac is None:
            continue
        idx, p1, p2, score, base, key = dfac["idx"], dfac["p1"], dfac["p2"], dfac["score"], dfac["base"], dfac["key"]
        n = len(idx)
        cnt["n_univ"] += n
        corr12.append(pd.Series(p1).corr(pd.Series(p2), method="spearman"))
        rets, st = ret_window(X, Tn, idx, last_date)
        for c in (1, 2, 3, 4):
            cnt[f"st{c}"] += int((st == c).sum())
        ok = np.isfinite(rets[:, 0])
        tv = X["tv20"][Tn][idx]
        k_top = math.ceil(TOP * n)

        def topset(sc, asc=True):
            o = np.lexsort((-tv, sc if asc else -sc))
            m = np.zeros(n, bool); m[o[:k_top]] = True
            return m
        top = topset(score)
        bot = topset(-score)
        top1, top2 = topset(p1), topset(p2)
        u = rets[ok, 0].mean()
        rec = {"T": pd.Timestamp(cal[Tn]), "n": n, "n_ok": int(ok.sum()), "k_top": k_top,
               "D": rets[top & ok, 0].mean() - u, "TB": rets[top & ok, 0].mean() - rets[bot & ok, 0].mean(),
               "D_F1": rets[top1 & ok, 0].mean() - u, "D_F2": rets[top2 & ok, 0].mean() - u,
               "D_1_5": np.nanmean(rets[top & ok, 1]) - np.nanmean(rets[ok, 1]),
               "D_6_10": np.nanmean(rets[top & ok, 2]) - np.nanmean(rets[ok, 2]),
               "D_day1": np.nanmean(rets[top & ok, 3]) - np.nanmean(rets[ok, 3])}
        # F3 진단: 업종 동일가중 누적수익률 t-63~t-5 (업종 5종목 이상), 높을수록 우대
        if Tn >= 63:
            cr = X["C"][Tn - 5] / X["C"][Tn - 63] - 1
            jj = X["jump_in"](Tn - 63, Tn - 5)
            cr = np.where(jj, np.nan, cr)
            d3 = pd.DataFrame({"cr": np.where(base, cr, np.nan), "key": np.where(base, key, None)})
            g3 = d3.groupby("key")["cr"].agg(["mean", "count"])
            f3 = np.array([g3.at[key[j], "mean"] if isinstance(key[j], str) and key[j] in g3.index and g3.at[key[j], "count"] >= 5
                           else np.nan for j in idx])
            v3 = np.isfinite(f3) & ok
            if v3.sum() >= 20:
                k3 = math.ceil(TOP * v3.sum())
                o3 = np.argsort(-np.where(v3, f3, -np.inf))[:k3]
                rec["D_F3"] = rets[o3, 0].mean() - rets[v3, 0].mean()
        dq = day_factors(X, Tn, dict(fb_mkt=0, fb_all=0, nokey=0), tv_q=0.70)
        if dq is not None:
            rq, _ = ret_window(X, Tn, dq["idx"], last_date)
            okq = np.isfinite(rq[:, 0]); nq = len(dq["idx"]); kq = math.ceil(TOP * nq)
            oq = np.lexsort((-X["tv20"][Tn][dq["idx"]], dq["score"]))
            mq = np.zeros(nq, bool); mq[oq[:kq]] = True
            rec["D_tvq30"] = rq[mq & okq, 0].mean() - rq[okq, 0].mean()
            rec["n_tvq30"] = nq
        recs.append(rec)
    Dd = pd.DataFrame(recs)
    Dd["Q"] = Dd["T"].dt.to_period("Q").astype(str)
    Q = Dd.groupby("Q")[[c for c in Dd.columns if c.startswith("D") or c == "TB"]].mean()
    Q["n_days"] = Dd.groupby("Q").size()
    mean_q = Q["D"].mean()
    tQ, tD = nw_t(Q["D"].to_numpy(), 1), nw_t(Dd["D"].to_numpy(), 10)
    t = min(tQ, tD)
    print(f"\n[표본] 신호일 {len(Dd)} · 분기 {len(Q)} · 일평균 유니버스 {Dd['n'].mean():.0f}(최소 {Dd['n'].min()}·최대 {Dd['n'].max()}) · "
          f"상위 20% 일평균 {Dd['k_top'].mean():.0f}종목")
    tot = cnt["n_univ"]
    print(f"[처리 건수] 유니버스 종목-일 {tot:,} · F1 같은 시장 대체 {cnt['fb_mkt']:,}({cnt['fb_mkt']/tot:.2%}) · 전체 평균 대체 {cnt['fb_all']:,} · "
          f"업종 키 없음 {cnt['nokey']:,} · 매수불가 제외 {cnt['st1']:,} · 단차 제외 {cnt['st2']:,} · 폐지 −100% {cnt['st3']:,} · 미확정 제외 {cnt['st4']:,}")
    with pd.option_context("display.width", 250, "display.max_columns", 20, "display.float_format", lambda v: f"{v:+.4f}"):
        print("\n[분기별] (10일 초과수익, 0.0100 = 1%)\n" + Q[["n_days", "D", "TB", "D_F1", "D_F2", "D_F3"] if "D_F3" in Q else ["n_days", "D", "TB", "D_F1", "D_F2"]].to_string())
    passed = (mean_q > COST) and (t >= T_MIN)
    print(f"\n[판정 지표] 분기 평균 D의 평균 {100*mean_q:+.3f}% · t_Q(NW1) {tQ:.2f} · t_D(NW10) {tD:.2f} → t {t:.2f} · "
          f"플러스 분기 {int((Q['D'] > 0).sum())}/{len(Q)}")
    print("[진단·판정 미사용] 분기 평균: " + " · ".join(
        f"{c} {100*Q[c].mean():+.3f}%" for c in ["TB", "D_F1", "D_F2", "D_F3", "D_1_5", "D_6_10", "D_day1", "D_tvq30"] if c in Q))
    print("[진단] 연도별 일평균 유니버스(30억 기준 / 거래대금 상위 30%): " + " ".join(
        f"{y}:{g['n'].mean():.0f}/{g['n_tvq30'].mean():.0f}" for y, g in Dd.groupby(Dd['T'].dt.year)))
    print(f"[진단] F1·F2 일별 순위상관 평균 {np.nanmean(corr12):+.3f} · 연도별 D 평균: " +
          " ".join(f"{y}:{100*v:+.2f}%" for y, v in Dd.groupby(Dd['T'].dt.year)["D"].mean().items()))
    print(f"\n2단계 판정: {'통과' if passed else '미달 → 사전등록대로 New1 종료'} "
          f"(기준: 평균 > {100*COST:.3f}% → {100*mean_q:+.3f}% · t ≥ {T_MIN} → {t:.2f})")
    Dd.to_csv(f"{OUT}/s2_daily.csv", index=False, encoding="utf-8-sig")
    Q.to_csv(f"{OUT}/s2_quarters.csv", encoding="utf-8-sig")
    json.dump({"pass": bool(passed), "mean_q": mean_q, "tQ": tQ, "tD": tD, "counts": cnt, "n_days": len(Dd), "n_q": len(Q)},
              open(f"{OUT}/s2_summary.json", "w"), ensure_ascii=False, indent=1, default=str)
    print(f"저장: {OUT}/ (s2_daily·s2_quarters·s2_summary) · 소요 {time.time()-t0:.0f}s")
    return Dd, Q, X


if os.environ.get("NEW1_S2_NO_MAIN") != "1":
    try:
        from google.colab import drive
        drive.mount("/content/drive")
    except Exception:
        pass
    S2 = run()
