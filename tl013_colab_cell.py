# [TL-013 본 실행 셀] 선행: hedge_near_clone_cell.py 실행(레포 main에 research_tl013.zip을 올린 뒤 클론하거나, zip을 /content에 업로드). pit-data에 idx 필요(클론 셀 기대값 idx 10).
# 사전등록(TL-013): RISK h20, 그룹 439 제외(A+B) 유니버스의 Q5 롱 + KOSPI200·KOSDAQ 지수 베타 헤지(TL-006과 동일 규칙: 직전 120거래일 OLS·20거래일 갱신), 설계구간 2018~22만.
# 판정 ①헤지후 일평균>0 ②헤지후 MDD 절대값<비헤지 ③2018-19·2020-22 각각 일평균>0. 실패 시 RISK 계열 완전 종료.
# 순서: ①합성 테스트 13종 ②설계구간 전 유니버스 재현(Drive new_g2_out/gate2_results.csv) ③설계구간 A+B 재현(Drive new_tl011_out/g2/gate2_results.csv) ④헤지 평가(Q5 CAGR·Sharpe·MDD가 TL-011과 일치해야 함) ⑤풀 동일 헤지 보조 진단.
# 2023~24·E1 미적재. 불일치 시 AssertionError로 중단. 예상 소요 3~5분(미실측).
import os, sys, subprocess, shutil, zipfile, time

OUT = "/content/tl013_out"; REF_G2 = "/content/g2_ref/gate2_results.csv"; REF_T11 = "/content/t11_ref/gate2_results.csv"
try:
    from google.colab import drive
    drive.mount("/content/drive")
    B = "/content/drive/MyDrive"
    OUT = f"{B}/new_tl013_out"; REF_G2 = f"{B}/new_g2_out/gate2_results.csv"; REF_T11 = f"{B}/new_tl011_out/g2/gate2_results.csv"
except Exception as e:
    print("Drive 마운트 실패:", repr(e)[:120])
os.makedirs(OUT, exist_ok=True)
assert os.path.exists(REF_G2), f"Gate 2 결과 파일 없음: {REF_G2}"
assert os.path.exists(REF_T11), f"TL-011 Gate 2 결과 파일 없음: {REF_T11}"
ZIP = "/content/research_tl013.zip"
if not os.path.exists(ZIP) and os.path.exists("/content/main/research_tl013.zip"):
    shutil.copy("/content/main/research_tl013.zip", ZIP)
assert os.path.exists(ZIP), f"{ZIP} 없음"
shutil.rmtree("/content/rebuild", ignore_errors=True)
zipfile.ZipFile(ZIP).extractall("/content/rebuild")
sys.path.insert(0, "/content/rebuild")
import numba; print("numba", numba.__version__)
for t in ("test_synthetic", "test_gate1", "test_d5_run", "test_gate2", "test_g2_diag", "test_g2_hedge", "test_g2_hedge_near", "test_tl009", "test_tl010", "test_tl011", "test_near", "test_tl012", "test_tl013"):
    r = subprocess.run([sys.executable, f"/content/rebuild/research/tests/{t}.py"], capture_output=True, text=True,
                       cwd="/content/rebuild", env={**os.environ, "PYTHONPATH": "/content/rebuild"})
    print(r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "(출력 없음)", f"<- {t}")
    assert r.returncode == 0 and "ALL PASS" in r.stdout, r.stdout[-1500:] + r.stderr[-1500:]

from research import tl013 as W
paths = ("/content/main/data/ohlcv_full.parquet", "/content/delisted/delisted_data", "/content/pit/pit_data_extra/sector_*.parquet")
PIT = "/content/pit/pit_data_extra"
t0 = time.time()
prep, group, cal, info = W.build_design(paths, label="design")
print("설계구간 준비:", info, f"{time.time()-t0:.0f}s")
o = W.run_tl013(prep, group, cal, PIT, REF_G2, REF_T11, OUT)
W.print_report(o)
print("\n결과 파일:", OUT, "(tl013_hedge_results.csv, tl013_pool_control.csv)", f"| 총 {(time.time()-t0)/60:.1f}분")
