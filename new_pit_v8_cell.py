# [New v8 PIT 유니버스 검증 셀 v2 — 메모리 절감·외국인 근사 시차 보정] 현 운용 규칙(v8: N3·T+1 종가·등락률 ≤10%·KOSDAQ 과열 회피·K5·종목당 20%·T+10·0.206%)을
# 439 고정 유니버스 대신 그날 기준(PIT) 3,355 로스터에서 돌려, 백테스트 성과 중 유니버스 선택편향 몫을 확인. 읽기 전용·레포·운용 무변경.
# 부품은 main의 sig_grid_cell.py 함수를 수정 없이 불러 씀(build_feats·hot_series·select_by_day_x·extra_mask·slot_logic·simulate_equity_close·perf).
# ===== 사전 고정 (실행 전, 결과 후 변경 금지) =====
# 경로 A = 439 원본(flow·short 그대로) / B = 439 근사(외국인 수급 = pit forgn 지분율 변화 근사, 공매도 없음 → SIG5 꺼짐)
#       C = PIT 3,355 근사(B와 같은 근사, 보통주[ticker 끝 '0']·스팩 제외[T 이하 최신 스냅샷 이름], 거래대금 30억은 원 규칙대로)
#       C2 = C에서 보유 중 데이터가 끝난(폐지) 종목을 −100%로 처리
#   외국인 근사: px_T = (지분율_T − 지분율_전거래일)/100 × 상장주식수_T × 종가_T (분할에 불변). 전거래일 행 없으면 결측.
#   시차 k(0~3): T일 순매수 근사 = px_{T+k}. k는 439에서 원 외국인합계(T)와의 상관(2017~2024)이 가장 큰 값으로 고름(수익률 미사용).
#   과거 재현용이며 운용에서는 같은 정보를 T일 KIS 순매수로 이미 앎(보유 데이터의 결제 기준 지연 보정).
#   메모리: C는 2017-01-02~2026-06-26에 20일 평균 거래대금이 한 번이라도 30억 이상인 종목만 적재(나머지는 ELIG가 될 수 없어 결과 불변).
#   근사 오차는 A vs B로, 선택편향은 B vs C(같은 근사끼리)로 본다.
# 선행조건(불일치면 중단): ① sig_grid build_all로 A 기록값 재현(설계 19.8%·0.91·−22.1% / 근접 10.5%·0.52·−23.3% / E1 102.3%·2.22·−19.4%)
#   ② 이 셀의 경로 함수가 A에서 build_all 현 조합 자산곡선과 완전 일치(설계·E1 끝 둘 다)
# 비교 구간(연속 경로, 끝 2026-06-26): 설계 = 첫 매수일 ~ 2022-12-30, 근접 = 2023-01-02 ~ 2024-12-30, E1 = 2025-01-02 ~ 2026-06-26(참고)
# 해석 규칙: C2가 설계·근접 두 구간 모두 [MDD ≥ −25% ∧ CAGR > 같은 구간 PIT 유니버스 동일가중 CAGR]이면 "유니버스 보정 후에도 유지",
#   아니면 "백테스트 성과가 439 선택편향에 의존". 이 결과로 운용 규칙을 바꾸지 않음(판단 자료).
# 한계: SIG5(공매도) 재현 불가·외국인 수급은 근사, forgn이 T일 장 마감 후 이용 가능하다고 가정, 관리종목 정보 없음.
import os, sys, gc, json, time, glob, subprocess, importlib.util
import numpy as np
import pandas as pd

BASE = os.environ.get("PITV8_BASE", "/content")
OUT = os.environ.get("PITV8_OUT", "/content/drive/MyDrive/new_pit_v8")
REPO = os.environ.get("PITV8_REPO", "https://github.com/stanleyim/new.git")
TEST = os.environ.get("PITV8_TEST") == "1"          # 합성 테스트 전용(기록값 재현 생략)
E1_START, E1_END = "2025-01-02", "2026-06-26"
LOAD_FROM = pd.Timestamp("2016-06-01")
TARGET = dict(entry="CLOSE", cap=10.0, heat=True, fz=False)
REC = {"설계": (19.8, 0.91, -22.1), "근접": (10.5, 0.52, -23.3), "E1": (102.3, 2.22, -19.4)}
DT = "datetime64[ns]"


def sh(c):
    subprocess.run(c, shell=True, check=True)


