# new3_s1_cell.py — New3 1단계(0단계 점검 포함) · 읽기 전용 · Colab 단일 셀 · 결과 Drive new3_s1
# 사전등록: New3_diversified_lowvol_plan_v1.md (2026-10-21 Stanley 확정). 결과를 보고 정의·기준을 바꾸지 않는다.
# 판정(시장 룰 ON, 세 구간 모두): 비용 후 CAGR > 0 ∧ MDD ≥ −25% ∧ Sharpe ≥ 같은 구간 D450 지수 Sharpe
# E1 차단: 가격은 2024-12-30까지만 적재한다. 예외: 종목별 '마지막 거래일'(폐지 −100% 판정용)은 2026-06-26까지 데이터로 계산(날짜만, 수익 미사용).
# 해석 규칙(문서 문구의 구현): ①"20거래일 거래량 0 없음"은 20일 모두 거래량>0(가격 행 없음도 불충족)
#   ②스팩 판정 이름 = T 이하 스냅샷 중 그 종목이 있는 가장 최근 것(없으면 이름 모름 → 포함)
#   ③보유 중 수익은 마지막 유효 종가 대비로 갱신(가격 공백 후 재개 포함), |수익|>30.5%면 그날 0
#   ④상위 20%(진단 D20) = floor(F2 계산 가능 종목 수 × 0.2)
import os, sys, glob, json, time, subprocess, warnings
import numpy as np, pandas as pd

COST = 0.00206; HOLD = 20; SLOTS = 20; WGT = 0.05; FILL = 1.29; JUMP = 0.305
TOPN = 450; LB_AMT = 60; MIN_AMT = 48; LB_VOL0 = 20; LB_V = 60; MIN_V = 48; LB_M = 21; MIN_M = 17; MA_N = 200
CUT = pd.Timestamp("2024-12-30"); DATA_END = pd.Timestamp("2026-06-26"); CAL_START = pd.Timestamp("2014-04-03")
SEGS = [("2015~17", "2015-01-02", "2017-12-28"), ("2018~22", "2018-01-02", "2022-12-30"), ("2023~24", "2023-01-02", "2024-12-30")]
REF_EW_CAGR = {"2015~17": -0.031, "2018~22": -0.132, "2023~24": -0.147}   # v8 상위450 셀의 '같은 유니버스 동일가중'(정의 완전 동일 아님, 참고)
OUT = "/content/drive/MyDrive/new3_s1"


# ---------------- 공통 ----------------
def mem():
    try:
        import psutil
        return f"{psutil.Process().memory_info().rss / 1e9:.1f}GB"
    except Exception:
        return "?"


def norm_ticker(s):
    s = pd.Series(s).astype(str).str.strip().str.replace(r"\.0$", "", regex=True)
    return s.where(~s.str.fullmatch(r"\d+"), s.str.zfill(6)).values


def norm_date(s):
    s = pd.to_datetime(pd.Series(s))
    if getattr(s.dt, "tz", None) is not None:
        s = s.dt.tz_localize(None)
    return s.dt.normalize().astype("datetime64[ns]").values


def is_common(t):
    return isinstance(t, str) and len(t) == 6 and t.endswith("0")


