"""
verify_open_recover.py — KODEX 200 시가가 동시호가에서만 눌렸다가 몇 분 안에 돌아오는지 확인
(한 번 실행, 읽기 전용, 주문 없음, 레포에 쓰지 않음)

질문: verify_open_noise 로그에서 KODEX 200 시가가 지수보다 매일 약 -0.03% 낮았다(중앙 -0.0287%).
      이것이 09:00 시가 동시호가에서만 생기고 곧 돌아오는가?
방법: 같은 분의 기준 가격(코스피200 선물 최근월물, 거래 두꺼움)과 비교해 시장 전체 움직임을 지운다.
  K0 = KODEX 090000 봉 시가(= 시가 동시호가 체결가)      F0 = 선물 085900 봉 마지막 가격(09:00 직전)
  Kt = KODEX t분 봉 마지막 가격                        Ft = 선물 같은 t분 봉 마지막 가격
  x(t) = (Kt/K0 - 1) - (Ft/F0 - 1)   → 평균 > 0 이면 "시가가 눌렸다가 돌아온다"
  같은 계산을 ACE 200(시가 차이 중앙 -0.0013%, 대조군)에도 한다.
  참고로 d(t) = Kt/K0 - 1 (시가 대신 t분 뒤 팔았을 때 차이, 시장 움직임 포함)도 보고.
판정(실행 전 고정): KODEX x(5분) 평균 > 0 이고 t값 >= 2.0 → "시가 동시호가 눌림" 지지. 그 외는 지지 안 함.
봉 시각 표기: kis_open_probe 로그에서 KODEX 090000 봉 시가 = 일봉 시가, 봉 안에 여러 가격 → 봉 시각 = 그 분의 시작.
자료 근거: kis_open_probe 실행 #1 — ETF 분봉 2025-10-31까지 옴(2024-11-15는 0봉), 선물 지난 월물 A01609·A01603 옴,
          A01512(2025-11) 0봉. 그래서 받히는 날만 쓰고 받힌 기간을 출력한다.
"""
import os
import sys
import json
import time
import math
import datetime as dt
import statistics as st
import requests

BASE = "https://openapi.koreainvestment.com:9443"
S = requests.Session()
ERR = []  # KIS 오류 응답 기록
ETFS = {"069500": "KODEX 200", "105190": "ACE 200"}
START, END = dt.date(2025, 11, 3), dt.date(2026, 11, 3)
H = {"1분": "090000", "3분": "090200", "5분": "090400", "10분": "090900"}  # t분 = 이 봉의 마지막 가격(09:00:59, 09:02:59 ...)
JUDGE = "5분"


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
    for _ in range(3):
        try:
            r = S.get(f"{BASE}{path}", headers=h, params=params, timeout=30)
            j = r.json()
        except Exception as e:
            j = {"rt_cd": "X", "msg1": type(e).__name__}
        time.sleep(0.3)
        if j.get("rt_cd") == "0":
            return j
        if "초과" in str(j.get("msg1", "")):  # 호출 한도 초과면 잠깐 쉬고 다시
            time.sleep(1.5)
            continue
        ERR.append(f"{params.get('FID_INPUT_ISCD')} {params.get('FID_INPUT_DATE_1')} {j.get('rt_cd')} {str(j.get('msg1',''))[:40]}")
        return j
    ERR.append(f"{params.get('FID_INPUT_ISCD')} {params.get('FID_INPUT_DATE_1')} 재시도 초과")
    return j


def second_thu(y, m):
    d1 = dt.date(y, m, 1)
    return d1 + dt.timedelta(days=(3 - d1.weekday()) % 7 + 7)


def fut_code(d):
    """최근월물(3·6·9·12월, 둘째 목요일 만기). 만기일 당일은 그 월물. 형식 A01 + 연도 끝자리 + 월 (A01612=2026-12)."""
    y, m = d.year, d.month
    for _ in range(5):
        if m in (3, 6, 9, 12) and d <= second_thu(y, m):
            return f"A01{str(y)[-1]}{m:02d}"
        m += 1
        if m == 13:
            y, m = y + 1, 1
    raise ValueError(d)


def bars(j, ds, pk):
    """ds 날짜 봉만 {시각: 봉}. pk = 가격 필드 접두(stck / futs)."""
    out = {}
    for b in j.get("output2") or []:
        if b.get("stck_bsop_date") == ds:
            try:
                out[str(b["stck_cntg_hour"])] = (float(b[f"{pk}_oprc"]), float(b[f"{pk}_prpr"]), b)
            except (KeyError, ValueError, TypeError):
                pass
    return out


def summ(v):
    n = len(v)
    if n < 2:
        return f"n {n}"
    m, sd = st.mean(v), st.stdev(v)
    t = m / (sd / math.sqrt(n)) if sd > 0 else float("nan")
    pos = sum(1 for x in v if x > 0)
    return f"n {n:3d} · 평균 {m*100:+.4f}% · 중앙 {st.median(v)*100:+.4f}% · 표준편차 {sd*100:.4f}% · t {t:+.2f} · 양수 {pos}/{n}", m, t


