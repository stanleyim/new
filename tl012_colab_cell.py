# [TL-012 본 실행 셀] 선행: hedge_near_clone_cell.py 실행(레포 main에 research_tl012.zip을 올린 뒤 클론하거나, zip을 /content에 업로드).
# 사전등록(TL-012): RISK h10·h20, 그룹 439 제외(A+B) 유니버스의 2023-01-02~2024-12-30 근접 검증. TL-005와 동일 코드·규칙(near_run 재사용), null=허용 이동량 전부,
# 판정 ①초과 E p+family BH(q=0.10) ②블록 양비율≥null 95th ③Q5 비용 후 절대순수익>0, 왕복 0.206%. 2025-01 이후 미적재, E1 비접촉.
# 순서: ①합성 테스트 10종 ②설계구간 전 유니버스 재현(Drive new_g2_out/gate2_results.csv) ③설계구간 A+B 재현(Drive new_tl011_out/g2/gate2_results.csv)
#       -> 둘 다 일치해야만 ④2023~24 평가(불일치 시 AssertionError로 중단, 2023~24 결과 미산출) ⑤사후 진단. 예상 소요 10~20분(미실측).
import os, sys, subprocess, shutil, zipfile, time

OUT = "/content/tl012_out"; REF_G2 = "/content/g2_ref/gate2_results.csv"; REF_T11 = "/content/t11_ref/gate2_results.csv"; REF_NEAR = "/content/near_ref/near_results.csv"
try:
    from google.colab import drive
    drive.mount("/content/drive")
    B = "/content/drive/MyDrive"
    OUT = f"{B}/new_tl012_out"; REF_G2 = f"{B}/new_g2_out/gate2_results.csv"; REF_T11 = f"{B}/new_tl011_out/g2/gate2_results.csv"; REF_NEAR = f"{B}/new_near_out/near_results.csv"
except Exception as e:
    print("Drive 마운트 실패:", repr(e)[:120])
os.makedirs(OUT, exist_ok=True)
assert os.path.exists(REF_G2), f"Gate 2 결과 파일 없음: {REF_G2}"
assert os.path.exists(REF_T11), f"TL-011 Gate 2 결과 파일 없음: {REF_T11}"
if not os.path.exists(REF_NEAR):
    print("참고: TL-005 near_results.csv 없음 -> 전 유니버스 근접 병기 생략:", REF_NEAR)
ZIP = "/content/research_tl012.zip"
if not os.path.exists(ZIP) and os.path.exists("/content/main/research_tl012.zip"):
    shutil.copy("/content/main/research_tl012.zip", ZIP)
assert os.path.exists(ZIP), f"{ZIP} 없음"
shutil.rmtree("/content/rebuild", ignore_errors=True)
zipfile.ZipFile(ZIP).extractall("/content/rebuild")
sys.path.insert(0, "/content/rebuild")
import numba; print("numba", numba.__version__)
for t in ("test_synthetic", "test_gate1", "test_d5_run", "test_gate2", "test_g2_diag", "test_tl009", "test_tl010", "test_tl011", "test_near", "test_tl012"):
    r = subprocess.run([sys.executable, f"/content/rebuild/research/tests/{t}.py"], capture_output=True, text=True,
                       cwd="/content/rebuild", env={**os.environ, "PYTHONPATH": "/content/rebuild"})
    print(r.stdout.strip().splitlines()[-1] if r.stdout.strip() else "(출력 없음)", f"<- {t}")
    assert r.returncode == 0 and "ALL PASS" in r.stdout, r.stdout[-1500:] + r.stderr[-1500:]

from research import near_run as N, tl012 as Y
paths = ("/content/main/data/ohlcv_full.parquet", "/content/delisted/delisted_data", "/content/pit/pit_data_extra/sector_*.parquet")
t0 = time.time()
prep_d, grp_d, px_d, info_d = Y.build_prep_g(paths, N.DESIGN, "design")
print("설계구간 준비:", info_d, f"{time.time()-t0:.0f}s")
t1 = time.time()
prep_n, grp_n, px_n, info_n = Y.build_prep_g(paths, N.NEAR, "near")
print("근접구간 준비:", info_n, f"{time.time()-t1:.0f}s")
o = Y.run_tl012(prep_d, grp_d, prep_n, grp_n, px_n, REF_G2, REF_T11, OUT, ref_near_csv=REF_NEAR)
Y.print_report(o)
import json
json.dump(o["meta"], open(os.path.join(OUT, "tl012_summary.json"), "w"), ensure_ascii=False, indent=1, default=str)
print("\n결과 파일:", OUT, "(tl012_near_results.csv, tl012_vs_full_near.csv, tl012_summary.json, diag/*.csv)", f"| 총 {(time.time()-t0)/60:.0f}분")
