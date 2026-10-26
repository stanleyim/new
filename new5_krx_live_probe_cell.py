# new5_krx_live_probe_cell.py (v3: 보안 비밀 실패 시 입력창) — Colab 셀 1개 (10-26 장중 실행용)
# 목적: pykrx(KRX) 당일 일봉이 장중에 실시간으로 갱신되는지 확인. 레포·기록은 건드리지 않음(읽기만).
# 판정: 3분 사이 거래량이 늘면 "실시간 갱신" → 18:52 실행값은 확정값이 아님
#       행 수 0이면 "장 마감 후 게시" → 게시 시각은 오늘 밤 셀로 측정
# 실행 전: 런타임 → 세션 다시 시작(이전 셀에서 pykrx가 로그인 없이 이미 import됨)
!pip -q install pykrx
import os, time, datetime as dt
import getpass
for k in ("KRX_ID", "KRX_PW"):                  # 1순위 Colab 보안 비밀(🔑), 실패하면 입력창(값은 화면·파일에 안 남음)
    if os.environ.get(k):
        continue
    try:
        from google.colab import userdata
        os.environ[k] = userdata.get(k)
    except Exception as e:
        print(f"{k} 보안 비밀 읽기 실패({type(e).__name__}) → 입력창에 직접 입력")
        os.environ[k] = getpass.getpass(f"{k}: ")
print("KRX_ID 설정:", bool(os.environ.get("KRX_ID")), "· KRX_PW 설정:", bool(os.environ.get("KRX_PW")))  # 값은 출력 안 함
from pykrx import stock                          # 로그인은 환경변수 설정 뒤에 import

KST = dt.timezone(dt.timedelta(hours=9))
DAY = "20261026"
T = ["005930", "000660", "009150"]          # 삼성전자·SK하이닉스·삼성전기 (모두 KOSPI)

def snap():
    now = dt.datetime.now(KST).strftime("%H:%M:%S")
    try:
        x = stock.get_market_ohlcv(DAY, market="KOSPI")
    except Exception as e:
        print(now, "조회 오류:", type(e).__name__, str(e)[:200])
        return None
    print(now, "KST · 행 수", len(x))
    if len(x) == 0:
        return None
    y = x.loc[[t for t in T if t in x.index], ["종가", "거래량"]]
    print(y)
    return y

a = snap()
time.sleep(180)
b = snap()
if a is not None and b is not None:
    d = (b["거래량"] - a["거래량"]).astype(int)
    print("\n3분간 거래량 변화\n", d)
    print("\n판정:", "실시간 갱신 (장중 값이 계속 바뀜)" if (d != 0).any() else "3분간 변화 없음 (아래 출력 그대로 붙여 주세요)")
else:
    print("\n판정: 당일 자료 없음 또는 오류 (출력 그대로 붙여 주세요)")