def clone():
    if not os.path.exists(f"{BASE}/main/sig_grid_cell.py"):
        sh(f"rm -rf {BASE}/main; git clone -q --depth 1 --single-branch -b main {REPO} {BASE}/main")
    if not glob.glob(f"{BASE}/delisted/delisted_data/*.parquet"):
        sh(f"rm -rf {BASE}/delisted; git clone -q --depth 1 --single-branch -b delisted-data {REPO} {BASE}/delisted")
    need = [f"{BASE}/pit/pit_data_extra/{k}_2020.parquet" for k in ("sector", "idx", "forgn")]
    if not all(os.path.exists(p) for p in need):
        sh(f"rm -rf {BASE}/pit; git clone -q --depth 1 --single-branch --filter=blob:none --sparse -b pit-data {REPO} {BASE}/pit")
        sh(f"git -C {BASE}/pit sparse-checkout set --no-cone 'pit_data_extra/sector_*.parquet' 'pit_data_extra/idx_*.parquet' 'pit_data_extra/forgn_*.parquet'")
    n = {k: len(glob.glob(f"{BASE}/pit/pit_data_extra/{k}_*.parquet")) for k in ("sector", "idx", "forgn")}
    nk = len(glob.glob(f"{BASE}/delisted/delisted_data/*.parquet"))
    print(f"클론: main {os.path.exists(f'{BASE}/main/sig_grid_cell.py')} · KIS {nk:,} · pit {n}")
    assert nk == 2916 and n["sector"] == 13 and n["forgn"] >= 12 and n["idx"] >= 10, "클론 기대값 불일치"


def load_sg():
    os.environ["SIGGRID_NO_MAIN"] = "1"
    spec = importlib.util.spec_from_file_location("sig_grid_cell", f"{BASE}/main/sig_grid_cell.py")
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    m.PIT_EXTRA = f"{BASE}/pit/pit_data_extra"
    return m


def rec_ok(p, r):
    return (abs(p["CAGR"] * 100 - r[0]) <= 0.05 + 1e-9 and abs(p["Sharpe"] - r[1]) <= 0.005 + 1e-9
            and abs(p["MDD"] * 100 - r[2]) <= 0.05 + 1e-9)


def fmt(p):
    return f"CAGR {p['CAGR']*100:+.1f}% · Sharpe {p['Sharpe']:.2f} · MDD {p['MDD']*100:.1f}%"


def zero_tail(pc_values, Clf):
    """각 열의 마지막 유효 가격 다음 날부터 0(데이터가 끝난 종목 = 폐지 → 보유분 −100%)."""
    N = pc_values.shape[0]
    valid = ~np.isnan(pc_values)
    has = valid.any(axis=0)
    last_idx = N - 1 - valid[::-1].argmax(axis=0)
    Z = Clf.copy()
    for j in np.where(has & (last_idx < N - 1))[0]:
        Z[last_idx[j] + 1:, j] = 0.0
    return Z


def path(sg, ohlcv, flow, short, end, block=None, both=False):
    """build_all의 현 조합 경로를 그대로 재현(한 조합만). block: (date,ticker)→True면 ELIG 해제.
    both=True면 같은 Feature로 '폐지 −100%' 경로도 계산해 반환 dict의 'zero'에 넣음."""
    ohlcv, flow, short, feats0, nlk = sg.build_feats(ohlcv, flow, short, end)
    hot = sg.hot_series(sg.PIT_EXTRA, end)
    feats0["hot"] = feats0["date"].map(hot).astype("boolean").fillna(False).astype(bool)
    feats = feats0[feats0["date"] >= pd.Timestamp(sg.CAL_START)].reset_index(drop=True)
    if block is not None:
        b = feats.merge(block, on=["date", "ticker"], how="left")["blk"].fillna(False).to_numpy(bool)
        feats["ELIG"] = feats["ELIG"] & ~b
    po = feats.pivot(index="date", columns="ticker", values="시가").sort_index()
    pc = feats.pivot(index="date", columns="ticker", values="종가").sort_index()
    cal, tick = po.index, po.columns
    N = len(cal)
    Clf = pc.ffill().to_numpy(dtype=float, copy=True)
    col_of = {t: j for j, t in enumerate(tick)}
    s0 = int(np.searchsorted(cal.values, np.datetime64(pd.Timestamp(sg.DESIGN_START))))
    run_idx = [i for i in range(s0, N) if i + sg.HMAX < N]
    sel = sg.select_by_day_x(feats, sg.DESIGN_START, end, sg.extra_mask(feats, TARGET["cap"], TARGET["heat"], TARGET["fz"], feats["hot"]))
    entries = sg.slot_logic(cal, sel, run_idx)[0]
    ncand = int((feats["N3"] & feats["ELIG"] & (feats["date"] >= pd.Timestamp(sg.DESIGN_START))).sum())

    def run(C_):
        sim = sg.simulate_equity_close(C_, col_of, entries, 0.20, N)
        gap = sim["cash"] - (1.0 + sum(t[4] - t[3] for t in sim["trades"]))
        assert abs(gap) < 1e-9, f"회계 항등식 불일치 {gap}"
        return sim
    out = dict(cal=cal, N=N, sim=run(Clf), feats=feats, nlk=nlk, ncand=ncand)
    if both:
        out["zero"] = dict(cal=cal, N=N, sim=run(zero_tail(pc.to_numpy(dtype=float), Clf)), feats=None, nlk=nlk, ncand=ncand)
    del po, pc, ohlcv, flow, short, feats0
    return out


