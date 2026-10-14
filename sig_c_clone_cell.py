# [클론 셀] C단계용. main(data·signal_runner.py·sig_grid_cell.py) + pit-data(idx_*.parquet만). 이 셀 실행 후 sig_c_cell.py 실행.
import os, glob, subprocess, time
t0 = time.time()
def sh(c): subprocess.run(c, shell=True, check=True)
sh("rm -rf /content/main; git clone -q --depth 1 --single-branch -b main https://github.com/stanleyim/new.git /content/main")
sh("rm -rf /content/pit; git clone -q --depth 1 --single-branch --filter=blob:none --sparse -b pit-data https://github.com/stanleyim/new.git /content/pit")
sh("git -C /content/pit sparse-checkout set --no-cone 'pit_data_extra/idx_*.parquet'")
chk = {"main data parquet": (len(glob.glob("/content/main/data/*.parquet")), 4),
       "idx parquet": (len(glob.glob("/content/pit/pit_data_extra/idx_*.parquet")), 12),
       "idx_2026.parquet": (len(glob.glob("/content/pit/pit_data_extra/idx_2026.parquet")), 1),
       "signal_runner.py": (len(glob.glob("/content/main/signal_runner.py")), 1),
       "sig_grid_cell.py": (len(glob.glob("/content/main/sig_grid_cell.py")), 1)}
bad = 0
for k, (got, exp) in chk.items():
    ok = got == exp
    bad += (not ok)
    print(("OK " if ok else "불일치 ") + f"{k}: {got} (기대 {exp})")
assert bad == 0, "클론 점검 실패 - sig_c_cell.py 실행 금지"
print(f"클론 완료 {time.time()-t0:.0f}s")
