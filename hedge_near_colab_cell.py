# [TL-007 본 실행 셀] 선행: hedge_near_clone_cell.py 실행(+ research_g2_hedge_near.zip을 레포 main에 올린 뒤 클론, 또는 /content에 업로드).
# 순서: ①합성 테스트 7종 ②설계구간(2018~22) 재현(gate2_results.csv·TL-006 기록값·헤지 경로 일치) ③연속 경로(2018~2024)=설계 경로 일치 확인 ④2023~24 평가.
# ②③ 중 하나라도 불일치면 AssertionError로 중단되고 2023~24 결과는 출력·저장되지 않는다. 2024-12-30 이후 데이터는 적재하지 않는다.
import os, sys, subprocess, shutil, zipfile, time, json
import pandas as pd

OUT = "/content/hedge_near_out"; REF = "/content/g2_ref/gate2_results.csv"
try:
    from google.colab import drive
    drive.mount("/content/drive")
    OUT = "/content/drive/MyDrive/new_hedge_near_out"; REF = "/content/drive/MyDrive/new_g2_out/gate2_results.csv"
except Exception as e:
    print("Drive 마운트 실패:", repr(e)[:120])
os.makedirs(OUT, exist_ok=True)
assert os.path.exists(REF), f"기존 Gate 2 결과 파일 없음: {REF}"
ZIP = "/content/research_g2_hedge_near.zip"
if not os.path.exists(ZIP) and os.path.exists("/content/main/research_g2_hedge_near.zip"):
    shutil.copy("/content/main/research_g2_hedge_near.zip", ZIP)
assert os.path.exists(ZIP), f"{ZIP} 없음"
shutil.rmtree("/content/rebuild", ignore_errors=True)
zipfile.ZipFile(ZIP).extractall("/content/rebuild")
sys.path.insert(0, "/content/rebuild")
import numba; print("numba", numba.__version__)
for t in ("test_synthetic", "test_gate1", "test_d5_run", "test_gate2", "test_g2_hedge", "test_near", "test_g2_hedge_near"):
    r = subprocess.run([sys.executable, f"/content/rebuild/research/tests/{t}.py"], capture_output=True, text=True,
                       cwd="/content/rebuild", env={**os.environ, "PYTHONPATH": "/content/rebuild"})
    print(r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "(출력 없음)", f"<- {t}")
    assert r.returncode == 0 and "ALL PASS" in r.stdout, r.stdout[-1500:] + r.stderr[-1500:]

from research import near_run as N, g2_hedge_near as X
paths = ("/content/main/data/ohlcv_full.parquet", "/content/delisted/delisted_data", "/content/pit/pit_data_extra/sector_*.parquet")
PIT = "/content/pit/pit_data_extra"
t0 = time.time()
prep_d, cal_d, info_d = X.build_cont(paths, N.DESIGN, "design")
print("설계구간 준비:", info_d, f"{time.time()-t0:.0f}s")
des = X.design_stage(prep_d, cal_d, PIT, REF)            # 불일치 시 AssertionError → 중단(2023~24 미산출)
print("설계구간 재현 OK: gate2_results.csv·TL-006 기록값·헤지 경로 일치")
del prep_d
t1 = time.time()
prep_c, cal_c, info_c = X.build_cont(paths, (N.DESIGN[0], N.NEAR[1]), "continuous")
print("연속구간 준비:", info_c, f"{time.time()-t1:.0f}s")
df = X.near_stage(prep_c, cal_c, cal_d, PIT, des)         # 연속=설계 경로 불일치 시 AssertionError → 중단
X.print_report(df)
out = df.copy(); out["연도별(헤지후,비헤지)"] = out["연도별(헤지후,비헤지)"].astype(str)
out.to_csv(os.path.join(OUT, "hedge_near_results.csv"), index=False, encoding="utf-8-sig")
json.dump({"통과": [f"{r.feature}-h{r.h}" for r in df.itertuples() if r.통과], "후보": len(df)},
          open(os.path.join(OUT, "hedge_near_summary.json"), "w"), ensure_ascii=False, indent=1)
print("\n결과 파일:", OUT, f"| 총 {time.time()-t0:.0f}s")
