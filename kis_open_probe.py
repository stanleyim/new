"""
kis_open_probe.py — KIS 과거 분봉으로 09:00 시가 순간을 볼 수 있는지 시험 (한 번 실행, 읽기 전용, 주문 없음, 레포에 쓰지 않음)

근거(KIS 공식 샘플 koreainvestment/open-trading-api):
 - 주식일별분봉조회 FHKST03010230 /uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice
   날짜·시각 입력으로 과거일자 분봉 조회, 1회 최대 120건, "최대 1년 분봉 보관". 시장 J. ETF도 같은 API인지는 이 시험으로 확인.
   응답 output2: stck_bsop_date, stck_cntg_hour, stck_prpr, stck_oprc, stck_hgpr, stck_lwpr, cntg_vol, acml_tr_pbmn
 - 업종 분봉조회 FHKUP03500200 /uapi/domestic-stock/v1/quotations/inquire-time-indexchartprice
   입력에 날짜가 없음(시간 간격·과거포함 여부만) → 과거일자 조회 가능 여부를 이 시험으로 확인.
 - 선물 분봉 FHKIF03020200 (kis_night_probe.py에서 11-02 15:30 봉 확인됨). 과거 월물 코드는 마스터 형식
   A01612(2026-12)·A01703(2027-03)에서 'A01 + 연도 끝자리 + 월'로 만든 것 → 응답으로 맞는지 확인.

확인할 것(출력만):
 [1] ETF 4종 × 날짜 7개: 그날 08:59~09:10 1분봉 원자료, 09:00 봉 거래량, 응답 날짜(보관 기간 밖이면 다른 날짜가 오는지)
 [2] 선물 최근월물 같은 날짜 08:59~09:10 1분봉 원자료
 [3] 지수(코스피200) 분봉: 응답 날짜 범위(오늘만 오는지)
"""
import os
import sys
import json
import time
import requests

BASE = "https://openapi.koreainvestment.com:9443"
S = requests.Session()
ETFS = {"069500": "KODEX 200", "105190": "ACE 200", "122630": "KODEX 레버리지", "123320": "TIGER 레버리지"}
DATES = ["20261103", "20260731", "20260305", "20251104", "20251103", "20251031", "20241115"]
FUT = {"20261103": "A01612", "20260731": "A01609", "20260305": "A01603", "20251104": "A01512",
       "20251103": "A01512", "20251031": "A01512", "20241115": "A01412"}


def log(*a):
    print(*a, flush=True)


def token(key, sec):
    r = S.post(f"{BASE}/oauth2/tokenP", json={"grant_type": "client_credentials", "appkey": key, "appsecret": sec}, timeout=30)
    log(f"[0] 토큰 발급: {r.status_code}")
    r.raise_for_status()
    return r.json()["access_token"]


def q(tok, key, sec, path, tr_id, params):
    h = {"content-type": "application/json; charset=utf-8", "authorization": f"Bearer {tok}",
         "appkey": key, "appsecret": sec, "tr_id": tr_id, "custtype": "P"}
    try:
        r = S.get(f"{BASE}{path}", headers=h, params=params, timeout=30)
        j = r.json()
    except Exception as e:
        return 0, {"_err": f"{type(e).__name__}"}
    finally:
        time.sleep(0.3)
    return r.status_code, j


def show(tag, st, j, ds, lo="085900", hi="091000"):
    o2 = j.get("output2") or []
    dates = sorted({b.get("stck_bsop_date") for b in o2})
    log(f"{tag}: HTTP {st} · rt_cd {j.get('rt_cd')} · msg {str(j.get('msg1', j.get('_err', '')))[:50]} · 봉 {len(o2)} · 응답 날짜 {dates}")
    if o2:
        log(f"    필드 {sorted(o2[0].keys())}")
    sel = sorted([b for b in o2 if b.get("stck_bsop_date") == ds and lo <= str(b.get("stck_cntg_hour", "")) <= hi],
                 key=lambda b: b["stck_cntg_hour"])
    for b in sel:
        log(f"    {json.dumps(b, ensure_ascii=False)}")
    if not sel:
        log(f"    {ds} {lo}~{hi} 봉 없음" + (f" · 첫 봉 {json.dumps(o2[0], ensure_ascii=False)[:200]}" if o2 else ""))


def main():
    key, sec = os.environ.get("KIS_APP_KEY"), os.environ.get("KIS_APP_SECRET")
    if not key or not sec:
        log("KIS 비밀값 없음 → 중단")
        sys.exit(1)
    tok = token(key, sec)

    log("\n===== [1] ETF 과거 분봉 (FHKST03010230, 시장 J, 시각 091000) =====")
    for t, nm in ETFS.items():
        for ds in DATES:
            st, j = q(tok, key, sec, "/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice", "FHKST03010230",
                      {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": t, "FID_INPUT_HOUR_1": "091000",
                       "FID_INPUT_DATE_1": ds, "FID_PW_DATA_INCU_YN": "Y", "FID_FAKE_TICK_INCU_YN": ""})
            show(f"[1] {nm} {t} {ds}", st, j, ds)

    log("\n===== [2] 선물 최근월물 과거 분봉 (FHKIF03020200, 시장 F, 시각 091000) =====")
    for ds in DATES:
        st, j = q(tok, key, sec, "/uapi/domestic-futureoption/v1/quotations/inquire-time-fuopchartprice", "FHKIF03020200",
                  {"FID_COND_MRKT_DIV_CODE": "F", "FID_INPUT_ISCD": FUT[ds], "FID_HOUR_CLS_CODE": "60",
                   "FID_PW_DATA_INCU_YN": "Y", "FID_FAKE_TICK_INCU_YN": "N", "FID_INPUT_DATE_1": ds,
                   "FID_INPUT_HOUR_1": "091000"})
        show(f"[2] 선물 {FUT[ds]} {ds}", st, j, ds)

    log("\n===== [3] 지수 분봉 (FHKUP03500200, 시장 U, 코스피200 2001, 60초) =====")
    st, j = q(tok, key, sec, "/uapi/domestic-stock/v1/quotations/inquire-time-indexchartprice", "FHKUP03500200",
              {"FID_COND_MRKT_DIV_CODE": "U", "FID_ETC_CLS_CODE": "0", "FID_INPUT_ISCD": "2001",
               "FID_INPUT_HOUR_1": "60", "FID_PW_DATA_INCU_YN": "Y"})
    o2 = j.get("output2") or []
    log(f"[3] HTTP {st} · rt_cd {j.get('rt_cd')} · msg {str(j.get('msg1', ''))[:50]} · 봉 {len(o2)} · 응답 날짜 {sorted({b.get('stck_bsop_date') for b in o2})}")
    if o2:
        log(f"    필드 {sorted(o2[0].keys())}")
        log(f"    첫 봉 {json.dumps(o2[0], ensure_ascii=False)}")
        log(f"    끝 봉 {json.dumps(o2[-1], ensure_ascii=False)}")
    o1 = j.get("output1")
    if isinstance(o1, dict):
        log(f"    output1 {json.dumps(o1, ensure_ascii=False)[:300]}")
    log("끝. 이 로그를 Claude가 Actions에서 직접 읽음.")


if __name__ == "__main__":
    main()
