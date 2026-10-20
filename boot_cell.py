# [③ 부트스트랩 MDD 분포 셀] 읽기 전용. 레포·운용 무변경. 선행: boot_clone_cell.py 실행(/content/main, /content/pit idx).
# ===== 사전등록 (실행 전 고정, 결과 후 변경 금지) =====
# 목적: 현 운용 조합의 120·250거래일 수익·MDD 기대 분포 -> 포워드 v8 판정 참고표. 합격/탈락 판정 없음, 운용 규칙 불변.
# 대상: sig_grid_cell.py의 CLOSE|cap=10.0|heat=Y|fz=- (T+1 종가·등락률 ≤10%·KOSDAQ 과열 회피·외국인 필터 없음), 종목당 20%, K_MAX 5, T+10, 비용 0.206%.
#   sig_grid_cell.py는 수정 없이 불러와 build_all(sr_path=None)만 사용(현 signal_runner는 연구 당시 규칙과 달라 대조 생략).
# 선행조건(하나라도 불일치면 AssertionError, 분포 미산출):
#   ① 설계 기준선 재현 CAGR 0.2921·MDD -0.5455 (sig_grid와 같은 허용오차 6e-5)
#   ② 설계 현 조합(공통 평가구간) 기록값 CAGR 19.8%·Sharpe 0.91·MDD -22.1% (기록 자릿수 반올림 일치)
#   ③ 근접 연속 경로 = 설계 경로 [0, 설계 last_run+1] (rtol 1e-9)
#   ④ 근접 현 조합(2023-01-02~) 기록값 CAGR 10.5%·Sharpe 0.52·MDD -23.3% (반올림 일치)
#   ⑤ 회계 항등식(build_all 내부 assert)
# 표본: 근접 연속 경로(2018~2024-12-30)의 현 조합 일별 수익, 설계 공통 평가 시작일 ~ 마지막 편입 가능일 다음날(last_run+1).
#   그 뒤 날들은 신규 편입 없이 보유만 줄어드는 정리 구간이라 제외.
# 방법: 원형 블록 부트스트랩(일별, 블록 길이 고정). 주 결과 L=20, 민감도 L=10·40 병기. 경로 10,000개, seed 20261020.
#   기간 H=120·250거래일. 각 경로: 수익(누적), MDD(시작 자산 1을 고점에 포함), -20%·-35% 도달 여부.
#   산출: 수익·MDD 5/25/50/75/95 분위, P(수익<0), P(MDD≤-20%), P(MDD≤-35%).
#   병기(검산용): 같은 표본의 실제 롤링 H일 창 통계(겹치는 창).
# 참고(E1, 판정·분포에 미사용): 2026-06-26까지 경로 재계산 -> E1 기록값(102.3%·2.22·-19.4%) 반올림 일치하면 E1 구간 실제 롤링 H일 창 통계만 표시,
#   불일치면 "E1 참고 생략"(주 결과에 영향 없음). 6/29 이후 데이터는 적재하지 않음.
# 한계: 설계·근접은 이미 본 구간(E2). 과거 국면이 반복된다는 가정. 블록보다 긴 국면 지속성은 일부 끊김. 포워드는 빈 계좌에서 시작(초기 보유 적음)하나
#   표본은 대부분 정상 운용 상태. 슬리피지·실거래 위험필터 미반영.
import os, sys, time, json, importlib.util
import numpy as np
import pandas as pd

MAIN_DIR = os.environ.get("BOOT_MAIN", "/content/main")
PIT_EXTRA = os.environ.get("BOOT_PIT", "/content/pit/pit_data_extra")
TARGET = dict(entry="CLOSE", cap=10.0, heat=True, fz=False)
REC_DESIGN = {"CAGR": 19.8, "Sharpe": 0.91, "MDD": -22.1}
REC_NEAR = {"CAGR": 10.5, "Sharpe": 0.52, "MDD": -23.3}
REC_E1 = {"CAGR": 102.3, "Sharpe": 2.22, "MDD": -19.4}
E1_START, E1_END = "2025-01-02", "2026-06-26"
HORIZONS = (120, 250)
BLOCKS = (20, 10, 40)          # 첫 값이 주 결과
NPATH = 10_000
SEED = 20261020
PCTS = (5, 25, 50, 75, 95)
WARN, STOP = -0.20, -0.35


def load_sg(path):
    os.environ["SIGGRID_NO_MAIN"] = "1"
    spec = importlib.util.spec_from_file_location("sig_grid_cell", path)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    m.PIT_EXTRA = PIT_EXTRA
    return m


def rec_match(p, rec):
    """기록 자릿수 반올림 일치: CAGR·MDD는 % 소수 1자리, Sharpe는 소수 2자리."""
    return (abs(p["CAGR"] * 100 - rec["CAGR"]) <= 0.05 + 1e-9 and abs(p["Sharpe"] - rec["Sharpe"]) <= 0.005 + 1e-9
            and abs(p["MDD"] * 100 - rec["MDD"]) <= 0.05 + 1e-9)


