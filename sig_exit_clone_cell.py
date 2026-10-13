# [클론 셀] 청산 규칙 검증용. main 브랜치만(data/ohlcv_full·flow_full·short_full·universe).
import os, glob, subprocess, time
t0 = time.time()
def sh(c): subprocess.run(c, shell=True, check=True)
sh("rm -rf /content/main; git clone -q --depth 1 --single-branch -b main https://github.com/stanleyim/new.git /content/main")
need = ["ohlcv_full", "flow_full", "short_full", "universe"]
for n in need:
    got = len(glob.glob(f"/content/main/data/{n}.parquet"))
    print(("OK " if got == 1 else "불일치 ") + f"{n}.parquet: {got} (기대 1)")
print(f"클론 완료 {time.time()-t0:.0f}s")