def run(fetch_etf, fetch_fut):
    days, raw_shown = [], 0
    d = START
    while d <= END:
        if d.weekday() < 5:
            ds = d.strftime("%Y%m%d")
            fc = fut_code(d)
            fb = bars(fetch_fut(fc, ds), ds, "futs")
            rec = {"ds": ds, "fc": fc, "f": fb, "e": {}}
            for t in ETFS:
                rec["e"][t] = bars(fetch_etf(t, ds), ds, "stck")
            if rec["e"]["069500"] or fb:
                days.append(rec)
                if raw_shown < 2 and "090000" in rec["e"]["069500"] and "085900" in fb:
                    raw_shown += 1
                    log(f"[원자료] {ds} 선물 {fc} 085900 {json.dumps(fb['085900'][2], ensure_ascii=False)}")
                    log(f"[원자료] {ds} 선물 {fc} 090400 {json.dumps(fb.get('090400', (0, 0, {}))[2], ensure_ascii=False)}")
                    log(f"[원자료] {ds} KODEX 090000 {json.dumps(rec['e']['069500']['090000'][2], ensure_ascii=False)}")
                    log(f"[원자료] {ds} KODEX 090400 {json.dumps(rec['e']['069500'].get('090400', (0, 0, {}))[2], ensure_ascii=False)}")
        d += dt.timedelta(days=1)

    k_days = [r["ds"] for r in days if r["e"]["069500"]]
    f_days = [r["ds"] for r in days if r["f"]]
    log(f"\n[1] 받힌 날: KODEX {len(k_days)}일 ({k_days[0] if k_days else '-'}~{k_days[-1] if k_days else '-'}) · "
        f"선물 {len(f_days)}일 ({f_days[0] if f_days else '-'}~{f_days[-1] if f_days else '-'})")
    by_code = {}
    for r in days:
        by_code.setdefault(r["fc"], [0, 0])
        by_code[r["fc"]][0] += 1
        by_code[r["fc"]][1] += 1 if r["f"] else 0
    log("    선물 월물별 (요청일수, 받힌 날): " + ", ".join(f"{c} {a}/{b}" for c, (b, a) in sorted(by_code.items())))

    res = {}
    for t, nm in ETFS.items():
        log(f"\n##### {nm} ({t}) #####")
        skip = {"ETF 0900 봉 없음(늦은 개장 등)": 0, "선물 0859 봉 없음": 0}
        for hk, hh in H.items():
            xs, dk = [], []
            for r in days:
                eb, fb = r["e"][t], r["f"]
                if "090000" not in eb:
                    if hk == JUDGE:
                        skip["ETF 0900 봉 없음(늦은 개장 등)"] += 1
                    continue
                if "085900" not in fb:
                    if hk == JUDGE:
                        skip["선물 0859 봉 없음"] += 1
                    continue
                if hh not in eb or hh not in fb:
                    continue
                k0, f0 = eb["090000"][0], fb["085900"][1]
                kt, ft = eb[hh][1], fb[hh][1]
                if min(k0, f0, kt, ft) <= 0:
                    continue
                xs.append((kt / k0 - 1) - (ft / f0 - 1))
                dk.append(kt / k0 - 1)
            sx = summ(xs)
            sd = summ(dk)
            log(f"  x({hk}) = ETF 변동 − 선물 변동 : {sx[0] if isinstance(sx, tuple) else sx}")
            log(f"  d({hk}) = t분 뒤 매도 − 시가 매도  : {sd[0] if isinstance(sd, tuple) else sd}")
            if isinstance(sx, tuple):
                res[(t, hk)] = sx[1:]
        log(f"  제외(판정 {JUDGE} 기준): {skip}")

    log("\n=== 판정 (실행 전 고정: KODEX x(5분) 평균 > 0 이고 t >= 2.0 → 시가 동시호가 눌림 지지) ===")
    if ("069500", JUDGE) in res:
        m, tv = res[("069500", JUDGE)]
        ok = m > 0 and tv >= 2.0
        log(f"KODEX 200 x({JUDGE}) 평균 {m*100:+.4f}% · t {tv:+.2f} → {'지지' if ok else '지지 안 함'}")
        if ("105190", JUDGE) in res:
            ma, ta = res[("105190", JUDGE)]
            log(f"(대조) ACE 200 x({JUDGE}) 평균 {ma*100:+.4f}% · t {ta:+.2f}")
        log(f"(참고) verify_open_noise: KODEX 시가 차이 중앙 -0.0287%, ACE -0.0013%")
    else:
        log("자료 부족 → 판정 불가")
    return res


def main():
    key, sec = os.environ.get("KIS_APP_KEY"), os.environ.get("KIS_APP_SECRET")
    if not key or not sec:
        log("KIS 비밀값 없음 → 중단")
        sys.exit(1)
    tok = token(key, sec)

    def fe(t, ds):
        return q(tok, key, sec, "/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice", "FHKST03010230",
                 {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": t, "FID_INPUT_HOUR_1": "091000",
                  "FID_INPUT_DATE_1": ds, "FID_PW_DATA_INCU_YN": "Y", "FID_FAKE_TICK_INCU_YN": ""})

    def ff(code, ds):
        return q(tok, key, sec, "/uapi/domestic-futureoption/v1/quotations/inquire-time-fuopchartprice", "FHKIF03020200",
                 {"FID_COND_MRKT_DIV_CODE": "F", "FID_INPUT_ISCD": code, "FID_HOUR_CLS_CODE": "60",
                  "FID_PW_DATA_INCU_YN": "Y", "FID_FAKE_TICK_INCU_YN": "N", "FID_INPUT_DATE_1": ds,
                  "FID_INPUT_HOUR_1": "091000"})

    run(fe, ff)
    log(f"[KIS 오류 응답] {len(ERR)}건" + (f" · 처음 3건 {ERR[:3]}" if ERR else ""))
    log("끝. 이 로그를 Claude가 Actions에서 직접 읽음.")


if __name__ == "__main__":
    main()
