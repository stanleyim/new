# [TL-010 본 실행 셀] 선행: hedge_near_clone_cell.py 실행(레포 main에 research_tl010.zip을 올린 뒤 클론하거나, zip을 /content에 업로드).
# 사전등록(TL-010): TL-009 상위 1/3 유니버스, Gate 2 후보 6개(REV h5·h10, RISK h5·h10·h20, ATTN h5), 롱온리 Q5 트랜치, 왕복 0.206%, 풀(=상위 1/3) 대비 초과 + Q5 절대순수익>0 필수, null 1,000회.
# 순서: ①합성 테스트 7종 ②설계구간 신호일 확인 ③필터 전 경로가 Drive new_g2_out/gate2_results.csv와 일치(불일치 시 중단) ④Gate 2 ⑤사후 진단(판정 불변).
# 설계구간(2018~22)만 사용, 2023~24·E1 미적재. 체크포인트·재개 가능: 세션이 끊기면 같은 셀 재실행(Drive new_tl010_out). 예상 소요는 미실측.
import os, sys, subprocess, shutil, zipfile, time

OUT = "/content/tl010_out"; REF = "/content/g2_ref/gate2_results.csv"
try:
    from google.colab import drive
    drive.mount("/content/drive")
    OUT = "/content/drive/MyDrive/new_tl010_out"; REF = "/content/drive/MyDrive/new_g2_out/gate2_results.csv"
except Exception as e:
    print("Drive 마운트 실패:", repr(e)[:120])
os.makedirs(OUT, exist_ok=True)
assert os.path.exists(REF), f"Gate 2 결과 파일 없음: {REF}"
ZIP = "/content/research_tl010.zip"
if not os.path.exists(ZIP) and os.path.exists("/content/main/research_tl010.zip"):
    shutil.copy("/content/main/research_tl010.zip", ZIP)
assert os.path.exists(ZIP), f"{ZIP} 없음"
shutil.rmtree("/content/rebuild", ignore_errors=True)
zipfile.ZipFile(ZIP).extractall("/content/rebuild")
sys.path.insert(0, "/content/rebuild")
import numba; print("numba", numba.__version__)
for t in ("test_synthetic", "test_gate1", "test_d5_run", "test_gate2", "test_g2_diag", "test_tl009", "test_tl010"):
    r = subprocess.run([sys.executable, f"/content/rebuild/research/tests/{t}.py"], capture_output=True, text=True,
                       cwd="/content/rebuild", env={**os.environ, "PYTHONPATH": "/content/rebuild"})
    print(r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "(출력 없음)", f"<- {t}")
    assert r.returncode == 0 and "ALL PASS" in r.stdout, r.stdout[-1500:] + r.stderr[-1500:]

import numpy as np
from research import config as C, tl010 as Z
from research.run_d3d4 import run
from research.gate2_prep import prepare_g2, dollar_volume_20
t0 = time.time()
D, U, parts, F, labels = run("/content/main/data/ohlcv_full.parquet", "/content/delisted/delisted_data",
                             "/content/pit/pit_data_extra/sector_*.parquet", verbose=False)
assert D["calendar"].max() <= C.SIGNAL_END, "설계구간 밖 데이터 적재"
DV = dollar_volume_20(D["close"], D["volume"], D["outlier"])
assert U.index.equals(DV.index) and list(U.columns) == list(DV.columns), "유니버스·거래대금 정렬 불일치"
DVu = DV.where(U)                                  # 유니버스 밖 종목은 3분위 기준에서 제외(TL-009와 동일)
prep = prepare_g2(D, U, F, labels, DV, [5, 10, 20])
assert list(DVu.columns) == list(labels[5]["t1"].columns), "열 정렬 불일치"
cl = D["close"].values
px = {h: cl[prep["h"][h]["tpos"]] for h in prep["h"]}                       # 신호일 T 종가(무효 NaN) — 구성 진단 전용
group = D["meta"]["group"].reindex(D["close"].columns).values
assert list(D["close"].columns) == list(D["open"].columns) and len(group) == cl.shape[1] and not any(g is None for g in group)
print(f"준비 완료 {time.time()-t0:.0f}s | {D['calendar'].min().date()}~{D['calendar'].max().date()} | 신호일 {[len(prep['h'][h]['dates']) for h in (5, 10, 20)]}")

df, meta, fdf, dres, cmp_ = Z.run_tl010(prep, DVu, OUT, REF, px, group)
Z.print_report(df, meta, fdf, cmp_, dres)
print("\n결과 파일:", OUT, "(gate2_results.csv, gate2_summary.json, tl010_vs_unfiltered.csv, tl010_filter_diag.csv, diag/*.csv, null_g2_*.npy)", f"| 총 {(time.time()-t0)/60:.0f}분")
