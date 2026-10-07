# [클론 셀] 근접 검증용. main / delisted-data / pit-data(sector만). DART는 이번 작업에 불필요.
import os, glob, subprocess, time
t0 = time.time()
def sh(c): subprocess.run(c, shell=True, check=True)
for br, dst in (("main", "/content/main"), ("delisted-data", "/content/delisted")):
    sh(f"rm -rf {dst}; git clone -q --depth 1 --single-branch -b {br} https://github.com/stanleyim/new.git {dst}")
sh("rm -rf /content/pit; git clone -q --depth 1 --single-branch --filter=blob:none --sparse -b pit-data https://github.com/stanleyim/new.git /content/pit")
sh("git -C /content/pit sparse-checkout set --no-cone 'pit_data_extra/sector_*.parquet'")
chk = {"ohlcv_full.parquet": (len(glob.glob("/content/main/data/ohlcv_full.parquet")), 1),
       "KIS parquet": (len(glob.glob("/content/delisted/delisted_data/*.parquet")), 2916),
       "sector parquet": (len(glob.glob("/content/pit/pit_data_extra/sector_*.parquet")), 13)}
for k, (got, exp) in chk.items():
    print(("OK " if got == exp else "불일치 ") + f"{k}: {got} (기대 {exp})")
print(f"클론 완료 {time.time()-t0:.0f}s")
