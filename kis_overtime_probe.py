"""
kis_overtime_probe.py — KIS가 장전 시간외종가(08:30~08:40) 체결을 주는지 시험
(한 번 수동 실행, 읽기 전용, 주문 없음, 레포에 쓰지 않음. 결과는 실행 로그에만)

근거(KIS 공식 샘플 koreainvestment/open-trading-api, examples_llm/domestic_stock/inquire_time_overtimeconclusion):
 - 주식현재가 시간외시간별체결 FHPST02310000 /uapi/domestic-stock/v1/quotations/inquire-time-overtimeconclusion
 - 입력: FID_COND_MRKT_DIV_CODE (J: 주식/ETF/ETN), FID_INPUT_ISCD, FID_HOUR_CLS_CODE (1: 시간외). 날짜 입력 없음
 - 출력 output2: stck_cntg_hour, stck_prpr, prdy_vrss, prdy_ctrt, askp, bidp, acml_vol, cntg_vol 등

확인할 것(출력만):
 [1] KODEX 200·ACE 200, 시간구분 1: 체결 시각 범위, 08:30~08:40 행 수·거래량, 원자료 앞뒤 몇 줄
 [2] 시간구분 0·2(문서에 없는 값, 탐색용): 응답이 오는지, 다른 시간대를 주는지
 [가정 점검] 실행 시각(KST), output1 원자료(날짜·전일 종가 확인용)
"""
import os
import sys
import json
import time
from datetime import datetime, timezone, timedelta

import requests

BASE = "https://openapi.koreainvestment.com:9443"
PATH = "/uapi/domestic-stock/v1/quotations/inquire-time-overtimeconclusion"
TR = "FHPST02310000"
S = requests.Session()
ETFS = {"069500": "KODEX 200", "105190": "ACE 200"}
HOUR_CODES = ["1", "0", "2"]   # 1 = 문서상 시간외. 0·2는 탐색용
KST = timezone(timedelta(hours=9))


def log(*a):
    print(*a, flush=True)


def token(key, sec):
    r = S.post(f"{BASE}/oauth2/tokenP", json={"grant_type": "client_credentials", "appkey": key, "appsecret": sec}, timeout=30)
    log(f"[0] 토큰 발급: {r.status_code}")
    r.raise_for_status()
    return r.json()["access_token"]


def q(tok, key, sec, params):
    h = {"content-type": "application/json; charset=utf-8", "authorization": f"Bearer {tok}",
         "appkey": key, "appsecret": sec, "tr_id": TR, "custtype": "P"}
    try:
        r = S.get(f"{BASE}{PATH}", headers=h, params=params, timeout=30)
        st, j = r.status_code, r.json()
    except Exception as e:
        st, j = 0, {"_err": f"{type(e).__name__}"}
    time.sleep(0.3)
    return st, j


def num(x):
    try:
        return float(str(x).replace(",", ""))
    except Exception:
        return 0.0


def show(tag, st, j):
    o1, o2 = j.get("output1"), j.get("output2") or []
    if isinstance(o2, dict):
        o2 = [o2]
    log(f"{tag}: HTTP {st} · rt_cd {j.get('rt_cd')} · msg {str(j.get('msg1', j.get('_err', '')))[:60]} · 행 {len(o2)}")
    if isinstance(o1, dict):
        log(f"    output1 {json.dumps(o1, ensure_ascii=False)[:400]}")
    if not o2:
        return
    log(f"    필드 {sorted(o2[0].keys())}")
    hrs = sorted(str(b.get("stck_cntg_hour", "")) for b in o2)
    log(f"    체결 시각 범위 {hrs[0]} ~ {hrs[-1]}")
    pre = [b for b in o2 if "083000" <= str(b.get("stck_cntg_hour", "")) <= "084059"]
    post = [b for b in o2 if "154000" <= str(b.get("stck_cntg_hour", "")) <= "160059"]
    log(f"    08:30~08:40 행 {len(pre)} · 체결량 합 {sum(num(b.get('cntg_vol')) for b in pre):,.0f}")
    log(f"    15:40~16:00 행 {len(post)} · 체결량 합 {sum(num(b.get('cntg_vol')) for b in post):,.0f}")
    for b in o2[:3]:
        log(f"    앞 {json.dumps(b, ensure_ascii=False)}")
    for b in o2[-3:]:
        log(f"    뒤 {json.dumps(b, ensure_ascii=False)}")


def main():
    log(f"[가정 점검] 실행 시각 KST {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')} "
        "(08:40 뒤면 그날 장전 체결이 끝난 시점)")
    key, sec = os.environ.get("KIS_APP_KEY"), os.environ.get("KIS_APP_SECRET")
    if not key or not sec:
        log("KIS 비밀값 없음 → 중단")
        sys.exit(1)
    tok = token(key, sec)
    for hc in HOUR_CODES:
        log(f"\n===== 시간구분 {hc} {'(문서: 시간외)' if hc == '1' else '(탐색용)'} =====")
        for t, nm in ETFS.items():
            st, j = q(tok, key, sec, {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": t, "FID_HOUR_CLS_CODE": hc})
            show(f"[{hc}] {nm} {t}", st, j)
    log("\n끝. 이 로그를 Claude가 Actions에서 직접 읽음.")


if __name__ == "__main__":
    main()
