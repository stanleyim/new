# [클론 셀] ③ 부트스트랩 MDD 분포용. main + pit-data(idx만). 읽기 전용. delisted·DART·sector 불필요.
# sig_grid_cell.py가 읽는 경로: /content/main/data/{ohlcv,flow,short}_full.parquet, /content/pit/pit_data_extra/idx_*.parquet
import os, glob, subprocess, time
t0 = time.time()
def sh(c): subprocess.run(c, shell=True, check=True)
sh("rm -rf /content/main; git clone -q --depth 1 --single-branch -b main https://github.com/stanleyim/new.git /content/main")
sh("rm -rf /content/pit; git clone -q --depth 1 --single-branch --filter=blob:none --sparse -b pit-data https://github.com/stanleyim/new.git /content/pit")
sh("git -C /content/pit sparse-checkout set --no-cone 'pit_data_extra/idx_*.parquet'")
chk = {"ohlcv_full.parquet": (len(glob.glob("/content/main/data/ohlcv_full.parquet")), 1),
       "flow_full.parquet": (len(glob.glob("/content/main/data/flow_full.parquet")), 1),
       "short_full.parquet": (len(glob.glob("/content/main/data/short_full.parquet")), 1),
       "sig_grid_cell.py": (len(glob.glob("/content/main/sig_grid_cell.py")), 1),
       "idx parquet(2015~2026)": (len(glob.glob("/content/pit/pit_data_extra/idx_*.parquet")), 12)}
for k, (got, exp) in chk.items():
    print(("OK " if got == exp else "불일치 ") + f"{k}: {got} (기대 {exp})")
for d in ("/content/main", "/content/pit"):
    print(d, subprocess.run(f"git -C {d} log -1 --format='%h %ci %s'", shell=True, capture_output=True, text=True).stdout.strip())
print(f"클론 완료 {time.time()-t0:.0f}s")
