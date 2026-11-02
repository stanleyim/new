"""
KIS 코스피200 야간선물 조회 가능 여부 시험 (한 번만 실행, 읽기 전용, 주문 없음, 레포에 아무것도 쓰지 않음)

확인할 것
 [1] 지수선물 마스터에서 코스피200 최근월물(월물구분 1) 찾기 — 처음 5줄을 그대로 출력
 [2] 그 코드(전체·앞 6자리 두 형식)로 현재가 조회(시장 F) → 되는 형식을 씀
 [3] 주간(F) 1분봉: 직전 거래일 15:30 봉이 있는지
 [4] 야간(CM) 1분봉: 직전 거래일 밤 → 06:00(입력 300000 = 다음날 06:00)까지 봉이 있는지, 마지막 봉 시각·가격
근거: KIS 샘플(Postman v2.6) 분봉조회 FHKIF03020200 설명 — "CM: 야간선물", "자정 이후 시간은 +24시간으로 입력"
출력만 함. 비밀값은 찍지 않음.
"""
import io
import os
import sys
import json
import zipfile
import datetime as dt
import requests

BASE = "https://openapi.koreainvestment.com:9443"
MASTER = "https://new.real.download.dws.co.kr/common/master/fo_idx_code_mts.mst.zip"
KST = dt.timezone(dt.timedelta(hours=9))
S = requests.Session()


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
    r = S.get(f"{BASE}{path}", headers=h, params=params, timeout=30)
    try:
        j = r.json()
    except Exception:
        j = {"_text": r.text[:300]}
    return r.status_code, j


def futures_codes(get=None):
    """마스터(fo_idx_code_mts.mst, '|' 구분)에서 지수선물(info_type 1) 행을 모두 돌려줌.
    필드: 0 상품종류 · 1 단축코드 · 2 표준코드 · 3 한글명 · 4 ATM · 5 행사가 · 6 월물구분(1=최근월물) · 7 기초 단축코드 · 8 기초명
    (KIS stocks_info/종목마스터정보(지수선물옵션).h). 코드 길이·앞자리는 가정하지 않음."""
    raw = (get or S.get)(MASTER, timeout=60).content
    z = zipfile.ZipFile(io.BytesIO(raw))
    text = z.read(z.namelist()[0]).decode("cp949", errors="replace")
    lines = text.splitlines()
    log(f"[1] 마스터 {len(lines)}줄 · 처음 5줄:")
    for ln in lines[:5]:
        log(f"    {ln[:160]}")
    rows = []
    for ln in lines:
        f = [x.strip() for x in ln.split("|")]
        if len(f) >= 9 and f[0] == "1":
            rows.append(f)
    log(f"    지수선물(상품종류 1) {len(rows)}개:")
    for f in rows[:12]:
        log(f"    단축 {f[1]} · 이름 {f[3]} · 월물구분 {f[6]} · 기초 {f[7]} {f[8]}")
    return rows


def prev_trading_day(today):
    d = today - dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d


def show_bars(tag, st, j, n=3):
    log(f"{tag}: HTTP {st} · rt_cd {j.get('rt_cd')} · msg {str(j.get('msg1', ''))[:60]}")
    o2 = j.get("output2") or []
    log(f"    봉 개수 {len(o2)}")
    if o2:
        log(f"    필드: {sorted(o2[0].keys())}")
        for b in o2[:n]:
            log(f"    {json.dumps(b, ensure_ascii=False)[:220]}")
        log(f"    ... 마지막: {json.dumps(o2[-1], ensure_ascii=False)[:220]}")
    o1 = j.get("output1")
    if isinstance(o1, dict):
        log(f"    output1 일부: {json.dumps({k: o1[k] for k in list(o1)[:12]}, ensure_ascii=False)[:300]}")
    return o2


def main():
    key, sec = os.environ.get("KIS_APP_KEY"), os.environ.get("KIS_APP_SECRET")
    if not key or not sec:
        log("KIS 비밀값 없음 → 중단")
        sys.exit(1)
    now = dt.datetime.now(KST)
    d0 = prev_trading_day(now.date())
    log(f"실행 {now:%Y-%m-%d %H:%M} KST · 직전 평일 {d0}")

    rows = futures_codes()
    k200 = [f for f in rows if "200" in f[3] or "200" in f[8]]
    front = [f for f in k200 if f[6] == "1"] or k200[:1]
    if not front:
        log("    코스피200 선물 행을 못 찾음 → 위 처음 5줄을 보고 고쳐야 함")
        sys.exit(1)
    full = front[0][1]
    cands = [full] + ([full[:6]] if len(full) > 6 else [])
    log(f"    → 최근월물 후보: {front[0][3]} · 시험할 코드 {cands}")

    tok = token(key, sec)
    code = None
    for c in cands:
        st, j = q(tok, key, sec, "/uapi/domestic-futureoption/v1/quotations/inquire-price", "FHMIF10000000",
                  {"FID_COND_MRKT_DIV_CODE": "F", "FID_INPUT_ISCD": c})
        o1 = j.get("output1") or {}
        log(f"[2] 주간 F 현재가 {c}: HTTP {st} rt_cd {j.get('rt_cd')} msg {str(j.get('msg1', ''))[:50]} · 현재가 {o1.get('futs_prpr')} · 누적거래량 {o1.get('acml_vol')}")
        if code is None and j.get("rt_cd") == "0" and o1.get("futs_prpr"):
            code = c
    if code is None:
        log("    두 형식 모두 현재가 조회 실패 → 중단")
        sys.exit(1)
    log(f"    → 조회에 쓸 코드: {code}")

    path = "/uapi/domestic-futureoption/v1/quotations/inquire-time-fuopchartprice"
    base = {"FID_INPUT_ISCD": code, "FID_HOUR_CLS_CODE": "60", "FID_PW_DATA_INCU_YN": "Y", "FID_FAKE_TICK_INCU_YN": "N"}
    ds = d0.strftime("%Y%m%d")

    st, j = q(tok, key, sec, path, "FHKIF03020200", {**base, "FID_COND_MRKT_DIV_CODE": "F",
                                                   "FID_INPUT_DATE_1": ds, "FID_INPUT_HOUR_1": "153000"})
    show_bars(f"[3] 주간 F {ds} 15:30부터 거꾸로", st, j)

    for tag, dd, hh in [("[4a] 야간 CM 직전 평일 날짜, 300000(=다음날 06:00)", ds, "300000"),
                        ("[4b] 야간 CM 오늘 날짜, 060000", now.strftime("%Y%m%d"), "060000"),
                        ("[4c] 야간 CM 직전 평일 날짜, 235900", ds, "235900")]:
        st, j = q(tok, key, sec, path, "FHKIF03020200", {**base, "FID_COND_MRKT_DIV_CODE": "CM",
                                                       "FID_INPUT_DATE_1": dd, "FID_INPUT_HOUR_1": hh})
        show_bars(tag, st, j)

    st, j = q(tok, key, sec, "/uapi/domestic-futureoption/v1/quotations/inquire-price", "FHMIF10000000",
              {"FID_COND_MRKT_DIV_CODE": "CM", "FID_INPUT_ISCD": code})
    o1 = j.get("output1") or {}
    log(f"[5] 야간 CM 현재가: HTTP {st} rt_cd {j.get('rt_cd')} msg {str(j.get('msg1', ''))[:60]} · 현재가 {o1.get('futs_prpr')}")
    log("끝. 이 로그를 Claude가 Actions에서 직접 읽음.")


if __name__ == "__main__":
    main()
