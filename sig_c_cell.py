# [C단계 셀 — 최종 고정 조합 vs 현재 운용(기준선), E1 구간 2025-01-02~2026-06-26 1회 확인] 읽기 전용
# 선행: 클론 셀(sig_hedge_clone_cell.py: main + pit-data idx) 실행 완료 -> /content/main/{data, signal_runner.py, sig_grid_cell.py}, /content/pit/pit_data_extra/idx_*.parquet
# 부품 함수는 레포의 sig_grid_cell.py를 그대로 import(복사·수정 없음): build_feats·hot_series·select_by_day(_x)·extra_mask·slot_logic·simulate_equity(_close)·check_against_sr_x·window_metrics·perf·calmar.
#
# ===== 사전등록 (실행 전 고정, 결과 후 변경 금지) =====
# 비교 대상은 2개뿐: 기준선(현재 운용: N3·K_MAX 5·종목당 20%·T+1 시가 매수·T+10 종가 청산) vs 최종 조합 FINAL = T+1 종가 진입(상한가 근접 1.29 미체결) · 신호일 등락률 <= 10% · KOSDAQ 60일선 +4.5% 초과일 신호 무시 · 외국인 필터 없음.
# 경로: 설계 시작(2018-01-02)부터 이어진 연속 경로(근접 B단계와 같은 방식). 평가구간 = 2025-01-02 ~ 2026-06-26. 2026-06-26 이후 데이터는 로드 단계에서 폐기.
# 통과 = ① Calmar(CAGR/|MDD|) FINAL > 기준선  ∧  ② 2025·2026(~6/26) 각 연도 MDD 절대값 FINAL <= 기준선. 둘 다 충족해야 통과. 보조 수치(CAGR·Sharpe·거래 등)는 판정에 미사용.
# E1 열람 1회: 모든 선행조건 통과 후에만 성과를 계산하고, 결과를 c_stage_result.json에 먼저 저장한 뒤 출력. 이후 재실행은 재계산 없이 저장된 결과만 출력. 실패해도 재튜닝·재실행 금지.
# 통과 시에도 signal_runner.py 변경은 diff 제시 -> 3단계 검증 -> Stanley 승인 후에만. 탈락 시 반영 없음.
# 선행조건(전부 통과해야 성과 출력): ①final_config.json(있으면) = 사전등록 조합 ②Feature 미래 비의존(설계·E1 각각) ③설계구간 기준선 재현(CAGR 0.2921, MDD -0.5455) ④E1 연속 경로가 설계 경로와 [0,L]에서 일치
#   ⑤실제 signal_runner.select_signals·update_holdings 일자별 실행 = 내 슬롯 로직 편입 목록 전 건 일치(기준선·최종 조합 각각, 2018~E1 끝 전체 연속 경로) ⑥회계 항등식 ⑦KOSDAQ 지수가 모든 거래일에 존재(과열 필터가 조용히 꺼지는 것 방지) ⑧데이터 마지막 거래일 = 2026-06-26
# 한계(사실): v7 SIG 규칙 자체는 2014~2026 백테스트로 정해져 E1 구간에 깨끗한 홀드아웃이 아님(원본 스크립트 소실로 확인 불가). 이번 변경분(종가 진입·등락률 10% 상한·과열 회피)은 2025~26 미노출. E1 적격 검사(G1~G6)는 별도로 적용하지 않고 설계·근접과 같은 main 데이터를 사용.
import os, sys, json, time, hashlib, datetime, importlib.util
import numpy as np
import pandas as pd

C_START, C_END = "2025-01-02", "2026-06-26"
FINAL = dict(entry="CLOSE", cap=10.0, heat=True, fz=False)
FINAL_NAME = "CLOSE|cap=10.0|heat=Y|fz=-"
EXPECTED_BASE = {"CAGR": 0.2921, "MDD": -0.5455}
MAIN_DIR = os.environ.get("SIGC_MAIN", "/content/main")
GRID_CFG = os.environ.get("SIGC_GRID_CFG", "/content/drive/MyDrive/new_siggrid_out/final_config.json")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()[:16]


def load_grid(main_dir):
    os.environ["SIGGRID_NO_MAIN"] = "1"      # import 시 sig_grid_cell.main() 자동 실행 방지
    spec = importlib.util.spec_from_file_location("sig_grid_cell", f"{main_dir}/sig_grid_cell.py")
    G = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(G)
    return G


