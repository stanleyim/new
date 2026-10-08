# [TL-011 본 실행 셀] 선행: hedge_near_clone_cell.py 실행(레포 main에 research_tl011.zip을 올린 뒤 클론하거나, zip을 /content에 업로드).
# 사전등록(TL-011): RISK(h5·h10·h20)에서 그룹 439를 제외하고 A+B 종목만으로 D5 Gate 1(전 규칙 동일)과 Gate 2(풀=A+B ∧ RISK 유효, 롱온리 Q5, 왕복 0.206%)를 재검정.
# 종료 규칙: h별 생존 = Gate 1 통과 ∧ Gate 2 ① 통과 ∧ 평균E>0. 생존 h 없음 → RISK 계열 종료. 생존해도 자동 진행 없음.
# 순서: ①합성 테스트 8종 ②설계구간 확인 ③선행조건 2종(제외 전 경로가 Drive new_d5_out/gate1_results.csv·new_g2_out/gate2_results.csv와 일치, 불일치 시 중단) ④Gate 1 ⑤Gate 2 ⑥사후 진단 ⑦종료 규칙.
# 설계구간(2018~22)만 사용, 2023~24·E1 미적재. 체크포인트·재개 가능: 세션이 끊기면 같은 셀 재실행(Drive new_tl011_out). 예상 소요 약 1시간(TL-008 Gate 1 단일 Feature 54분 선례, 미실측).
import os, sys, subprocess, shutil, zipfile, time

OUT = "/content/tl011_out"; REF1 = "/content/g1_ref/gate1_results.csv"; REF2 = "/content/g2_ref/gate2_results.csv"
try:
    from google.colab import drive
    drive.mount("/content/drive")
    OUT = "/content/drive/MyDrive/new_tl011_out"
    REF1 = "/content/drive/MyDrive/new_d5_out/gate1_results.csv"; REF2 = "/content/drive/MyDrive/new_g2_out/gate2_results.csv"
except Exception as e:
    print("Drive 마운트 실패:", repr(e)[:120])
os.makedirs(OUT, exist_ok=True)
assert os.path.exists(REF1), f"D5 Gate 1 결과 파일 없음: {REF1}"
assert os.path.exists(REF2), f"Gate 2 결과 파일 없음: {REF2}"
ZIP = "/content/research_tl011.zip"
if not os.path.exists(ZIP) and os.path.exists("/content/main/research_tl011.zip"):
    shutil.copy("/content/main/research_tl011.zip", ZIP)
assert os.path.exists(ZIP), f"{ZIP} 없음"
shutil.rmtree("/content/rebuild", ignore_errors=True)
zipfile.ZipFile(ZIP).extractall("/content/rebuild")
sys.path.insert(0, "/content/rebuild")
import numba; print("numba", numba.__version__)
for t in ("test_synthetic", "test_gate1", "test_d5_run", "test_gate2", "test_g2_diag", "test_tl009", "test_tl010", "test_tl011"):
    r = subprocess.run([sys.executable, f"/content/rebuild/research/tests/{t}.py"], capture_output=True, text=True,
                       cwd="/content/rebuild", env={**os.environ, "PYTHONPATH": "/content/rebuild"})
    print(r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "(출력 없음)", f"<- {t}")
    assert r.returncode == 0 and "ALL PASS" in r.stdout, r.stdout[-1500:] + r.stderr[-1500:]

import numpy as np
from research import config as C, tl011 as Z
from research.run_d3d4 import run
from research.gate1_prep import prepare
from research.gate2_prep import prepare_g2, dollar_volume_20
t0 = time.time()
D, U, parts, F, labels = run("/content/main/data/ohlcv_full.parquet", "/content/delisted/delisted_data",
                             "/content/pit/pit_data_extra/sector_*.parquet", verbose=False)
assert D["calendar"].max() <= C.SIGNAL_END, "설계구간 밖 데이터 적재"
DV = dollar_volume_20(D["close"], D["volume"], D["outlier"])
assert U.index.equals(DV.index) and list(U.columns) == list(DV.columns), "유니버스·거래대금 정렬 불일치"
prep1 = prepare(D, U, F, labels)
prep2 = prepare_g2(D, U, F, labels, DV, [5, 10, 20])
for h in (5, 10, 20):
    assert (prep1[h]["dates"] == prep2["h"][h]["dates"]).all() and prep1[h]["F"]["RISK"].shape == prep2["h"][h]["F"]["RISK"].shape, f"Gate 1·2 입력 불일치 h={h}"
    assert np.array_equal(prep1[h]["F"]["RISK"], prep2["h"][h]["F"]["RISK"], equal_nan=True), f"Gate 1·2 RISK 값 불일치 h={h}"
cl = D["close"].values
px = {h: cl[prep2["h"][h]["tpos"]] for h in prep2["h"]}
group = D["meta"]["group"].reindex(D["close"].columns).values
assert list(D["close"].columns) == list(D["open"].columns) and len(group) == cl.shape[1] and not any(g is None for g in group)
import pandas as pd
print("그룹별 종목 수:", pd.Series(group).value_counts().to_dict())
print(f"준비 완료 {time.time()-t0:.0f}s | {D['calendar'].min().date()}~{D['calendar'].max().date()} | 신호일 {[len(prep2['h'][h]['dates']) for h in (5, 10, 20)]}")

o = Z.run_tl011(prep1, prep2, OUT, REF1, REF2, px, group)
Z.print_report(o)
print("\n결과 파일:", OUT, "(g1/gate1_results.csv, g2/gate2_results.csv, g2/diag/*.csv, tl011_decision.csv, tl011_vs_full.csv, tl011_pool_diag.csv)", f"| 총 {(time.time()-t0)/60:.0f}분")
