# [근접 검증 본 실행 셀 — TL-005] 선행: 클론 셀 실행 + research_near.zip을 /content에 업로드(또는 레포 main에 올린 뒤 클론 셀 재실행).
# 순서: ①합성 테스트 5종 ②설계구간(2018~22) 재현(기존 gate2_results.csv와 일치 못 하면 여기서 중단, 2023~24 결과는 출력 안 함) ③2023~24 평가.
# 2025-01 이후 데이터는 적재하지 않는다. 결과 열람 후 기준 변경 없음.
import os, sys, subprocess, shutil, zipfile, time, json

OUT = "/content/near_out"; REF = "/content/g2_ref/gate2_results.csv"
try:
    from google.colab import drive
    drive.mount("/content/drive")
    OUT = "/content/drive/MyDrive/new_near_out"; REF = "/content/drive/MyDrive/new_g2_out/gate2_results.csv"
except Exception as e:
    print("Drive 마운트 실패:", repr(e)[:120])
os.makedirs(OUT, exist_ok=True)
assert os.path.exists(REF), f"기존 Gate 2 결과 파일 없음: {REF} (Drive new_g2_out/gate2_results.csv 필요)"

ZIP = "/content/research_near.zip"
if not os.path.exists(ZIP) and os.path.exists("/content/main/research_near.zip"):
    shutil.copy("/content/main/research_near.zip", ZIP)
assert os.path.exists(ZIP), f"{ZIP} 없음: zip을 /content에 업로드하세요"
shutil.rmtree("/content/rebuild", ignore_errors=True)
zipfile.ZipFile(ZIP).extractall("/content/rebuild")
sys.path.insert(0, "/content/rebuild")
import numba; print("numba", numba.__version__)
for t in ("test_synthetic", "test_gate1", "test_d5_run", "test_gate2", "test_near"):
    r = subprocess.run([sys.executable, f"/content/rebuild/research/tests/{t}.py"], capture_output=True, text=True,
                       cwd="/content/rebuild", env={**os.environ, "PYTHONPATH": "/content/rebuild"})
    print(r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "(출력 없음)", f"<- {t}")
    assert r.returncode == 0 and "ALL PASS" in r.stdout, r.stdout[-1500:] + r.stderr[-1500:]

from research import near_run as N
paths = ("/content/main/data/ohlcv_full.parquet", "/content/delisted/delisted_data", "/content/pit/pit_data_extra/sector_*.parquet")
t0 = time.time()
prep_d, info_d = N.build_prep(paths, N.DESIGN, "design")
print("설계구간 준비:", info_d, f"{time.time()-t0:.0f}s")
df_design = N.replicate_design(prep_d, REF)            # 불일치 시 AssertionError로 중단
print("설계구간 재현 OK: 11개 후보 결정적 열이 기존 gate2_results.csv와 일치")
del prep_d
t1 = time.time()
prep_n, info_n = N.build_prep(paths, N.NEAR, "near")
print("근접구간 준비:", info_n, f"{time.time()-t1:.0f}s")
df, meta = N.run_near(prep_n)
N.print_report(df, meta, df_design)
df.to_csv(os.path.join(OUT, "near_results.csv"), index=False, encoding="utf-8-sig")
json.dump(meta, open(os.path.join(OUT, "near_summary.json"), "w"), ensure_ascii=False, indent=1)
print("\n결과 파일:", OUT, f"| 총 {time.time()-t0:.0f}s")