def build_two(G, ohlcv, flow, short, end, sr_path, P, label):
    """기준선(BASE)과 최종 조합(FINAL) 두 경로만 만든다. end 이후 데이터는 build_feats가 폐기."""
    ohlcv, flow, short, feats0, nlk = G.build_feats(ohlcv, flow, short, end)
    hot = G.hot_series(G.PIT_EXTRA, end)
    feats0["hot"] = feats0["date"].map(hot).astype("boolean").fillna(False).astype(bool)
    P(f"[{label}점검 Feature 미래 비의존] OK · {nlk}건 · 행 {len(feats0):,} · 종목 {feats0['ticker'].nunique()}")
    feats = feats0[feats0["date"] >= pd.Timestamp(G.CAL_START)].reset_index(drop=True)
    po = feats.pivot(index="date", columns="ticker", values="시가").sort_index()
    pc = feats.pivot(index="date", columns="ticker", values="종가").sort_index()
    cal, tick = po.index, po.columns
    N = len(cal)
    O, Clf = po.values, pc.ffill().values
    col_of = {t: j for j, t in enumerate(tick)}
    # KOSDAQ 지수 커버리지: 누락 거래일은 과열 필터가 조용히 꺼지므로 즉시 중단
    miss = cal.difference(hot.index)
    assert len(miss) == 0, f"[{label}] KOSDAQ 지수 종가 결측 거래일 {len(miss)}개 (예: {[str(x.date()) for x in miss[:3]]})"
    assert int((hot.index < cal[0]).sum()) >= 60, f"[{label}] 60일선 계산용 지수 이력 부족"
    P(f"[{label}점검 KOSDAQ 지수] 모든 거래일({N}일) 존재 OK · 지수 마지막 {hot.index.max().date()}")
    s0 = int(np.searchsorted(cal.values, np.datetime64(pd.Timestamp(G.DESIGN_START))))
    run_idx = [i for i in range(s0, N) if i + G.HMAX < N]
    run_dates = [cal[i] for i in run_idx]
    sel_b = G.select_by_day(feats, G.DESIGN_START, end)
    emask = G.extra_mask(feats, FINAL["cap"], FINAL["heat"], FINAL["fz"], feats["hot"])
    sel_f = G.select_by_day_x(feats, G.DESIGN_START, end, emask)
    e_b = G.slot_logic(cal, sel_b, run_idx)[0]
    e_f = G.slot_logic(cal, sel_f, run_idx)[0]
    if sr_path:
        sr = G.load_sr(sr_path)
        G.check_against_sr_x(sr, ohlcv, flow, short, feats0, run_dates, [(cal[i], t) for i, t in e_b],
                             lambda d: pd.Series(True, index=d.index), P, f"{label}점검 기준선")
        hot_map = lambda d: d["date"].map(hot).astype("boolean").fillna(False).astype(bool)
        G.check_against_sr_x(sr, ohlcv, flow, short, feats0, run_dates, [(cal[i], t) for i, t in e_f],
                             lambda d: G.extra_mask(d, FINAL["cap"], FINAL["heat"], FINAL["fz"], hot_map(d)), P,
                             f"{label}점검 최종 조합")
    sims = {"BASE": G.simulate_equity(O, Clf, col_of, e_b, 0.20, N),
            FINAL_NAME: G.simulate_equity_close(Clf, col_of, e_f, 0.20, N)}
    for nm, s in sims.items():
        gap = s["cash"] - (1.0 + sum(t[4] - t[3] for t in s["trades"]))
        assert abs(gap) < 1e-9, f"회계 항등식 불일치 {nm}: {gap}"
    P(f"[{label}점검 회계 항등식] OK (2경로)")
    wm = (feats["date"] >= pd.Timestamp(C_START)) & (feats["date"] <= pd.Timestamp(end))
    base_m = feats["N3"] & feats["ELIG"] & wm
    hw = hot[(hot.index >= pd.Timestamp(C_START)) & (hot.index <= pd.Timestamp(end))]
    diag = {"신호후보(N3&ELIG) 행": int(base_m.sum()), "조합 필터 통과 행": int((base_m & emask).sum()),
            "KOSDAQ 과열일수(창 내)": int(hw.sum()), "창 내 지수일수": int(len(hw))}
    return dict(cal=cal, N=N, last_run=run_idx[-1], sims=sims, diag=diag)