def windows(cal, sim, N):
    c = cal.values
    i = lambda d: int(np.searchsorted(c, np.datetime64(pd.Timestamp(d))))
    j = lambda d: int(np.searchsorted(c, np.datetime64(pd.Timestamp(d)), side="right")) - 1
    return {"설계": (sim["first"], j("2022-12-30")), "근접": (i("2023-01-02"), j("2024-12-30")), "E1": (i(E1_START), N - 1)}


def frgn_px(fr):
    """종목·일별 지분율 변화(전거래일 대비, %p). 반환 date·ticker·dshr·상장주식수."""
    fr = fr.sort_values(["ticker", "date"]).copy()
    cal = np.unique(fr["date"].to_numpy(dtype=DT))
    pos = np.searchsorted(cal, fr["date"].to_numpy(dtype=DT))
    prev_cal = np.where(pos > 0, cal[np.maximum(pos - 1, 0)], np.datetime64("NaT", "ns"))
    g = fr.groupby("ticker", sort=False)
    prev_date = g["date"].shift(1).to_numpy(dtype=DT)
    d_share = fr["지분율"].astype(float) - g["지분율"].shift(1).astype(float)
    ok = prev_date == prev_cal
    fr["dshr"] = np.where(ok, d_share, np.nan)
    return fr[["date", "ticker", "dshr", "상장주식수"]], cal


def frgn_proxy(fx, cal, close_df, k):
    """T일 근사 = (T+k 거래일의 지분율 변화) × (T+k 상장주식수) × (T일 종가). T+k 관측값을 T일에 귀속."""
    src = fx.copy()
    pos = np.searchsorted(cal, src["date"].to_numpy(dtype=DT))
    ok = pos - k >= 0
    src = src[ok].copy()
    src["date"] = cal[pos[ok] - k]                                   # T+k 관측값을 T일에 귀속
    m = src.merge(close_df, on=["date", "ticker"], how="inner")
    m["외국인합계"] = m["dshr"] / 100.0 * m["상장주식수"].astype(float) * m["종가"].astype(float)
    return m[["date", "ticker", "외국인합계"]]


def mem():
    try:
        import psutil
        return f"{psutil.Process().memory_info().rss/1e9:.1f}GB"
    except Exception:
        return "?"


