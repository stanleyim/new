# [D5 Gate 1 본 실행 셀] 선행: 클론 셀 실행 + /content/research_d3d4d5_run.zip 업로드
# 중단(세션 끊김 등) 시 같은 셀을 다시 실행하면 저장된 청크부터 이어받음. 성과 통계는 맨 끝 보고에서만 출력.
import os, sys, subprocess, shutil, zipfile, glob, time, json

# 0) 체크포인트 저장 위치: Drive 마운트 성공 시 Drive(런타임 리셋에도 보존), 실패 시 /content
OUT = "/content/d5_out"
try:
    from google.colab import drive
    drive.mount("/content/drive")
    OUT = "/content/drive/MyDrive/new_d5_out"
except Exception as e:
    print("Drive 마운트 실패 -> /content 사용(런타임 리셋 시 체크포인트 소실):", repr(e)[:120])
os.makedirs(OUT, exist_ok=True)
print("체크포인트 폴더:", OUT)

# 1) 모듈 설치(zip 업로드본으로 /content/rebuild 덮어쓰기)
ZIP = "/content/research_d3d4d5_run.zip"
assert os.path.exists(ZIP), f"{ZIP} 없음: zip을 /content에 업로드하세요"
shutil.rmtree("/content/rebuild", ignore_errors=True)
zipfile.ZipFile(ZIP).extractall("/content/rebuild")
sys.path.insert(0, "/content/rebuild")
import numba; print("numba", numba.__version__)
for t in ("test_synthetic", "test_gate1", "test_d5_run"):
    r = subprocess.run([sys.executable, f"/content/rebuild/research/tests/{t}.py"], capture_output=True, text=True, cwd="/content/rebuild",
                       env={**os.environ, "PYTHONPATH": "/content/rebuild"})
    print(r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "(출력 없음)", f"<- {t}")
    assert r.returncode == 0 and "ALL PASS" in r.stdout, r.stdout[-1500:] + r.stderr[-1500:]

# 2) 실데이터 로드 + 입력 준비(진단 출력 없음)
from research.run_d3d4 import run
from research.gate1_prep import prepare
from research import d5_run as R
t0 = time.time()
D, U, parts, F, labels = run("/content/main/data/ohlcv_full.parquet", "/content/delisted/delisted_data",
                             "/content/pit/pit_data_extra/sector_*.parquet", verbose=False)
prep = prepare(D, U, F, labels)
print(f"로드·준비 완료 {time.time()-t0:.0f}s | h={sorted(prep)} | 신호일 {[len(prep[h]['dates']) for h in sorted(prep)]}")

# 3) 본 실행(K=1,000, 18조합×T1/T2 null = 36 unit, 청크 100, 재개 가능)
res, meta = R.run_gate1(prep, OUT)

# 4) 보고(사전등록 순서 판정: IC BH q=0.10 -> 블록 부호 일치율 -> 분위 스프레드·단조성)
R.print_report(res, meta)
print("\n결과 파일:", OUT, "(gate1_results.csv, gate1_summary.json, null_*.npy)")