def fmt(p):
    return f"CAGR {p['CAGR']*100:.2f}% · Sharpe {p['Sharpe']:.3f} · MDD {p['MDD']*100:.2f}%"


def path_stats(R):
    """R: (경로수, H) 일별 수익 -> 누적수익, MDD(시작 1 포함)."""
    eq = np.cumprod(1.0 + R, axis=1)
    peak = np.maximum(np.maximum.accumulate(eq, axis=1), 1.0)
    return eq[:, -1] - 1.0, (eq / peak - 1.0).min(axis=1)


def boot_paths(r, H, L, npath, rng):
    """원형 블록 부트스트랩: 블록 시작을 균등 추출, L일 연속(끝에서 처음으로 이어짐), H일로 자름."""
    n = len(r); nb = -(-H // L)
    st = rng.integers(0, n, size=(npath, nb))
    idx = (st[:, :, None] + np.arange(L)[None, None, :]) % n
    return r[idx.reshape(npath, nb * L)[:, :H]]


def rolling_paths(r, H):
    n = len(r)
    if n < H:
        return np.empty((0, H))
    return np.lib.stride_tricks.sliding_window_view(r, H)


def summarize(ret, mdd):
    d = {"n": int(len(ret))}
    for q in PCTS:
        d[f"ret_p{q}"] = float(np.percentile(ret, q))
    for q in PCTS:
        d[f"mdd_p{q}"] = float(np.percentile(mdd, q))
    d["P(ret<0)"] = float((ret < 0).mean())
    d["P(MDD<=-20%)"] = float((mdd <= WARN).mean())
    d["P(MDD<=-35%)"] = float((mdd <= STOP).mean())
    return d


def run_boot(r, npath=None, seed=SEED):
    npath = NPATH if npath is None else npath
    rows = []
    for H in HORIZONS:
        for L in BLOCKS:
            rng = np.random.default_rng([seed, H, L])
            ret, mdd = path_stats(boot_paths(r, H, L, npath, rng))
            rows.append({"구분": f"부트 L={L}", "H": H, **summarize(ret, mdd)})
        rp = rolling_paths(r, H)
        if len(rp):
            ret, mdd = path_stats(rp)
            rows.append({"구분": "실제 롤링", "H": H, **summarize(ret, mdd)})
    return pd.DataFrame(rows)


def print_table(df, P):
    for H in HORIZONS:
        P(f"\n[{H}거래일]  수익 분위(5/25/50/75/95) | MDD 분위(5/25/50/75/95) | P(수익<0) · P(MDD≤-20%) · P(MDD≤-35%) · 표본")
        for _, x in df[df["H"] == H].iterrows():
            P(f"  {x['구분']:<10} " + "/".join(f"{x[f'ret_p{q}']*100:+.1f}" for q in PCTS) + " | "
              + "/".join(f"{x[f'mdd_p{q}']*100:.1f}" for q in PCTS)
              + f" | {x['P(ret<0)']*100:.1f}% · {x['P(MDD<=-20%)']*100:.1f}% · {x['P(MDD<=-35%)']*100:.1f}% · {int(x['n'])}")


def main(expect=True, e1_ref=True, out_dir=None, verbose=True):
    t0 = time.time()
    P = print if verbose else (lambda *a, **k: None)
    sg = load_sg(f"{MAIN_DIR}/sig_grid_cell.py")
    tgt = sg.cname(TARGET)
    assert tgt in [sg.cname(c) for c in sg.combos()], f"대상 조합 없음 {tgt}"
    d0 = f"{MAIN_DIR}/data"
    ohlcv = pd.read_parquet(f"{d0}/ohlcv_full.parquet")
    flow = pd.read_parquet(f"{d0}/flow_full.parquet")
    short = pd.read_parquet(f"{d0}/short_full.parquet")
    P(f"대상 조합 {tgt}")

    # 설계 경로
    d = sg.build_all(ohlcv, flow, short, sg.DESIGN_END, None, P, "설계 ")
    names = [sg.cname(c) for c in sg.combos()]
    if expect:
        pb = sg.perf(d["sims"]["BASE"]["ret"], d["sims"]["BASE"]["first"], d["N"] - 1)
        assert abs(pb["CAGR"] - sg.EXPECTED_BASE["CAGR"]) < 6e-5 and abs(pb["MDD"] - sg.EXPECTED_BASE["MDD"]) < 6e-5, f"① 기준선 재현 불일치 {pb}"
        P(f"[①] 기준선 재현 OK (CAGR {pb['CAGR']:.4f}, MDD {pb['MDD']:.4f})")
    start = max(d["sims"][k]["first"] for k in ["BASE"] + names)
    pdz = sg.perf(d["sims"][tgt]["ret"], start, d["N"] - 1)
    P(f"설계 현 조합 ({d['cal'][start].date()}~{d['cal'][d['N']-1].date()}): {fmt(pdz)}")
    if expect:
        assert rec_match(pdz, REC_DESIGN), f"② 설계 기록값 불일치: {fmt(pdz)} vs {REC_DESIGN}"
        P("[②] 설계 기록값 일치 OK")

    # 근접 연속 경로
    n = sg.build_all(ohlcv, flow, short, sg.NEAR_END, None, P, "근접 ")
    Ld = d["last_run"] + 1
    assert np.allclose(n["sims"][tgt]["eq"][:Ld + 1], d["sims"][tgt]["eq"][:Ld + 1], rtol=1e-9, atol=1e-12), "③ 연속 경로≠설계 경로"
    assert n["cal"][start] == d["cal"][start]
    P(f"[③] 근접 연속 경로 = 설계 경로 [0,{Ld}] OK")
    e0 = int(np.searchsorted(n["cal"].values, np.datetime64(pd.Timestamp(sg.NEAR_START))))
    pnz = sg.perf(n["sims"][tgt]["ret"], e0, n["N"] - 1)
    P(f"근접 현 조합 ({n['cal'][e0].date()}~{n['cal'][n['N']-1].date()}): {fmt(pnz)}")
    if expect:
        assert rec_match(pnz, REC_NEAR), f"④ 근접 기록값 불일치: {fmt(pnz)} vs {REC_NEAR}"
        P("[④] 근접 기록값 일치 OK")

    # 표본
    end = n["last_run"] + 1
    r = np.asarray(n["sims"][tgt]["ret"][start:end + 1], dtype=float)
    npos = np.asarray(n["sims"][tgt]["npos"][start:end + 1])
    assert np.isfinite(r).all() and len(r) > max(HORIZONS) * 2, "표본 이상"
    ps = sg.perf(n["sims"][tgt]["ret"], start, end)
    P(f"\n표본 {n['cal'][start].date()} ~ {n['cal'][end].date()} · {len(r)}일 · 일평균 {r.mean():.5f} · 일표준편차 {r.std(ddof=1):.5f} · "
      f"평균 보유 {npos.mean():.2f}종목 · 무보유일 {np.mean(npos == 0)*100:.1f}% · {fmt(ps)}")

    tab = run_boot(r)
    P(f"\n===== 분포 (경로 {NPATH:,} · seed {SEED} · 주 결과 L={BLOCKS[0]}, L={BLOCKS[1]}·{BLOCKS[2]}는 민감도, 실제 롤링은 검산용, 단위 %) =====")
    print_table(tab, P)

    if out_dir is None:
        try:
            from google.colab import drive
            drive.mount("/content/drive")
            out_dir = "/content/drive/MyDrive/new_boot_out"
        except Exception as ex:
            out_dir = "/content/new_boot_out"
            P(f"Drive 마운트 실패({ex}) -> 로컬 {out_dir}")
    os.makedirs(out_dir, exist_ok=True)
    tab.to_csv(f"{out_dir}/boot_summary.csv", index=False)
    pd.DataFrame({"date": n["cal"][start:end + 1], "ret": r, "npos": npos}).to_csv(f"{out_dir}/sample_daily_returns.csv", index=False)
    meta = {"target": tgt, "sample": [str(n["cal"][start].date()), str(n["cal"][end].date()), int(len(r))],
            "design": pdz, "near": pnz, "npath": NPATH, "seed": SEED, "blocks": BLOCKS, "horizons": HORIZONS}

    # E1 참고
    if e1_ref:
        P("\n===== 참고: E1 (2025-01-02~2026-06-26, 분포 미사용) =====")
        e = sg.build_all(ohlcv, flow, short, E1_END, None, P, "E1 ")
        Ln = n["last_run"] + 1
        cont = np.allclose(e["sims"][tgt]["eq"][:Ln + 1], n["sims"][tgt]["eq"][:Ln + 1], rtol=1e-9, atol=1e-12)
        s1 = int(np.searchsorted(e["cal"].values, np.datetime64(pd.Timestamp(E1_START))))
        pe = sg.perf(e["sims"][tgt]["ret"], s1, e["N"] - 1)
        P(f"E1 현 조합 ({e['cal'][s1].date()}~{e['cal'][e['N']-1].date()}): {fmt(pe)} · 연속 경로 일치 {cont}")
        if cont and rec_match(pe, REC_E1):
            re1 = np.asarray(e["sims"][tgt]["ret"][s1:e["last_run"] + 2], dtype=float)
            rows = []
            for H in HORIZONS:
                rp = rolling_paths(re1, H)
                if len(rp):
                    ret, mdd = path_stats(rp)
                    rows.append({"구분": "E1 실제 롤링", "H": H, **summarize(ret, mdd)})
            te = pd.DataFrame(rows)
            if len(te):
                print_table(te, P)
                te.to_csv(f"{out_dir}/e1_rolling_ref.csv", index=False)
            meta["e1"] = pe
        else:
            P(f"E1 기록값({REC_E1}) 또는 연속 경로 불일치 -> E1 참고 생략(주 결과 영향 없음)")
            meta["e1"] = "생략"
    json.dump(meta, open(f"{out_dir}/boot_meta.json", "w"), ensure_ascii=False, indent=2, default=str)
    P(f"\n총 {time.time()-t0:.0f}s · 저장: {out_dir}")
    return tab, r


if os.environ.get("BOOT_NO_MAIN") != "1":
    main()
