"""
000420(로케트전기) 응답 이상현상(4개월 요청 -> 742건 반환) 진단 전용 스크립트.
읽기 전용. 기존 파일/워크플로우 수정 없음. 000420 단일 종목만 조회.
원인을 결론 내리지 않고, 요청받은 10개 항목 + 종목명/코드 확인만 사실로 남긴다.
"""
import os
import json
import time
import requests
from datetime import datetime
from collections import Counter

KIS_BASE = "https://openapi.koreainvestment.com:9443"
TICKER = "000420"
START = "20141104"
END = "20150304"  # 어제 이상현상이 나온 것과 동일한 (a) 구간, 그대로 재현


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


def call_itemchartprice(session, token, app_key, app_secret, ticker, start, end):
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
        "FID_INPUT_DATE_1": start,
        "FID_INPUT_DATE_2": end,
        "FID_PERIOD_DIV_CODE": "D",
        "FID_ORG_ADJ_PRC": "0",
    }
    r = session.get(
        f"{KIS_BASE}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
        headers=headers, params=params, timeout=30,
    )
    print(f"[요청] HTTP {r.status_code}  구간 {start}~{end}  ticker={ticker}")
    data = r.json()
    print(f"[응답상태] rt_cd={data.get('rt_cd')}  msg_cd={data.get('msg_cd')}  msg1={data.get('msg1')}")
    return data


def call_inquire_price(session, token, app_key, app_secret, ticker):
    """현재 이 코드에 매핑된 종목명/상태 확인용 (원인 결론 아님, 사실 확인용)."""
    headers = {
        "content-type": "application/json",
        "authorization": f"Bearer {token}",
        "appkey": app_key,
        "appsecret": app_secret,
        "tr_id": "FHKST01010100",
    }
    params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker}
    r = session.get(
        f"{KIS_BASE}/uapi/domestic-stock/v1/quotations/inquire-price",
        headers=headers, params=params, timeout=30,
    )
    print(f"[현재종목조회] HTTP {r.status_code}")
    data = r.json()
    print(f"[현재종목조회 응답상태] rt_cd={data.get('rt_cd')}  msg_cd={data.get('msg_cd')}  msg1={data.get('msg1')}")
    return data


def main():
    token, app_key, app_secret = get_kis_token()
    session = requests.Session()

    print("=" * 90)
    print(f"000420 이상현상 재현: {START}~{END} 동일 재요청")
    print("=" * 90)
    data = call_itemchartprice(session, token, app_key, app_secret, TICKER, START, END)

    print()
    print("--- output1 (헤더/요약 블록) 전체 원본 ---")
    output1 = data.get("output1")
    print(json.dumps(output1, ensure_ascii=False, indent=2))

    output2 = data.get("output2") or []
    print()
    print(f"--- output2 총 건수: {len(output2)} ---")

    # 파일로 전체 raw 저장 (재검증용)
    with open("000420_raw_output2.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("[저장] 000420_raw_output2.json 에 전체 raw 응답 저장 (artifact 업로드 예정)")

    dates_raw = [o.get("stck_bsop_date", "") for o in output2]

    # 1. 날짜 중복 여부 / 6. 동일 날짜 여러 번
    counts = Counter(dates_raw)
    dup_dates = {d: c for d, c in counts.items() if c > 1}
    print()
    print(f"[1/6. 날짜 중복] 고유하지 않은 날짜(2회 이상 등장) 종류 수: {len(dup_dates)}")
    if dup_dates:
        print(f"  중복 상세(최대 20개만 표시): {dict(list(dup_dates.items())[:20])}")

    # 2. 정렬 여부
    is_desc = all(dates_raw[i] >= dates_raw[i + 1] for i in range(len(dates_raw) - 1))
    is_asc = all(dates_raw[i] <= dates_raw[i + 1] for i in range(len(dates_raw) - 1))
    print(f"[2. 정렬 여부] 내림차순 정렬됨={is_desc}  오름차순 정렬됨={is_asc}  (둘 다 False면 뒤섞여 있음)")

    # 3. 실제 고유 날짜 수
    uniq_dates = sorted(set(dates_raw))
    print(f"[3. 실제 고유 날짜 수] {len(uniq_dates)}  (전체 output2 건수 {len(output2)}와 비교)")

    # 4. 최소/최대 날짜
    if uniq_dates:
        print(f"[4. 최소/최대 날짜] 최소={uniq_dates[0]}  최대={uniq_dates[-1]}")

    # 5. 날짜 간격 분포 (고유 날짜를 오름차순 정렬 후 연속 차이, 영업일 기준이라 1~4일이 정상)
    gap_counter = Counter()
    fmt = "%Y%m%d"
    for a, b in zip(uniq_dates[:-1], uniq_dates[1:]):
        try:
            gap = (datetime.strptime(b, fmt) - datetime.strptime(a, fmt)).days
            gap_counter[gap] += 1
        except ValueError:
            gap_counter["파싱실패"] += 1
    print(f"[5. 날짜 간격 분포(일)] {dict(sorted(gap_counter.items(), key=lambda x: str(x[0])))}")

    # 9. 요청 기간 밖 날짜 포함 여부
    out_of_range = [d for d in uniq_dates if d and (d < START or d > END)]
    print(f"[9. 요청기간({START}~{END}) 밖 날짜 포함 여부] {len(out_of_range)}건")
    if out_of_range:
        print(f"  범위 밖 날짜 목록(최대 30개): {out_of_range[:30]}")

    # 7/8. 종목코드 관련 필드 확인 (output2 각 행 자체에 코드 필드가 있는지 원본 키로 확인)
    if output2:
        sample_keys = list(output2[0].keys())
        print(f"[7. output2 행의 전체 필드 목록] {sample_keys}")
        code_like_keys = [k for k in sample_keys if "cd" in k.lower() or "iscd" in k.lower() or "shrn" in k.lower()]
        print(f"[8. 종목코드로 보이는 필드] {code_like_keys if code_like_keys else '없음 -- output2 행 자체에는 종목코드 필드가 없음'}")

    # 10. OHLCV 일관성 (고가>=시가/저가/종가, 저가<=시가/종가)
    violations = []
    for o in output2:
        try:
            op, hi, lo, cl = float(o.get("stck_oprc", 0)), float(o.get("stck_hgpr", 0)), float(o.get("stck_lwpr", 0)), float(o.get("stck_clpr", 0))
            if not (lo <= op <= hi and lo <= cl <= hi and lo <= hi):
                violations.append(o.get("stck_bsop_date"))
        except (ValueError, TypeError):
            violations.append(o.get("stck_bsop_date"))
    print(f"[10. OHLCV 일관성] 위반(고가<저가 또는 시가/종가가 고가-저가 범위 밖) 건수: {len(violations)}")
    if violations:
        print(f"  위반 날짜(최대 30개): {violations[:30]}")

    # 전체 날짜 목록 로그에 남김 (재검증용)
    print()
    print("[전체 고유 날짜 목록 (오름차순)]")
    print(uniq_dates)

    print()
    print("=" * 90)
    print("현재 000420 코드에 매핑된 종목명/상태 확인 (코드 재사용 여부 판단용 사실 수집, 결론 아님)")
    print("=" * 90)
    price_data = call_inquire_price(session, token, app_key, app_secret, TICKER)
    print(json.dumps(price_data.get("output", {}), ensure_ascii=False, indent=2))

    print()
    print("=" * 90)
    print("진단 종료 -- 원인 결론 내리지 않음. 위 원본 수치만으로 판단할 것")
    print("=" * 90)


if __name__ == "__main__":
    main()