def daily_ret(C):
    R = np.full(C.shape, np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        R[1:] = C[1:] / C[:-1] - 1.0
    R[~np.isfinite(R)] = np.nan
    return R


def nw_t(x, lag):
    x = np.asarray(x, float); x = x[np.isfinite(x)]; n = x.size
    if n < 3:
        return np.nan
    e = x - x.mean(); s = (e @ e) / n
    for k in range(1, min(lag, n - 1) + 1):
        s += 2.0 * (1.0 - k / (lag + 1.0)) * (e[k:] @ e[:-k]) / n
    return x.mean() / np.sqrt(s / n) if s > 0 else np.nan


# ---------------- 유니버스 · 지수 · 신호 ----------------
def build_spac(sec, cal, tickers):
    """sec: date,ticker,종목명. 반환 (D,N) bool: T 이하 스냅샷 중 그 종목이 있는 최신 이름에 '스팩'."""
    s = sec[sec["ticker"].isin(set(tickers))].copy()
    s["f"] = s["종목명"].astype(str).str.contains("스팩", regex=False).astype(float)
    piv = s.pivot_table(index="date", columns="ticker", values="f", aggfunc="max").reindex(columns=list(tickers))
    allidx = piv.index.union(pd.DatetimeIndex(cal)).sort_values()
    p = piv.reindex(allidx).ffill().reindex(pd.DatetimeIndex(cal))
    return np.array(p.values == 1.0, copy=True)


def build_universe(C, V, spac, common, topn=TOPN):
    D, N = C.shape
    valid = np.isfinite(C) & (C > 0)
    amt = np.where(valid & np.isfinite(V) & (V >= 0), C * V, np.nan)
    a60 = np.array(pd.DataFrame(amt).rolling(LB_AMT, min_periods=MIN_AMT).mean().values, dtype=float, copy=True)
    volpos = (valid & np.isfinite(V) & (V > 0)).astype(float)
    ok20 = np.array(pd.DataFrame(volpos).rolling(LB_VOL0, min_periods=LB_VOL0).sum().values == LB_VOL0, copy=True)
    elig = valid & ok20 & np.isfinite(a60) & (~spac) & np.asarray(common, bool)[None, :]
    M = np.zeros((D, N), bool); cnt = np.zeros(D, int)
    for d in range(D):
        idx = np.flatnonzero(elig[d])
        if idx.size == 0:
            continue
        sel = idx[np.lexsort((idx, -a60[d, idx]))[:topn]]
        M[d, sel] = True; cnt[d] = sel.size
    return M, a60, cnt


def build_index(R, M):
    D = R.shape[0]; ix = np.full(D, np.nan); nmem = np.zeros(D, int); t0 = None
    for d in range(D):
        if t0 is None:
            if M[d].any():
                t0 = d; ix[d] = 1000.0
            continue
        r = R[d, M[d - 1]]; r = r[np.isfinite(r) & (np.abs(r) <= JUMP)]
        nmem[d] = r.size
        ix[d] = ix[d - 1] * (1.0 + r.mean()) if r.size else ix[d - 1]
    ma = np.array(pd.Series(ix).rolling(MA_N, min_periods=MA_N).mean().values, dtype=float, copy=True)
    return ix, ma, t0, nmem


def build_f2(R, M, a60):
    """반환: F2 (D,N), ranks[d] = 매수 순위(F2 오름차순, 동률 60일 거래대금 큰 순)의 종목 인덱스 배열."""
    D, N = R.shape
    jump = np.isfinite(R) & (np.abs(R) > JUMP)
    F2 = np.full((D, N), np.nan); ranks = [np.array([], int) for _ in range(D)]
    for d in range(LB_V - 1, D):
        mem_ = np.flatnonzero(M[d])
        if mem_.size == 0:
            continue
        w = R[d - LB_V + 1:d + 1][:, mem_]
        jmp = jump[d - LB_V + 1:d + 1][:, mem_].any(0)
        nv = np.isfinite(w).sum(0)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            v60 = np.nanstd(w, axis=0, ddof=1)
        v60[(nv < MIN_V) | jmp] = np.nan
        w21 = R[d - LB_M + 1:d + 1][:, mem_]
        n21 = np.isfinite(w21).sum(0)
        srt = -np.sort(-np.where(np.isfinite(w21), w21, -np.inf), axis=0)[:3]
        m3 = srt.mean(0); m3[(n21 < MIN_M) | jmp] = np.nan
        ok = np.isfinite(v60) & np.isfinite(m3)
        if not ok.any():
            continue
        ids = mem_[ok]
        r1 = pd.Series(v60[ok]).rank(pct=True, method="average").values
        r2 = pd.Series(m3[ok]).rank(pct=True, method="average").values
        f = (r1 + r2) / 2.0
        F2[d, ids] = f
        ranks[d] = ids[np.lexsort((-a60[d, ids], f))]
    return F2, ranks


def allow_from_index(ix, ma):
    a = np.ones(len(ix), bool)
    m = np.isfinite(ix) & np.isfinite(ma) & (ix < ma)
    a[m] = False
    return a


# ---------------- 포트폴리오 ----------------
def simulate(seg, C, V, ranks, allow, last_tr, slots=SLOTS, w=WGT, hold=HOLD, cost=COST, fill=FILL, writeoff_zero=True):
    """seg: 전역 거래일 인덱스(연속). allow: 신호일 T별 매수 허용(None=항상). last_tr: 종목별 마지막 거래(거래량>0) 전역 인덱스.
    반환 dict: V(일별 계좌, 마지막 날은 미청산 비용·폐지 반영), cash, trades(DataFrame), st(건수)."""
    cash = 1.0; pos = []; Vs = []; cs = []; tr = []
    st = dict(buys=0, nonfill=0, jump=0, writeoff=0, writeoff_end=0, blocked_days=0, pending_days=0, cost=0.0, buy_amt=0.0)
    for d in seg:
        for p in pos:                                   # 오늘 종가로 평가 갱신
            c = C[d, p["i"]]
            if np.isfinite(c) and c > 0:
                rr = c / p["lc"] - 1.0
                if abs(rr) > JUMP:
                    st["jump"] += 1; p["nj"] += 1
                else:
                    p["val"] *= (1.0 + rr)
                p["lc"] = c
        keep = []                                       # 1) 청산
        for p in pos:
            if d >= p["x"]:
                i = p["i"]; c = C[d, i]; v = V[d, i]
                if np.isfinite(c) and c > 0 and np.isfinite(v) and v > 0:
                    proc = p["val"] * (1.0 - cost); cash += proc; st["cost"] += p["val"] * cost
                    tr.append(dict(i=i, e=p["e"], x=d, amt=p["amt"], net=proc / p["amt"] - 1.0, how="close" if d == p["x"] else "delayed", nj=p["nj"]))
                    continue
                if last_tr[i] < d:                      # 다시 거래되지 않음 → 폐지 처리
                    st["writeoff"] += 1
                    ref = p["val"] * (1.0 - cost)
                    proc = 0.0 if writeoff_zero else ref
                    cash += proc
                    tr.append(dict(i=i, e=p["e"], x=d, amt=p["amt"], net=proc / p["amt"] - 1.0, how="writeoff", nj=p["nj"]))
                    continue
                st["pending_days"] += 1
            keep.append(p)
        pos = keep
        Vd = cash + sum(p["val"] for p in pos)          # 2) 평가
        T = d - 1                                       # 3~5) 매수
        if allow is not None and not allow[T]:
            st["blocked_days"] += 1
        elif len(pos) < slots and cash > 0:
            held = {p["i"] for p in pos}
            for i in ranks[T]:
                if i in held:
                    continue
                c = C[d, i]; v = V[d, i]; cT = C[T, i]
                if not (np.isfinite(c) and c > 0 and np.isfinite(v) and v > 0 and c < cT * fill):
                    st["nonfill"] += 1; continue
                amt = min(Vd * w, cash); cash -= amt
                pos.append(dict(i=i, val=amt, amt=amt, lc=c, e=d, x=d + hold, nj=0))
                st["buys"] += 1; st["buy_amt"] += amt
                break
        Vs.append(cash + sum(p["val"] for p in pos)); cs.append(cash)
    dl = seg[-1]; fin = cash                            # 구간 종료: 미청산은 종가 평가 − 비용, 영구 정지는 0
    for p in pos:
        ref = p["val"] * (1.0 - cost)
        if last_tr[p["i"]] < dl:
            st["writeoff_end"] += 1
            v = 0.0 if writeoff_zero else ref
            tr.append(dict(i=p["i"], e=p["e"], x=dl, amt=p["amt"], net=v / p["amt"] - 1.0, how="writeoff_end", nj=p["nj"]))
        else:
            v = ref
            tr.append(dict(i=p["i"], e=p["e"], x=dl, amt=p["amt"], net=v / p["amt"] - 1.0, how="open_end", nj=p["nj"]))
        fin += v
    Vs[-1] = fin
    return dict(V=np.array(Vs), cash=np.array(cs), trades=pd.DataFrame(tr), st=st)


def metrics(v_after, v0=1.0):
    v = np.r_[v0, np.asarray(v_after, float)]; a = v[1:] / v[:-1] - 1.0; n = a.size
    sd = a.std(ddof=1)
    return dict(CAGR=(v[-1] / v[0]) ** (252.0 / n) - 1.0,
                Sharpe=a.mean() / sd * np.sqrt(252.0) if sd > 0 else np.nan,
                MDD=float((v / np.maximum.accumulate(v) - 1.0).min()), n=n)


def judge(m, mi):
    c1 = m["CAGR"] > 0; c2 = m["MDD"] >= -0.25; c3 = m["Sharpe"] >= mi["Sharpe"]
    return bool(c1 and c2 and c3), (c1, c2, c3)


# ---------------- 진단 D20 ----------------
def d20_diag(seg, C, V, R, M, ranks, hold=HOLD):
    rows = []; last = seg[-1]; excl = 0
    for E in seg:
        T = E - 1; X = E + hold; Mid = E + hold // 2
        if X > last:
            break
        rk = ranks[T]; k = int(np.floor(len(rk) * 0.2))
        if k < 1:
            continue
        def grp(ids):
            nonlocal excl
            cE, cX, cM, vE = C[E, ids], C[X, ids], C[Mid, ids], V[E, ids]
            ok = np.isfinite(cE) & (cE > 0) & np.isfinite(vE) & (vE > 0) & np.isfinite(cX) & (cX > 0)
            seg_r = R[E + 1:X + 1][:, ids]
            ok &= ~(np.isfinite(seg_r) & (np.abs(seg_r) > JUMP)).any(0)
            excl += int((~ok).sum())
            okm = ok & np.isfinite(cM) & (cM > 0)
            return (np.mean(cX[ok] / cE[ok] - 1) if ok.any() else np.nan,
                    np.mean(cM[okm] / cE[okm] - 1) if okm.any() else np.nan,
                    np.mean(cX[okm] / cM[okm] - 1) if okm.any() else np.nan)
        t20, t1, t2 = grp(rk[:k]); u20, u1, u2 = grp(np.flatnonzero(M[T]))
        rows.append(dict(T=T, D=t20 - u20, D1=t1 - u1, D2=t2 - u2))
    return pd.DataFrame(rows), excl


# ---------------- 데이터 ----------------
def sh(cmd):
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout, r.stderr); raise RuntimeError(cmd)
    return r.stdout.strip()


