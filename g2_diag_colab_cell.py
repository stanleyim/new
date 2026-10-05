# [Gate 2 후속 진단 셀 — 읽기 전용] 선행: 클론 셀 실행(main·delisted-data·pit-data) + /content/research_g2_diag.zip 업로드(또는 레포 main에 올린 뒤 clone)
# 원칙: Gate 2 판정·후보·임계·비용·선택 규칙 불변, 재판정 없음, null 재실행 없음, 2023-24 미사용. 결과는 Drive new_g2_diag에만 저장(new_g2_out은 읽기만).
import os, sys, subprocess, shutil, zipfile, time

OUT = "/content/g2_diag_out"; G2_CSV = None
try:
    from google.colab import drive
    drive.mount("/content/drive")
    OUT = "/content/drive/MyDrive/new_g2_diag"; G2_CSV = "/content/drive/MyDrive/new_g2_out/gate2_results.csv"
except Exception as e:
    print("Drive 마운트 실패 -> /content 사용:", repr(e)[:120])
os.makedirs(OUT, exist_ok=True); print("출력 폴더:", OUT)

ZIP = "/content/research_g2_diag.zip"
if not os.path.exists(ZIP) and os.path.exists("/content/main/research_g2_diag.zip"):
    shutil.copy("/content/main/research_g2_diag.zip", ZIP)
assert os.path.exists(ZIP), f"{ZIP} 없음: zip을 /content에 업로드하거나 레포 main에 올린 뒤 clone 셀을 다시 실행하세요"
shutil.rmtree("/content/rebuild", ignore_errors=True)
zipfile.ZipFile(ZIP).extractall("/content/rebuild")
sys.path.insert(0, "/content/rebuild")
import numba; print("numba", numba.__version__)
for t in ("test_synthetic", "test_gate1", "test_d5_run", "test_gate2", "test_g2_diag"):
    r = subprocess.run([sys.executable, f"/content/rebuild/research/tests/{t}.py"], capture_output=True, text=True, cwd="/content/rebuild",
                       env={**os.environ, "PYTHONPATH": "/content/rebuild"})
    print(r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "(출력 없음)", f"<- {t}")
    assert r.returncode == 0 and "ALL PASS" in r.stdout, r.stdout[-1500:] + r.stderr[-1500:]

import numpy as np
from research.run_d3d4 import run
from research.gate2_prep import prepare_g2, dollar_volume_20
from research import gate2_run as G2
from research import g2_diag as DG
t0 = time.time()
D, U, parts, F, labels = run("/content/main/data/ohlcv_full.parquet", "/content/delisted/delisted_data",
                             "/content/pit/pit_data_extra/sector_*.parquet", verbose=False)
DV = dollar_volume_20(D["close"], D["volume"], D["outlier"])
prep = prepare_g2(D, U, F, labels, DV, sorted({h for _, h in DG.DIAG_CANDS}))
cl = D["close"].values
px = {h: cl[prep["h"][h]["tpos"]] for h in prep["h"]}                       # 신호일 T 종가(무효 NaN) — 구성 진단 전용
group = D["meta"]["group"].reindex(D["close"].columns).values
assert list(D["close"].columns) == list(D["open"].columns) and len(group) == cl.shape[1] and not any(g is None for g in group)
print(f"로드·준비 완료 {time.time()-t0:.0f}s | 그룹 {dict(zip(*np.unique(group, return_counts=True)))}")

res = DG.run_diag(prep, px, group)
if G2_CSV and os.path.exists(G2_CSV):
    n = DG.check_against_gate2(res, G2_CSV); print(f"기준값 일치 확인: gate2_results.csv 대비 {n}개 후보 평균E·Q5절대순수익 일치(atol 1e-12)")
else:
    print("경고: gate2_results.csv 없음 -> 기준값 일치 확인 생략")
DG.save_report(res, OUT)
DG.print_report(res)
print("\n결과 파일:", OUT, "(g2_diag_comp/conc/year/terc/conc_meta.csv) | 판정·후보·임계 불변, 재판정 없음")
