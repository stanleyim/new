# [클론 셀] 새 정보원 1단계(잠정실적 본문 수집 가능성). main(ohlcv 종목 목록) + dart-data + pit-data(sector, 3,355 로스터). 읽기 전용.
import glob, json, subprocess, time
t0 = time.time()
def sh(c): subprocess.run(c, shell=True, check=True)
for br, dst in (("main", "/content/main"), ("dart-data", "/content/dart")):
    sh(f"rm -rf {dst}; git clone -q --depth 1 --single-branch -b {br} https://github.com/stanleyim/new.git {dst}")
sh("rm -rf /content/pit; git clone -q --depth 1 --single-branch --filter=blob:none --sparse -b pit-data https://github.com/stanleyim/new.git /content/pit")
sh("git -C /content/pit sparse-checkout set --no-cone 'pit_data_extra/sector_*.parquet'")
prog = json.load(open("/content/dart/dart_data/progress.json"))
chk = {"ohlcv_full.parquet": (len(glob.glob("/content/main/data/ohlcv_full.parquet")), 1),
       "DART csv(2018~2026)": (len(glob.glob("/content/dart/dart_data/*.csv")), 9),
       "progress completed": (prog.get("completed"), True),
       "sector parquet": (len(glob.glob("/content/pit/pit_data_extra/sector_*.parquet")), 13)}
for k, (got, exp) in chk.items():
    print(("OK " if got == exp else "불일치 ") + f"{k}: {got} (기대 {exp})")
for d in ("/content/main", "/content/dart", "/content/pit"):
    print(d, subprocess.run(f"git -C {d} log -1 --format='%h %ci %s'", shell=True, capture_output=True, text=True).stdout.strip())
try:
    from google.colab import userdata
    k = userdata.get("DART_API_KEY")
    print(f"OK Colab 보안 비밀 DART_API_KEY 읽기 (길이 {len(k)})")
except Exception as e:
    print(f"불일치 DART_API_KEY 읽기 실패: {e}")
print(f"클론 완료 {time.time()-t0:.0f}s")