def clone():
    base = "https://github.com/stanleyim/new.git"
    if not os.path.exists("/content/main/data/ohlcv_full.parquet"):
        sh(f"git clone -q --depth 1 --single-branch -b main {base} /content/main")
    if not os.path.isdir("/content/delisted/delisted_data"):
        sh(f"git clone -q --depth 1 --single-branch -b delisted-data {base} /content/delisted")
    if not glob.glob("/content/pit/pit_data_extra/sector_*.parquet"):
        if os.path.isdir("/content/pit/.git"):
            sh("git -C /content/pit sparse-checkout add 'pit_data_extra/sector_*.parquet'")
        else:
            sh(f"git clone -q --depth 1 --single-branch --filter=blob:none --sparse -b pit-data {base} /content/pit")
            sh("git -C /content/pit sparse-checkout set --no-cone 'pit_data_extra/sector_*.parquet'")
    n1 = len(glob.glob("/content/main/data/ohlcv_full.parquet")); n2 = len(glob.glob("/content/delisted/delisted_data/*.parquet"))
    n3 = len(glob.glob("/content/pit/pit_data_extra/sector_*.parquet"))
    hs = [sh(f"git -C /content/{x} rev-parse --short HEAD") for x in ("main", "delisted", "pit")]
    print(f"클론: ohlcv {n1} · KIS {n2:,} · sector {n3} (기대 1 · 2,916 · 13) · 커밋 main {hs[0]} · delisted {hs[1]} · pit {hs[2]}")
    assert (n1, n2, n3) == (1, 2916, 13), "파일 수가 기대값과 다름 → 중단"


