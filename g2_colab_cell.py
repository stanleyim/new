# [Gate 2 본 실행 셀] 선행: 클론 셀 실행 + /content/research_g2.zip 업로드 (Gate 1 폴더 new_d5_out은 건드리지 않음)
# 사전등록(TL-004): 후보 11개 (Feature,h), 롱온리 Q5 트랜치, 왕복 0.206%, 풀 대비 초과 + Q5 절대 순수익>0 필수, null 1,000회. 중단 시 같은 셀 재실행하면 이어받음.
import os, sys, subprocess, shutil, zipfile, time

OUT = "/content/g2_out"; GATE1_CSV = None
try:
    from google.colab import drive
    drive.mount("/content/drive")
    OUT = "/content/drive/MyDrive/new_g2_out"; GATE1_CSV = "/content/drive/MyDrive/new_d5_out/gate1_results.csv"
except Exception as e:
    print("Drive 마운트 실패 -> /content 사용(런타임 리셋 시 체크포인트 소실):", repr(e)[:120])
os.makedirs(OUT, exist_ok=True); print("체크포인트 폴더:", OUT)

ZIP = "/content/research_g2.zip"
if not os.path.exists(ZIP) and os.path.exists("/content/main/research_g2.zip"):       # 레포 main에 올렸다면 clone 결과에서 사용
    shutil.copy("/content/main/research_g2.zip", ZIP)
assert os.path.exists(ZIP), f"{ZIP} 없음: zip을 /content에 업로드하거나 레포 main에 올린 뒤 clone 셀을 다시 실행하세요"
shutil.rmtree("/content/rebuild", ignore_errors=True)
zipfile.ZipFile(ZIP).extractall("/content/rebuild")
sys.path.insert(0, "/content/rebuild")
import numba; print("numba", numba.__version__)
for t in ("test_synthetic", "test_gate1", "test_d5_run", "test_gate2"):
    r = subprocess.run([sys.executable, f"/content/rebuild/research/tests/{t}.py"], capture_output=True, text=True, cwd="/content/rebuild",
                       env={**os.environ, "PYTHONPATH": "/content/rebuild"})
    print(r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "(출력 없음)", f"<- {t}")
    assert r.returncode == 0 and "ALL PASS" in r.stdout, r.stdout[-1500:] + r.stderr[-1500:]

from research.run_d3d4 import run
from research.gate2_prep import prepare_g2, dollar_volume_20
from research import gate2_run as G2
t0 = time.time()
D, U, parts, F, labels = run("/content/main/data/ohlcv_full.parquet", "/content/delisted/delisted_data",
                             "/content/pit/pit_data_extra/sector_*.parquet", verbose=False)
DV = dollar_volume_20(D["close"], D["volume"], D["outlier"])
prep = prepare_g2(D, U, F, labels, DV, sorted({h for _, h in G2.CANDS}))
print(f"로드·준비 완료 {time.time()-t0:.0f}s | h={sorted(prep['h'])} | 신호일 {[len(prep['h'][h]['dates']) for h in sorted(prep['h'])]}")

df, meta = G2.run_gate2(prep, OUT, GATE1_CSV)
G2.print_report(df, meta)
print("\n결과 파일:", OUT, "(gate2_results.csv, gate2_summary.json, null_g2_*.npy)")
