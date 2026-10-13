# [클론 셀] N3 헤지 검증용. main(data) + pit-data(idx_*.parquet만).
import os, glob, subprocess, time
t0 = time.time()
def sh(c): subprocess.run(c, shell=True, check=True)
sh("rm -rf /content/main; git clone -q --depth 1 --single-branch -b main https://github.com/stanleyim/new.git /content/main")
sh("rm -rf /content/pit; git clone -q --depth 1 --single-branch --filter=blob:none --sparse -b pit-data https://github.com/stanleyim/new.git /content/pit")
sh("git -C /content/pit sparse-checkout set --no-cone 'pit_data_extra/idx_*.parquet'")
chk = {"main data parquet": (len(glob.glob("/content/main/data/*.parquet")), 4),
       "idx parquet": (len(glob.glob("/content/pit/pit_data_extra/idx_*.parquet")), 12)}
for k, (got, exp) in chk.items():
    print(("OK " if got == exp else "불일치 ") + f"{k}: {got} (기대 {exp})")
print(f"클론 완료 {time.time()-t0:.0f}s")