def load():
    m = pd.read_parquet("/content/main/data/ohlcv_full.parquet", columns=["date", "종가", "거래량", "ticker"])
    m = pd.DataFrame({"date": norm_date(m["date"]), "ticker": norm_ticker(m["ticker"]),
                      "close": m["종가"].astype(float).values, "volume": m["거래량"].astype(float).values, "src": 0})
    parts = []
    for f in sorted(glob.glob("/content/delisted/delisted_data/*.parquet")):
        k = pd.read_parquet(f, columns=["date", "close", "volume"])
        parts.append(pd.DataFrame({"date": norm_date(k["date"]), "ticker": norm_ticker([os.path.basename(f)[:-8]])[0],
                                   "close": k["close"].astype(float).values, "volume": k["volume"].astype(float).values, "src": 1}))
    k = pd.concat(parts, ignore_index=True); del parts
    t439, tkis = set(m["ticker"]), set(k["ticker"])
    print(f"로스터: main {len(t439)} · KIS {len(tkis):,} · 합집합 {len(t439 | tkis):,} (기대 3,355) · 겹침 {len(t439 & tkis)}")
    L = pd.concat([m, k], ignore_index=True); del m, k
    L = L.sort_values(["date", "ticker", "src"]).drop_duplicates(["date", "ticker"], keep="first")   # 겹치면 main 우선
    tradable = L[(L["close"] > 0) & (L["volume"] > 0) & (L["date"] <= DATA_END)]
    last_trade = tradable.groupby("ticker")["date"].max()                                        # 폐지 판정용(날짜만)
    cal = pd.DatetimeIndex(np.sort(L.loc[(L["src"] == 0) & (L["date"] >= CAL_START) & (L["date"] <= CUT), "date"].unique()))
    L = L[(L["date"] >= CAL_START) & (L["date"] <= CUT)]
    out_cal = int((~L["date"].isin(cal)).sum())
    L = L[L["date"].isin(cal)]
    tick = sorted(t for t in L["ticker"].unique() if is_common(t))
    L = L[L["ticker"].isin(set(tick))]
    C = np.array(L.pivot(index="date", columns="ticker", values="close").reindex(index=cal, columns=tick).values, dtype=float, copy=True)
    V = np.array(L.pivot(index="date", columns="ticker", values="volume").reindex(index=cal, columns=tick).values, dtype=float, copy=True)
    C[~(C > 0)] = np.nan
    del L
    big = 10 ** 9; pos = {d: i for i, d in enumerate(cal)}
    lt = np.full(len(tick), -1, int)
    for j, t in enumerate(tick):
        if t in last_trade.index:
            dd = pd.Timestamp(last_trade[t])
            lt[j] = big if dd > CUT else int(cal.searchsorted(dd, side="right") - 1)
    sec = pd.concat([pd.read_parquet(f, columns=["date", "ticker", "종목명"]) for f in sorted(glob.glob("/content/pit/pit_data_extra/sector_*.parquet"))], ignore_index=True)
    sec = pd.DataFrame({"date": norm_date(sec["date"]), "ticker": norm_ticker(sec["ticker"]), "종목명": sec["종목명"].values})
    sec = sec[sec["date"] <= CUT]
    print(f"적재: 거래일 {len(cal):,} ({cal[0].date()}~{cal[-1].date()}) · 보통주 {len(tick):,} · 달력 밖 행 {out_cal:,} 제외 · 스냅샷 {sec['date'].nunique()} · 메모리 {mem()}")
    return cal, tick, C, V, lt, sec


