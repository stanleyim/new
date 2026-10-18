# [Colab] 데이터 무결성 감사용 main 클론 + 확인 (읽기 전용, 레포 변경 없음)
import os, subprocess, glob, shutil, time
import pandas as pd

t0 = time.time()
DST = "/content/audit_main"          # 기존 /content/main 등과 분리된 전용 경로
if os.path.isdir(DST):
    shutil.rmtree(DST)
subprocess.run(["git", "clone", "--depth", "1", "--single-branch", "-b", "main",
                "https://github.com/stanleyim/new.git", DST], check=True)

head = subprocess.run(["git", "-C", DST, "log", "-1", "--format=%h %ci %s"],
                      capture_output=True, text=True).stdout.strip()
print("HEAD:", head)

def rel(p): return os.path.relpath(p, DST)
def find(pattern):
    return sorted(p for p in glob.glob(os.path.join(DST, "**", pattern), recursive=True)
                  if "/.git/" not in p)

# 1) 필수 파일
for f in ["data/ohlcv_full.parquet", "signal_runner.py"]:
    p = os.path.join(DST, f)
    print(f"{'OK ' if os.path.isfile(p) else 'MISSING'} {f}"
          + (f"  ({os.path.getsize(p)/1e6:.1f} MB)" if os.path.isfile(p) else ""))

# 2) ohlcv_full 요약
p = os.path.join(DST, "data/ohlcv_full.parquet")
if os.path.isfile(p):
    df = pd.read_parquet(p, columns=["date", "ticker"])
    d = pd.to_datetime(df["date"])
    print(f"ohlcv_full: rows {len(df):,} · tickers {df['ticker'].nunique()} · "
          f"{d.min().date()} ~ {d.max().date()} · 06-29 이후 rows {(d >= '2026-06-29').sum():,}")

# 3) 포워드 기록 파일 위치 (경로 가정 없이 탐색)
for name in ["holdings*.json", "results*.json", "drop_alerts_sent*.json", "last_notified*"]:
    hits = find(name)
    print(f"{name}: " + (", ".join(rel(h) for h in hits) if hits else "없음"))
for d_ in ["signals", "all_signals_log"]:
    dirs = [p for p in find(d_) if os.path.isdir(p)]
    for dd in dirs:
        print(f"{rel(dd)}/: {len(os.listdir(dd))} files")
    if not dirs:
        print(f"{d_}/: 없음")

# 4) 수집 경로 확인용 목록
print("workflows:", ", ".join(os.path.basename(p) for p in find("*.yml") if "/.github/workflows/" in p) or "없음")
print("root .py:", ", ".join(sorted(os.path.basename(p) for p in glob.glob(os.path.join(DST, "*.py")))) or "없음")
print(f"done {time.time()-t0:.0f}s")
