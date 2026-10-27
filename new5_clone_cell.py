# new5_clone_cell.py — 읽기 전용 (레포·운용 변경 없음)
# main 클론 + pykrx 설치 + 보안 비밀(KRX_ID·KRX_PW) 존재 확인 + KRX 로그인 시험
import os, sys, subprocess

REPO = "/content/new"
if os.path.exists(REPO):
    subprocess.run(["rm", "-rf", REPO], check=True)
subprocess.run(["git", "clone", "-q", "--depth", "1", "https://github.com/stanleyim/new.git", REPO], check=True)
log = subprocess.run(["git", "-C", REPO, "log", "-1", "--format=%h | %cd | %s"], capture_output=True, text=True).stdout.strip()
print("[1] main 클론:", log)

subprocess.run([sys.executable, "-m", "pip", "install", "-q", "pykrx"], check=True)
import pykrx
print("[2] pykrx 버전:", getattr(pykrx, "__version__", "모름"))

from google.colab import userdata
ok = True
for k in ("KRX_ID", "KRX_PW"):
    try:
        v = userdata.get(k)
        os.environ[k] = v
        print(f"[3] 보안 비밀 {k}: 있음 ({len(v)}자)")
    except Exception as ex:
        ok = False
        print(f"[3] 보안 비밀 {k}: 없음 또는 접근 거부 → {type(ex).__name__}")

if ok:
    from pykrx import stock
    d = stock.get_market_ohlcv("20261023", market="KOSPI")
    print(f"[4] KRX 조회 시험(10-23 KOSPI 전 종목): {len(d)}행 · 삼성E&A 종가 {d.loc['028050', '종가']:,.0f} (정규장 기대값 59,800)")
print("끝.")