def trade_stats(s, e0, cal):
    tr = pd.DataFrame(s["trades"], columns=["E", "ticker", "net_ret", "alloc", "proceeds"])
    tr = tr[tr["E"] >= e0]
    yrs = pd.DatetimeIndex(cal[tr["E"].values]).year if len(tr) else []
    return {"거래": int(len(tr)), "거래평균": float(tr["net_ret"].mean()) if len(tr) else float("nan"),
            "꼬리(<=-20%)": int((tr["net_ret"] <= -0.2).sum()),
            "연도별거래": {str(y): int((np.asarray(yrs) == y).sum()) for y in (2025, 2026)}}


def run_c(G, ohlcv, flow, short, out_dir, sr_path, verbose=True):
    t0 = time.time()
    P = print if verbose else (lambda *a, **k: None)
    os.makedirs(out_dir, exist_ok=True)
    res_path = f"{out_dir}/c_stage_result.json"
    if os.path.exists(res_path):
        stored = json.load(open(res_path))
        P("E1 열람 1회 원칙: 이미 실행된 결과 파일이 있어 재계산하지 않고 저장된 결과만 출력합니다.")
        P(stored["report"])
        return stored
    if os.path.exists(GRID_CFG):
        cfg = json.load(open(GRID_CFG))
        assert cfg["final"] == FINAL and cfg["name"] == FINAL_NAME, f"final_config.json 불일치: {cfg}"
        P("[선행] final_config.json = 사전등록 최종 조합 일치 OK")
    else:
        P(f"[선행] final_config.json 없음({GRID_CFG}) - 사전등록 값 사용")
    P("===== 선행: 설계구간 재현 (성과 판정 없음) =====")
    d = build_two(G, ohlcv, flow, short, G.DESIGN_END, None, P, "설계 ")
    pb = G.perf(d["sims"]["BASE"]["ret"], d["sims"]["BASE"]["first"], d["N"] - 1)
    assert abs(pb["CAGR"] - EXPECTED_BASE["CAGR"]) < 6e-5 and abs(pb["MDD"] - EXPECTED_BASE["MDD"]) < 6e-5, f"기준선 재현 불일치 {pb}"
    P(f"[선행조건] 설계구간 기준선 재현 OK (CAGR {pb['CAGR']:.4f}, MDD {pb['MDD']:.4f})")
    P("===== E1 연속 경로 구성 (선행조건 통과 전에는 성과 미계산) =====")
    c = build_two(G, ohlcv, flow, short, C_END, sr_path, P, "E1 ")
    L = d["last_run"] + 1
    assert (c["cal"][:d["N"]] == d["cal"]).all(), "E1 캘린더 앞부분 != 설계 캘린더"
    for k in ("BASE", FINAL_NAME):
        assert np.allclose(c["sims"][k]["eq"][:L + 1], d["sims"][k]["eq"][:L + 1], rtol=1e-9, atol=1e-12), f"연속 경로 != 설계 경로 {k}"
    P(f"[선행조건] E1 연속 경로가 설계 경로와 [0,{L}]에서 일치 OK")
    cal, N = c["cal"], c["N"]
    assert cal[-1] == pd.Timestamp(C_END), f"데이터 마지막 거래일 {cal[-1].date()} != {C_END}"
    e0 = int(np.searchsorted(cal.values, np.datetime64(pd.Timestamp(C_START))))
    assert cal[e0] >= pd.Timestamp(C_START) and (cal[e0 - 1] < pd.Timestamp(C_START))
    assert all(c["sims"][k]["first"] < e0 for k in c["sims"]), "평가 시작 전에 첫 편입이 없음"
    P(f"[선행조건] 평가구간 {cal[e0].date()} ~ {cal[-1].date()} ({N - e0}거래일) OK")
    # ---- 여기부터 성과 계산(E1 열람 1회) ----
    seg = {"2025": lambda cl: pd.DatetimeIndex(cl).year == 2025, "2026(~6/26)": lambda cl: pd.DatetimeIndex(cl).year == 2026}
    rows = {}
    for nm in ("BASE", FINAL_NAME):
        p, sg = G.window_metrics(c["sims"][nm]["ret"], e0, N - 1, cal, seg)
        ts = trade_stats(c["sims"][nm], e0, cal)
        rows[nm] = dict(CAGR=p["CAGR"], Sharpe=p["Sharpe"], MDD=p["MDD"], Calmar=G.calmar(p), 일수=p["n_days"],
                        seg_MDD={k: v["MDD"] for k, v in sg.items()}, seg_CAGR={k: v["CAGR"] for k, v in sg.items()},
                        평균보유종목=float(c["sims"][nm]["npos"][e0:].mean()), 경로미체결=int(c["sims"][nm]["no_fill"]), **ts)
    b, f = rows["BASE"], rows[FINAL_NAME]
    ok1 = f["Calmar"] > b["Calmar"]
    ok2 = all(abs(f["seg_MDD"][k]) <= abs(b["seg_MDD"][k]) for k in b["seg_MDD"])
    passed = bool(ok1 and ok2)
    L_ = []
    L_.append(f"===== C단계 결과 (E1 {cal[e0].date()} ~ {cal[-1].date()}, 1회) =====")
    hdr = f"{'':<10}{'CAGR':>9}{'Sharpe':>8}{'MDD':>9}{'Calmar':>8}{'거래':>6}{'거래평균':>9}{'꼬리':>5}{'평균보유':>8}"
    L_.append(hdr)
    for nm, r in (("기준선", b), ("최종조합", f)):
        L_.append(f"{nm:<10}{r['CAGR']:>9.4f}{r['Sharpe']:>8.3f}{r['MDD']:>9.4f}{r['Calmar']:>8.3f}{r['거래']:>6d}{r['거래평균']:>9.4f}{r['꼬리(<=-20%)']:>5d}{r['평균보유종목']:>8.2f}")
    for k in b["seg_MDD"]:
        y = k[:4]
        L_.append(f"  {k}: MDD 기준선 {b['seg_MDD'][k]:.4f} / 최종 {f['seg_MDD'][k]:.4f} · CAGR 기준선 {b['seg_CAGR'][k]:.4f} / 최종 {f['seg_CAGR'][k]:.4f} · 편입 거래 기준선 {b['연도별거래'][y]} / 최종 {f['연도별거래'][y]}")
    L_.append(f"신호 필터 진단(창 내): " + " · ".join(f"{k} {v}" for k, v in c["diag"].items()))
    L_.append(f"판정: ① Calmar {f['Calmar']:.3f} vs {b['Calmar']:.3f} -> {'충족' if ok1 else '미충족'} · ② 연도별 MDD 각각 <= 기준선 -> {'충족' if ok2 else '미충족'} => {'통과' if passed else '탈락'}")
    L_.append("통과 시에도 signal_runner.py 변경은 diff 제시 -> 검증 -> Stanley 승인 후. 탈락 시 반영 없음. 재튜닝·재실행 금지.")
    report = "\n".join(L_)
    out = {"report": report, "passed": passed, "ok1": bool(ok1), "ok2": bool(ok2), "rows": rows, "diag": c["diag"],
           "window": [str(cal[e0].date()), str(cal[-1].date())], "final": FINAL, "name": FINAL_NAME,
           "sha_sig_grid_cell": sha256(f"{MAIN_DIR}/sig_grid_cell.py"), "sha_signal_runner": sha256(sr_path) if sr_path else None,
           "executed_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}
    json.dump(out, open(res_path, "w"), ensure_ascii=False, indent=2)       # 출력 전에 저장
    pd.DataFrame([{"조합": k, **{kk: vv for kk, vv in v.items() if not isinstance(vv, dict)}} for k, v in rows.items()]).to_csv(f"{out_dir}/c_stage_table.csv", index=False)
    P(report)
    P(f"\n총 {time.time()-t0:.0f}s · 저장: {out_dir}")
    return out


def main():
    G = load_grid(MAIN_DIR)
    if os.environ.get("SIGC_PIT"):
        G.PIT_EXTRA = os.environ["SIGC_PIT"]
    d = f"{MAIN_DIR}/data"
    ohlcv = pd.read_parquet(f"{d}/ohlcv_full.parquet")
    flow = pd.read_parquet(f"{d}/flow_full.parquet")
    short = pd.read_parquet(f"{d}/short_full.parquet")
    out_dir = os.environ.get("SIGC_OUT")
    if not out_dir:
        try:
            from google.colab import drive
            drive.mount("/content/drive")
            out_dir = "/content/drive/MyDrive/new_sigc_out"
        except Exception as e:
            out_dir = "/content/new_sigc_out"
            print(f"Drive 마운트 실패({e}) -> 로컬 {out_dir}")
    run_c(G, ohlcv, flow, short, out_dir, f"{MAIN_DIR}/signal_runner.py")


if os.environ.get("SIGC_NO_MAIN") != "1":
    main()