def main():
    t0 = time.time()
    os.makedirs(OUT, exist_ok=True)
    clone()
    sg = load_sg()
    tgt = sg.cname(TARGET)
    d0 = f"{BASE}/main/data"
    ohlcv = pd.read_parquet(f"{d0}/ohlcv_full.parquet")
    flow = pd.read_parquet(f"{d0}/flow_full.parquet")
    short = pd.read_parquet(f"{d0}/short_full.parquet")
    for d in (ohlcv, flow, short):
        d["date"] = pd.to_datetime(d["date"]).astype(DT); d["ticker"] = d["ticker"].astype(str)
    ohlcv = ohlcv[ohlcv["date"] >= LOAD_FROM]
    print(f"대상 조합 {tgt}")

    # ① 기록값 재현 (A, sig_grid build_all) — 끝난 뒤 메모리 해제
    P = lambda *a, **k: None
    eq_ref = {}
    for nm, end, start_rule in (("설계", sg.DESIGN_END, "common"), ("근접", sg.NEAR_END, sg.NEAR_START), ("E1", E1_END, E1_START)):
        b = sg.build_all(ohlcv, flow, short, end, None, P, "")
        if start_rule == "common":
            names = [sg.cname(c) for c in sg.combos()]
            s = max(b["sims"][k]["first"] for k in ["BASE"] + names)
        else:
            s = int(np.searchsorted(b["cal"].values, np.datetime64(pd.Timestamp(start_rule))))
        p = sg.perf(b["sims"][tgt]["ret"], s, b["N"] - 1)
        ok = rec_ok(p, REC[nm])
        print(f"[①] A {nm} 기록값 재현: {fmt(p)} · 기록 {REC[nm]} → {'일치' if ok else '불일치'}")
        if not TEST:
            assert ok, f"① {nm} 기록값 불일치 — 중단"
        if nm in ("설계", "E1"):
            eq_ref[nm] = b["sims"][tgt]["eq"].copy()
        del b; gc.collect()
    # ② 경로 함수 = build_all 현 조합
    for nm, end in (("설계", sg.DESIGN_END), ("E1", E1_END)):
        a = path(sg, ohlcv, flow, short, end)
        assert np.allclose(a["sim"]["eq"], eq_ref[nm], rtol=1e-12, atol=1e-12), f"② 경로 불일치({nm})"
    print(f"[②] 경로 함수 = build_all 현 조합 자산곡선 일치(설계·E1 끝) · 메모리 {mem()}")
    A = a
    featsA = A["feats"][["date", "ticker", "frgn_z", "N3", "SIG3", "SIG5"]].copy()
    A["feats"] = None; gc.collect()

    # 외국인 근사 원천(pit forgn) → 지분율 변화만 남기고 원본 해제
    fr = pd.concat([pd.read_parquet(f, columns=["date", "ticker", "지분율", "상장주식수"])
                    for f in sorted(glob.glob(f"{BASE}/pit/pit_data_extra/forgn_*.parquet"))], ignore_index=True)
    fr["date"] = pd.to_datetime(fr["date"]).astype(DT); fr["ticker"] = fr["ticker"].astype(str)
    fr = fr[fr["date"] >= LOAD_FROM]
    print(f"forgn: {len(fr):,}행 · 종목 {fr['ticker'].nunique():,} · {fr['date'].min().date()}~{fr['date'].max().date()}")
    fx, fcal = frgn_px(fr)
    del fr; gc.collect()

    # 시차 k 선택(439, 원 외국인합계와 상관만, 2017~2024)
    fl = flow[(flow["date"] >= pd.Timestamp("2017-01-01")) & (flow["date"] <= pd.Timestamp("2024-12-30"))][["date", "ticker", "외국인합계"]]
    corr = {}
    for k in (0, 1, 2, 3):
        pk = frgn_proxy(fx, fcal, ohlcv[["date", "ticker", "종가"]], k)
        m = fl.merge(pk, on=["date", "ticker"], how="inner", suffixes=("", "_px"))
        corr[k] = (m["외국인합계"].corr(m["외국인합계_px"]), m["외국인합계"].corr(m["외국인합계_px"], method="spearman"), len(m))
    K = max(corr, key=lambda k: corr[k][0])
    print("[시차 선택] 원 외국인합계(T)와 근사(T+k) 상관 — " + " · ".join(f"k={k}: {c[0]:.3f}(순위 {c[1]:.3f}, {c[2]:,}행)" for k, c in corr.items()) + f" → k={K}")
    del fl; gc.collect()

    # B: 439 근사
    fb = frgn_proxy(fx, fcal, ohlcv[["date", "ticker", "종가"]], K)
    flowB = ohlcv[["date", "ticker"]].merge(fb, on=["date", "ticker"], how="left").assign(기관합계=np.nan, 개인=np.nan)
    shortB = pd.DataFrame({"date": pd.Series(dtype=DT), "ticker": pd.Series(dtype=str), "공매도": pd.Series(dtype=float), "비중": pd.Series(dtype=float)})
    B = path(sg, ohlcv, flowB, shortB, E1_END)
    j = featsA.merge(B["feats"][["date", "ticker", "frgn_z", "N3", "SIG3"]], on=["date", "ticker"], suffixes=("", "_B"))
    j = j[j["date"] >= pd.Timestamp(sg.DESIGN_START)]
    ea = {(t[0], t[1]) for t in A["sim"]["trades"]}
    eb = {(t[0], t[1]) for t in B["sim"]["trades"]}
    print(f"[근사 품질 k={K}] 439 근사 가능 {flowB['외국인합계'].notna().mean():.1%} · frgn_z 상관 {j['frgn_z'].corr(j['frgn_z_B']):.3f} · "
          f"N3 원본 {int(j['N3'].sum()):,} vs 근사 {int(j['N3_B'].sum()):,} · 체결 A {len(ea)} · B {len(eb)} · 공통 {len(ea & eb)} ({len(ea & eb)/max(1, len(ea)):.1%} of A)")
    B["feats"] = None; del featsA, j, flowB, fb; gc.collect()

    # C: PIT — KIS 적재(필요 열만), 한 번이라도 30억 이상인 종목만
    ks = []
    for f in sorted(glob.glob(f"{BASE}/delisted/delisted_data/*.parquet")):
        k = pd.read_parquet(f)
        k["ticker"] = os.path.basename(f)[:-8]
        ks.append(k)
    k = pd.concat(ks, ignore_index=True); del ks
    k["date"] = pd.to_datetime(k["date"]).astype(DT)
    last_row = k.groupby("ticker")["date"].max()
    grpA = set(last_row[last_row.dt.year <= 2025].index)
    k = k.sort_values(["ticker", "date"])
    k["등락률"] = (k["close"] / k.groupby("ticker")["close"].shift(1) - 1.0) * 100.0
    k = k.rename(columns={"open": "시가", "high": "고가", "low": "저가", "close": "종가", "volume": "거래량"})
    cols = ["date", "ticker", "시가", "고가", "저가", "종가", "거래량", "등락률"]
    k = k[(k["date"] >= LOAD_FROM) & (k["date"] <= pd.Timestamp(E1_END))][cols]
    oc = pd.concat([ohlcv[cols], k], ignore_index=True); del k; gc.collect()
    oc = oc[oc["ticker"].str[-1] == "0"]
    assert not oc.duplicated(["date", "ticker"]).any(), "C (date,ticker) 중복"
    oc = oc.sort_values(["ticker", "date"])
    tv20 = (oc["종가"] * oc["거래량"]).groupby(oc["ticker"]).transform(lambda x: x.rolling(20).mean())
    win = (oc["date"] >= pd.Timestamp(sg.CAL_START)) & (oc["date"] <= pd.Timestamp(E1_END))
    ever = set(oc.loc[win & (tv20 >= sg.LIQ_TH), "ticker"].unique())
    n_all = oc["ticker"].nunique()
    oc = oc[oc["ticker"].isin(ever)].reset_index(drop=True); del tv20; gc.collect()
    print(f"C 적재: 보통주 {n_all:,}종목 중 30억 이상 이력 {len(ever):,}종목 · {len(oc):,}행 · 메모리 {mem()}")
    sct = pd.concat([pd.read_parquet(f, columns=["date", "ticker", "종목명"]) for f in sorted(glob.glob(f"{BASE}/pit/pit_data_extra/sector_*.parquet"))])
    sct["date"] = pd.to_datetime(sct["date"]).astype(DT); sct["ticker"] = sct["ticker"].astype(str)
    blk = pd.merge_asof(oc[["date", "ticker"]].sort_values("date"), sct[sct["ticker"].isin(ever)].sort_values("date"),
                        on="date", by="ticker", direction="backward")
    blk["blk"] = blk["종목명"].fillna("").astype(str).str.contains("스팩")
    blk = blk[["date", "ticker", "blk"]]; del sct
    fc = frgn_proxy(fx, fcal, oc[["date", "ticker", "종가"]], K)
    flowC = oc[["date", "ticker"]].merge(fc, on=["date", "ticker"], how="left").assign(기관합계=np.nan, 개인=np.nan)
    del fc, fx; gc.collect()
    print(f"C 외국인 근사 가능 {flowC['외국인합계'].notna().mean():.1%} · 스팩 행 {int(blk['blk'].sum()):,} · 메모리 {mem()}")
    C = path(sg, oc, flowC, shortB, E1_END, block=blk, both=True)
    C2 = C.pop("zero")
    del flowC; gc.collect()
    print(f"C 계산 완료 · 메모리 {mem()}")

    # PIT 유니버스 동일가중 벤치마크(전일 ELIG∧비스팩 종목의 당일 수익 평균, 하루 |수익|>30.5% 제외)
    f = C["feats"][["date", "ticker", "종가", "ELIG"]].sort_values(["ticker", "date"]).copy()
    g = f.groupby("ticker")
    f["r"] = f["종가"] / g["종가"].shift(1) - 1.0
    f["el_prev"] = g["ELIG"].shift(1).fillna(False).astype(bool)
    f = f[f["el_prev"] & f["r"].notna() & (f["r"].abs() <= 0.305)]
    bench = f.groupby("date")["r"].mean().reindex(C["cal"]).fillna(0.0).to_numpy()
    del f; C["feats"] = None; gc.collect()

    def wperf(X):
        return {kk: sg.perf(X["sim"]["ret"], a0, b0) for kk, (a0, b0) in windows(X["cal"], X["sim"], X["N"]).items()}
    res = {nm: wperf(X) for nm, X in (("A 439 원본", A), (f"B 439 근사(k={K})", B), ("C PIT 근사", C), ("C2 PIT 근사·폐지 −100%", C2))}
    wb = windows(C["cal"], C["sim"], C["N"])
    resb = {kk: sg.perf(bench, a0, b0) for kk, (a0, b0) in wb.items()}
    print("\n[결과] 연속 경로(끝 2026-06-26) 구간별 — 설계 / 근접 / E1(참고)")
    for nm, r in res.items():
        print(f"  {nm:<22} " + " | ".join(f"{kk} {fmt(v)}" for kk, v in r.items()))
    print(f"  {'PIT 유니버스 동일가중':<22} " + " | ".join(f"{kk} CAGR {v['CAGR']*100:+.1f}% · MDD {v['MDD']*100:.1f}%" for kk, v in resb.items()))
    for nm, X in (("A", A), ("B", B), ("C", C), ("C2", C2)):
        print(f"  {nm}: 후보(N3∧ELIG, 설계 시작 이후) {X['ncand']:,} · 체결 {len(X['sim']['trades'])} · 미체결 {X['sim']['no_fill']} · "
              f"평균 보유 {X['sim']['npos'][X['sim']['first']:].mean():.2f}")
    tr = pd.DataFrame(C2["sim"]["trades"], columns=["E", "ticker", "net", "alloc", "proceeds"])
    t439 = set(ohlcv["ticker"].unique())
    tr["grp"] = np.where(tr["ticker"].isin(t439), "439", np.where(tr["ticker"].isin(grpA), "A 폐지", "B 기타"))
    tr["date"] = C2["cal"][tr["E"].to_numpy()]
    print("  C2 거래 그룹별(편입일 기준 전 기간): " + " · ".join(
        f"{gname} {len(gg)}건 평균 {gg['net'].mean()*100:+.2f}% 꼬리(≤−20%) {int((gg['net']<=-0.2).sum())} −100% {int((gg['net']<=-0.999).sum())}"
        for gname, gg in tr.groupby("grp")))
    c2 = res["C2 PIT 근사·폐지 −100%"]
    keep = all(c2[kk]["MDD"] >= -0.25 and c2[kk]["CAGR"] > resb[kk]["CAGR"] for kk in ("설계", "근접"))
    print(f"\n해석(사전 고정 규칙): {'유니버스 보정 후에도 유지' if keep else '백테스트 성과가 439 선택편향에 의존'} "
          f"(C2 설계·근접 각각 MDD ≥ −25% ∧ CAGR > PIT 동일가중) · 운용 규칙 변경 없음")
    tr.to_csv(f"{OUT}/C2_trades.csv", index=False, encoding="utf-8-sig")
    json.dump({"K": K, "corr": corr, "res": res, "bench": resb, "keep": keep}, open(f"{OUT}/pit_v8_summary.json", "w"),
              ensure_ascii=False, indent=1, default=str)
    print(f"저장: {OUT}/ · 소요 {time.time()-t0:.0f}s · 메모리 {mem()}")


if os.environ.get("PITV8_NO_MAIN") != "1":
    try:
        from google.colab import drive
        drive.mount("/content/drive")
    except Exception:
        pass
    main()
