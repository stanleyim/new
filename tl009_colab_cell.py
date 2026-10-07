# [TL-009 본 실행 셀] 선행: hedge_near_clone_cell.py 실행(레포 main에 research_tl009.zip을 올린 뒤 클론하거나, zip을 /content에 업로드).
# 순서: ①합성 테스트 6종 ②필터 전 REV·RISK·ATTN IC가 D5 gate1_results.csv와 일치(불일치 시 중단) ③상위 1/3 필터 진단·판정 불가 메타 ④Gate 1(D5와 동일 규칙, permutation K=1000, 27 trial).
# 설계구간(2018~22)만 사용. 2023~24·E1은 적재하지 않는다. 체크포인트·재개 가능: 세션이 끊기면 같은 셀 재실행(Drive new_tl009_out). 예상 소요 약 2~3시간(미실측).
import os, sys, subprocess, shutil, zipfile, time
import pandas as pd

OUT = "/content/tl009_out"; REF = "/content/g1_ref/gate1_results.csv"
try:
    from google.colab import drive
    drive.mount("/content/drive")
    OUT = "/content/drive/MyDrive/new_tl009_out"; REF = "/content/drive/MyDrive/new_d5_out/gate1_results.csv"
except Exception as e:
    print("Drive 마운트 실패:", repr(e)[:120])
os.makedirs(OUT, exist_ok=True)
assert os.path.exists(REF), f"D5 Gate 1 결과 파일 없음: {REF}"
ZIP = "/content/research_tl009.zip"
if not os.path.exists(ZIP) and os.path.exists("/content/main/research_tl009.zip"):
    shutil.copy("/content/main/research_tl009.zip", ZIP)
assert os.path.exists(ZIP), f"{ZIP} 없음"
shutil.rmtree("/content/rebuild", ignore_errors=True)
zipfile.ZipFile(ZIP).extractall("/content/rebuild")
sys.path.insert(0, "/content/rebuild")
import numba; print("numba", numba.__version__)
for t in ("test_synthetic", "test_gate1", "test_d5_run", "test_gate2", "test_tl008", "test_tl009"):
    r = subprocess.run([sys.executable, f"/content/rebuild/research/tests/{t}.py"], capture_output=True, text=True,
                       cwd="/content/rebuild", env={**os.environ, "PYTHONPATH": "/content/rebuild"})
    print(r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "(출력 없음)", f"<- {t}")
    assert r.returncode == 0 and "ALL PASS" in r.stdout, r.stdout[-1500:] + r.stderr[-1500:]

from research import near_run as N, tl009 as X
from research.run_d3d4 import run
from research.gate1_prep import prepare
from research.gate2_prep import dollar_volume_20
paths = ("/content/main/data/ohlcv_full.parquet", "/content/delisted/delisted_data", "/content/pit/pit_data_extra/sector_*.parquet")
t0 = time.time()
with N.period(*N.DESIGN):
    D, U, parts, F, labels = run(*paths, verbose=False)
    assert D["calendar"].max() <= N.DESIGN[1], "설계구간 밖 데이터 적재"
    prep = prepare(D, U, F, labels)
    DV = dollar_volume_20(D["close"], D["volume"], D["outlier"])
    assert U.index.equals(DV.index) and list(U.columns) == list(DV.columns), "유니버스·거래대금 정렬 불일치"
    DVu = DV.where(U)                      # 유니버스 밖 종목은 3분위 기준에서 제외
    assert list(DVu.columns) == list(labels[5]["t1"].columns), "열 정렬 불일치"
print("준비:", f"{D['calendar'].min().date()}~{D['calendar'].max().date()}", f"{time.time()-t0:.0f}s")
res, meta, fdf, meta_df = X.run_tl009(prep, DVu, OUT, REF)
X.print_report(res, meta, fdf, meta_df, REF)
fdf.to_csv(os.path.join(OUT, "tl009_filter_diag.csv"), index=False, encoding="utf-8-sig")
meta_df.to_csv(os.path.join(OUT, "tl009_eligibility_meta.csv"), index=False, encoding="utf-8-sig")
print("\n결과 파일:", OUT, f"| 총 {(time.time()-t0)/60:.0f}분")
