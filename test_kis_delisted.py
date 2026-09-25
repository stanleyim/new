"""
KIS OpenAPI 상장폐지 종목 과거 일봉 데이터 제공 여부 -- 기술 검증 전용 스크립트.
읽기 전용. 파일 쓰기/커밋 없음. signal_runner.py 코드는 전혀 수정하지 않고
동일한 인증/호출 로직만 그대로 복사해서 사용.

확인 항목 (요청받은 그대로):
  1. API 요청 성공 여부
  2. HTTP/API 응답 상태 (status_code, rt_cd, msg_cd, msg1)
  3. 해당 종목의 과거 일봉 데이터 반환 여부 (output2 존재/row 수)
  4. 반환되는 최초/최종 날짜
  5. OHLCV 컬럼 존재 여부 (raw dict 키 확인)
  6. 상장폐지 이후 데이터 처리 방식 (완전히 상장폐지 이후 구간만 질의했을 때 응답)
  7. 현재 거래 가능 종목과의 응답 차이

테스트 대상 (sector_YYYY.parquet 기반 실제 상장폐지 확인 종목 3개 + 대조군 1개):
  117930 한진해운   (KOSPI,  2017년 2월 상장폐지 -- 마지막 관측 2017-03-02 스냅샷)
  000420 로케트전기 (KOSPI,  2015년 초 상장폐지 -- 마지막 관측 2015-02-02 스냅샷)
  091690 디지텍시스템(KOSDAQ, 2015년 초 상장폐지 -- 마지막 관측 2015-01-02 스냅샷)
  005930 삼성전자   (KOSPI,  현재 거래 가능 -- 대조군)
"""
import os
import time
import json
import requests

KIS_BASE = "https://openapi.koreainvestment.com:9443"

TARGETS = [
    # ticker, name, market, 스냅샷상 마지막 관측일(YYYYMMDD, 상장폐지 근사 시점)
    ("117930", "한진해운",    "KOSPI",  "20170302"),
    ("000420", "로케트전기",  "KOSPI",  "20150202"),
    ("091690", "디지텍시스템", "KOSDAQ", "20150102"),
    ("005930", "삼성전자",    "KOSPI",  None),  # 대조군, 상장폐지 아님
]


def get_kis_token():
    app_key = os.environ.get("KIS_APP_KEY")
    app_secret = os.environ.get("KIS_APP_SECRET")
    if not app_key or not app_secret:
        raise RuntimeError("KIS_APP_KEY/SECRET 환경변수 없음")
    r = requests.post(
        f"{KIS_BASE}/oauth2/tokenP",
        json={"grant_type": "client_credentials", "appkey": app_key, "appsecret": app_secret},
        timeout=30,
    )
    print(f"[토큰발급] HTTP {r.status_code}")
    r.raise_for_status()
    data = r.json()
    if "access_token" not in data:
        raise RuntimeError(f"토큰 응답 이상: {data}")
    return data["access_token"], app_key, app_secret


def call_ohlcv(session, token, app_key, app_secret, ticker, start_date, end_date):
    """signal_runner.py의 get_ohlcv와 동일한 엔드포인트/파라미터, 응답 원본을 그대로 반환."""
    headers = {
        "content-type": "application/json",
        "authorization": f"Bearer {token}",
        "appkey": app_key,
        "appsecret": app_secret,
        "tr_id": "FHKST03010100",
    }
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": ticker,
        "FID_INPUT_DATE_1": start_date,
        "FID_INPUT_DATE_2": end_date,
        "FID_PERIOD_DIV_CODE": "D",
        "FID_ORG_ADJ_PRC": "0",
    }
    t0 = time.time()
    try:
        r = session.get(
            f"{KIS_BASE}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            headers=headers, params=params, timeout=30,
        )
        elapsed = time.time() - t0
    except Exception as e:
        print(f"    [요청실패] {type(e).__name__}: {e}")
        return None

    print(f"    [요청] HTTP {r.status_code}  ({elapsed:.1f}s)  구간 {start_date}~{end_date}")
    try:
        data = r.json()
    except Exception as e:
        print(f"    [응답파싱실패] {type(e).__name__}: {e}  raw={r.text[:200]}")
        return None

    print(f"    [응답상태] rt_cd={data.get('rt_cd')}  msg_cd={data.get('msg_cd')}  msg1={data.get('msg1')}")
    out2 = data.get("output2")
    if out2 is None:
        print(f"    [output2] 없음. 최상위 키: {list(data.keys())}")
        return data
    print(f"    [output2] {len(out2)}건")
    if len(out2) > 0:
        first_row = out2[-1]  # KIS는 보통 최신순 정렬 -> 마지막 원소가 가장 과거
        last_row = out2[0]
        print(f"    [컬럼목록] {list(first_row.keys())}")
        print(f"    [반환된 날짜 범위] 최초={first_row.get('stck_bsop_date')}  최종={last_row.get('stck_bsop_date')}")
        print(f"    [샘플-최초행] {json.dumps(first_row, ensure_ascii=False)}")
        print(f"    [샘플-최종행] {json.dumps(last_row, ensure_ascii=False)}")
    return data


def shift_date(yyyymmdd, days):
    from datetime import datetime, timedelta
    d = datetime.strptime(yyyymmdd, "%Y%m%d") + timedelta(days=days)
    return d.strftime("%Y%m%d")


def main():
    token, app_key, app_secret = get_kis_token()
    session = requests.Session()

    for ticker, name, market, last_seen in TARGETS:
        print("=" * 80)
        print(f"{ticker} {name} ({market})  마지막 관측(스냅샷 기준)={last_seen}")
        print("=" * 80)

        if last_seen:
            # (a) 상장폐지 추정 시점을 걸치는 구간 -- 실제로 어디서 끊기는지 확인
            print("  (a) 상장폐지 추정 시점을 걸치는 구간")
            call_ohlcv(session, token, app_key, app_secret, ticker,
                       shift_date(last_seen, -90), shift_date(last_seen, 30))
            time.sleep(1)

            # (b) 상장폐지 이후로만 완전히 벗어난 구간 -- 응답이 어떻게 나오는지 확인
            print("  (b) 상장폐지 이후로만 벗어난 구간 (데이터가 전혀 없을 것으로 예상되는 구간)")
            call_ohlcv(session, token, app_key, app_secret, ticker,
                       shift_date(last_seen, 180), shift_date(last_seen, 240))
            time.sleep(1)
        else:
            # 대조군: 현재도 거래되는 종목, 최근 구간
            print("  (대조군) 최근 60일 구간")
            from datetime import datetime, timedelta
            today = datetime.utcnow() + timedelta(hours=9)  # KST 근사
            end = today.strftime("%Y%m%d")
            start = (today - timedelta(days=60)).strftime("%Y%m%d")
            call_ohlcv(session, token, app_key, app_secret, ticker, start, end)
            time.sleep(1)

    print("=" * 80)
    print("테스트 종료 -- 위 원본 출력만으로 판단, 여기서 결론을 내리지 않음")
    print("=" * 80)


if __name__ == "__main__":
    main()