# ---------------- 실행 ----------------
def main():
    t_start = time.time()
    from google.colab import drive
    drive.mount("/content/drive")
    os.makedirs(OUT, exist_ok=True)
    clone()
    cal, tick, C, V, lt, sec = load()
    R = daily_ret(C)
    spac = build_spac(sec, cal, tick)
    M, a60, cnt = build_universe(C, V, spac, np.ones(len(tick), bool))
    ix, ma, t0, nmem = build_index(R, M)
    F2, ranks = build_f2(R, M, a60)
    allow = allow_from_index(ix, ma)
    print(f"계산 완료 · 메모리 {mem()} · {time.time() - t_start:.0f}s")

    # ---- 0단계 점검 ----
    if t0 is None:
        print("중단: D450을 정의할 수 있는 날이 없음"); return
    ma_first = int(np.flatnonzero(np.isfinite(ma))[0]) if np.isfinite(ma).any() else None
    rs = cal.searchsorted(pd.Timestamp(SEGS[0][1]))
    ncomp = np.array([len(r) for r in ranks])
    f2miss = 1.0 - ncomp[rs:].sum() / max(cnt[rs:].sum(), 1)
    print("\n[0단계 점검]")
    print(f"  T0(지수 시작) {cal[t0].date()} · MA200 첫 날짜 {cal[ma_first].date() if ma_first is not None else '없음'} · 스팩 표시 종목-일 {int(spac.sum()):,}")
    print(f"  D450 종목 수(2015-01~2024-12): 최소 {cnt[rs:].min()} · 450 미만 {int((cnt[rs:] < TOPN).sum())}일 · 지수 구성 종목 수 최소 {nmem[rs:].min()} · 중앙 {int(np.median(nmem[rs:]))}")
    print(f"  F2 결측률(D450 중) {f2miss:.2%} · F2 순위 종목 수 최소 {ncomp[rs:].min()} · 시장 룰 매수 중단일(2015-01~2024-12) {int((~allow[rs - 1:-1]).sum())}")
    if ncomp[rs:].min() == 0:
        print("중단: F2를 만들 수 없는 날이 있음"); return

    # ---- 1단계 ----
    res = {}; allpass = True; lines = []; saved = {}
    for name, s, e in SEGS:
        si = int(cal.searchsorted(pd.Timestamp(s))); ei = int(cal.searchsorted(pd.Timestamp(e), side="right") - 1)
        seg = np.arange(si, ei + 1)
        mi = metrics(ix[si:ei + 1] / ix[si - 1])
        on = simulate(seg, C, V, ranks, allow, lt); off = simulate(seg, C, V, ranks, None, lt)
        on_ref = simulate(seg, C, V, ranks, allow, lt, writeoff_zero=False)
        mo, mf, mr = metrics(on["V"]), metrics(off["V"]), metrics(on_ref["V"])
        ok, cc = judge(mo, mi); allpass &= ok
        res[name] = dict(seg=(str(cal[si].date()), str(cal[ei].date())), on=mo, off=mf, on_ref=mr, idx=mi, pass_=ok, conds=cc, st_on=on["st"], st_off=off["st"])
        saved[name] = (seg, on, off)
        f = lambda m: f"CAGR {m['CAGR']:+.1%} · Sharpe {m['Sharpe']:.2f} · MDD {m['MDD']:.1%}"
        lines.append(f"  {name} ({cal[si].date()}~{cal[ei].date()}, {mo['n']}일)\n"
                     f"    ON  {f(mo)} | 판정 {'통과' if ok else '실패'} (CAGR>0 {'O' if cc[0] else 'X'} · MDD≥−25% {'O' if cc[1] else 'X'} · Sharpe≥지수 {'O' if cc[2] else 'X'})\n"
                     f"    OFF {f(mf)}\n    지수 {f(mi)} (참고 기록 동일가중 CAGR {REF_EW_CAGR[name]:+.1%})"
                     + (f"\n    참고: 폐지를 마지막 거래 종가로 청산하면 ON {f(mr)}" if on["st"]["writeoff"] + on["st"]["writeoff_end"] else ""))
    print("\n[1단계 결과] 판정 = 시장 룰 ON")
    print("\n".join(lines))
    print(f"\n판정(사전 고정): {'통과 → 2단계(E1 1회) 셀 승인 대기' if allpass else '실패 → New3 종료(수정·재시도 없음)'}")

    # ---- 진단 ----
    print("\n[진단] 판정에 쓰지 않음")
    rows_tr = []; rows_y = []; rows_q = []
    for name, s, e in SEGS:
        seg, on, off = saved[name]; st = on["st"]; td = on["trades"]
        Vv = on["V"]; inv = 1.0 - on["cash"] / np.where(Vv > 0, Vv, np.nan)
        clo = td[td["how"].isin(["close", "delayed", "writeoff"])] if len(td) else td
        yrs = (len(seg) / 252.0)
        print(f"  {name} ON: 매수 {st['buys']} · 청산 {len(clo)} · 승률 {(clo['net'] > 0).mean():.1%} · 거래 평균 {clo['net'].mean():+.2%} · 꼬리(≤−20%) {int((clo['net'] <= -0.2).sum())}"
              f" · 미체결 {st['nonfill']} · 단차 중립화 {st['jump']} · 폐지 −100% {st['writeoff'] + st['writeoff_end']} · 청산 지연일 {st['pending_days']}")
        print(f"     평균 투자 비중 {np.nanmean(inv):.1%} · 매수 중단일 {st['blocked_days']} · 연 회전(매수액/연) {st['buy_amt'] / yrs:.2f} · 비용 합계(시작 자본 대비) {st['cost']:.2%}"
              f" | OFF 매수 {off['st']['buys']} · 평균 투자 비중 {np.nanmean(1.0 - off['cash'] / off['V']):.1%}")
        dts = cal[seg]; v = np.r_[1.0, Vv]; ivx = ix[seg[0] - 1:seg[-1] + 1]
        for y in sorted(set(dts.year)):
            k = np.flatnonzero(dts.year == y); a, b = k[0], k[-1] + 1
            vy = v[a:b + 1]; iy = ivx[a:b + 1]
            rows_y.append(dict(구간=name, 연도=y, 수익=vy[-1] / vy[0] - 1, MDD=(vy / np.maximum.accumulate(vy) - 1).min(),
                               지수수익=iy[-1] / iy[0] - 1, 지수MDD=(iy / np.maximum.accumulate(iy) - 1).min()))
        dd, excl = d20_diag(seg, C, V, R, M, ranks)
        if len(dd):
            dd["q"] = cal[dd["T"].values].to_period("Q").astype(str)
            q = dd.groupby("q")[["D", "D1", "D2"]].mean()
            tq, tdd = nw_t(q["D"].values, 1), nw_t(dd["D"].values, 20)
            print(f"     D20 분기 평균 {q['D'].mean():+.3%} · t {min(tq, tdd):.2f} (분기 {tq:.2f} · 일별 {tdd:.2f}) · 플러스 {int((q['D'] > 0).sum())}/{len(q)} · 1~10일 {q['D1'].mean():+.3%} · 11~20일 {q['D2'].mean():+.3%} · 제외 종목-일 {excl:,}")
            q = q.reset_index(); q.insert(0, "구간", name); rows_q.append(q)
        if len(td):
            t2 = td.copy(); t2["ticker"] = [tick[i] for i in t2["i"]]; t2["entry"] = cal[t2["e"].values].date; t2["exit"] = cal[t2["x"].values].date
            t2.insert(0, "구간", name); rows_tr.append(t2.drop(columns=["i", "e", "x"]))
    ydf = pd.DataFrame(rows_y)
    with pd.option_context("display.float_format", "{:+.1%}".format, "display.width", 200):
        print(ydf.to_string(index=False))

    # ---- 저장 ----
    def js(o):
        if isinstance(o, dict): return {k: js(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)): return [js(v) for v in o]
        if isinstance(o, (np.floating, float)): return float(o)
        if isinstance(o, (np.integer, int)): return int(o)
        if isinstance(o, (np.bool_, bool)): return bool(o)
        return o
    json.dump(js(dict(pass_all=allpass, T0=str(cal[t0].date()), MA200_first=str(cal[ma_first].date()) if ma_first is not None else None, segments=res)),
              open(f"{OUT}/summary.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    ydf.to_csv(f"{OUT}/yearly.csv", index=False, encoding="utf-8-sig")
    if rows_tr: pd.concat(rows_tr, ignore_index=True).to_csv(f"{OUT}/trades_on.csv", index=False, encoding="utf-8-sig")
    if rows_q: pd.concat(rows_q, ignore_index=True).to_csv(f"{OUT}/d20_quarterly.csv", index=False, encoding="utf-8-sig")
    ed = []
    for name, s, e in SEGS:
        seg, on, off = saved[name]
        ed.append(pd.DataFrame({"구간": name, "date": cal[seg].date, "V_on": on["V"], "V_off": off["V"], "cash_on": on["cash"], "index": ix[seg] / ix[seg[0] - 1]}))
    pd.concat(ed).to_csv(f"{OUT}/equity_daily.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame({"date": cal.date, "index": ix, "ma200": ma, "allow_next": allow, "d450": cnt, "f2_n": ncomp}).to_csv(f"{OUT}/index_d450.csv", index=False, encoding="utf-8-sig")
    print(f"\n저장: {OUT}/ (summary.json · yearly.csv · trades_on.csv · d20_quarterly.csv · equity_daily.csv · index_d450.csv) · 소요 {time.time() - t_start:.0f}s · 메모리 {mem()}")


if __name__ == "__main__" and os.environ.get("NEW3_NO_MAIN") != "1":
    main()
