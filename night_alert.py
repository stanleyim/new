"""
night_alert.py — 08:30 밤사이 관찰 알림 (관찰용, 매매 규칙 불변, 읽기 전용, 주문 없음, 레포에 아무것도 쓰지 않음)

지표: KRX 코스피200 야간선물 최근월물 1개(바이낸스·EWY 안 씀).
  기준 = 직전 거래일 D 주간 15:30 봉 종가 (시장 F,  날짜 D, 시각 153000)
  끝   = D 밤 야간 마감 봉 종가       (시장 CM, 날짜 D, 시각 300000 = 다음날 06:00)
  변동률 = 끝 / 기준 - 1. 두 봉 모두 같은 종목코드(같은 월물).
근거: kis_night_probe.py 실행 #2(2026-11-03 06:54 KST) 실제 응답 — 11-02 15:30 918.90, 06:00 883.40.

직전 거래일 D: 오늘 전날부터 평일을 거꾸로 보며, 주간 봉 응답에 날짜 D의 봉이 있는 첫 날.
  (없는 날짜를 물으면 KIS가 다른 날짜 봉을 돌려줌 — probe [4b]: 11-03 요청 → 10-30 봉)
야간 봉 응답에 날짜 D의 봉이 없으면 '야간 봉 없음'으로 알림(휴장인지 자료 미제공인지는 구분하지 않음), 종료코드 0.
KIS·마스터·텔레그램 실패 → 이유 1줄 텔레그램(보낼 수 있으면) + 종료코드 1(Actions 실패 표시).
원자료 줄(마스터 1줄, 사용한 봉 2개)을 로그에 그대로 찍음. 비밀값은 찍지 않음.
시험용 환경변수: NA_TODAY=YYYYMMDD (오늘 날짜 대신).
"""
import io
import os
import sys
import json
import time
import zipfile
import datetime as dt

import requests

BASE = "https://openapi.koreainvestment.com:9443"
MASTER = "https://new.real.download.dws.co.kr/common/master/fo_idx_code_mts.mst.zip"
CHART = "/uapi/domestic-futureoption/v1/quotations/inquire-time-fuopchartprice"
TR_ID = "FHKIF03020200"
KST = dt.timezone(dt.timedelta(hours=9))
MAX_BACK = 10          # 직전 거래일을 찾을 때 확인하는 평일 수(연휴 대비)
WD = "월화수목금토일"
S = requests.Session()


class Fail(Exception):
    pass


def log(*a):
    print(*a, flush=True)


def send_telegram(text):
    """성공 True / 실패·미설정 False. 예외는 밖으로 내지 않음."""
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        log("[WARN] Telegram 환경변수 없음\n" + text)
        return False
    try:
        r = S.post(f"https://api.telegram.org/bot{token}/sendMessage", data={"chat_id": chat, "text": text}, timeout=30)
        if r.status_code != 200:
            log(f"[WARN] Telegram 실패 {r.status_code} {r.text[:200]}")
            return False
        return True
    except requests.exceptions.RequestException as e:
        log(f"[WARN] Telegram 오류 {type(e).__name__}")
        return False


def front_code():
    """마스터 fo_idx_code_mts.mst에서 상품종류 1 · 월물구분 1 · 기초 KOSPI200 행 (단축코드, 이름)."""
    try:
        r = S.get(MASTER, timeout=60)
        r.raise_for_status()
        z = zipfile.ZipFile(io.BytesIO(r.content))
        text = z.read(z.namelist()[0]).decode("cp949", errors="replace")
    except Exception as e:
        raise Fail(f"선물 마스터 받기 실패: {type(e).__name__}")
    for ln in text.splitlines():
        f = [x.strip() for x in ln.split("|")]
        if len(f) >= 9 and f[0] == "1" and f[6] == "1" and f[8] == "KOSPI200":
            log(f"[1] 마스터 원자료: {ln[:120]}")
            return f[1], f[3]
    raise Fail("마스터에서 코스피200 최근월물(상품종류 1·월물구분 1) 행을 못 찾음")


def token(key, sec):
    try:
        r = S.post(f"{BASE}/oauth2/tokenP", json={"grant_type": "client_credentials", "appkey": key, "appsecret": sec}, timeout=30)
        t = r.json().get("access_token") if r.status_code == 200 else None
    except Exception as e:
        raise Fail(f"KIS 토큰 요청 오류: {type(e).__name__}")
    if not t:
        raise Fail(f"KIS 토큰 발급 실패 HTTP {r.status_code}")
    log("[2] KIS 토큰 발급 200")
    return t


def bar(tok, key, sec, code, mkt, ds, hh):
    """날짜 ds의 봉 중 시각 ≤ hh인 가장 늦은 봉(원자료 dict). ds 봉이 없으면 None."""
    h = {"content-type": "application/json; charset=utf-8", "authorization": f"Bearer {tok}",
         "appkey": key, "appsecret": sec, "tr_id": TR_ID, "custtype": "P"}
    p = {"FID_COND_MRKT_DIV_CODE": mkt, "FID_INPUT_ISCD": code, "FID_HOUR_CLS_CODE": "60",
         "FID_PW_DATA_INCU_YN": "Y", "FID_FAKE_TICK_INCU_YN": "N", "FID_INPUT_DATE_1": ds, "FID_INPUT_HOUR_1": hh}
    last = None
    for k in range(3):
        try:
            r = S.get(f"{BASE}{CHART}", headers=h, params=p, timeout=30)
            break
        except requests.exceptions.RequestException as e:
            last = e
            time.sleep(2 + 2 * k)
    else:
        raise Fail(f"KIS 분봉 요청 오류 {mkt} {ds}: {type(last).__name__}")
    try:
        j = r.json()
    except Exception:
        raise Fail(f"KIS 분봉 응답이 JSON 아님 {mkt} {ds} HTTP {r.status_code}")
    if r.status_code != 200 or j.get("rt_cd") != "0":
        raise Fail(f"KIS 분봉 실패 {mkt} {ds} HTTP {r.status_code} rt_cd {j.get('rt_cd')} {str(j.get('msg1', ''))[:40]}")
    o2 = j.get("output2") or []
    dates = sorted({b.get("stck_bsop_date") for b in o2})
    same = [b for b in o2 if b.get("stck_bsop_date") == ds and str(b.get("stck_cntg_hour", "")) <= hh
            and _num(b.get("futs_prpr")) > 0]
    b = max(same, key=lambda x: x["stck_cntg_hour"]) if same else None
    log(f"[{mkt} {ds} {hh}] 봉 {len(o2)}개 · 응답 날짜 {dates} · 사용 봉 원자료 {json.dumps(b, ensure_ascii=False)}")
    time.sleep(0.2)
    return b


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def when(ds, hh):
    """(날짜, 봉 시각) → 'MM-DD HH:MM'. 240000 이상은 다음날(+24시간 표기)."""
    d = dt.datetime.strptime(ds, "%Y%m%d").date()
    h, m = int(hh[:2]), int(hh[2:4])
    if h >= 24:
        d, h = d + dt.timedelta(days=1), h - 24
    return f"{d:%m-%d} {h:02d}:{m:02d}"


def main():
    now = dt.datetime.now(KST)
    today = dt.datetime.strptime(os.environ["NA_TODAY"], "%Y%m%d").date() if os.environ.get("NA_TODAY") else now.date()
    log(f"실행 {now:%Y-%m-%d %H:%M} KST · 기준 오늘 {today}")
    key, sec = os.environ.get("KIS_APP_KEY"), os.environ.get("KIS_APP_SECRET")
    if not key or not sec:
        raise Fail("KIS 비밀값(KIS_APP_KEY·KIS_APP_SECRET) 없음")
    code, name = front_code()
    tok = token(key, sec)

    d, checked, base = today - dt.timedelta(days=1), 0, None
    while checked < MAX_BACK:
        if d.weekday() < 5:
            checked += 1
            ds = d.strftime("%Y%m%d")
            b = bar(tok, key, sec, code, "F", ds, "153000")
            if b:
                base = (ds, b)
                break
            log(f"    {ds} 주간 봉 없음 → 하루 전 평일 확인")
        d -= dt.timedelta(days=1)
    if base is None:
        raise Fail(f"최근 평일 {MAX_BACK}일 안에서 주간 15:30 봉을 못 찾음")
    ds, b0 = base
    n = bar(tok, key, sec, code, "CM", ds, "300000")

    title = f"🌃 밤사이 관찰 · {today:%m-%d}({WD[today.weekday()]}) {now:%H:%M} 실행"
    head = f"코스피200 야간선물 {name} ({code})"
    t0 = when(ds, b0["stck_cntg_hour"])
    p0 = _num(b0["futs_prpr"])
    if n is None:
        lines = [title, head, f"{t0} {p0:.2f} 이후 야간 봉 없음 ({ds[4:6]}-{ds[6:]} 밤 휴장 또는 자료 미제공)"]
    else:
        p1 = _num(n["futs_prpr"])
        lines = [title, head, f"{t0} {p0:.2f} → {when(ds, n['stck_cntg_hour'])} {p1:.2f}",
                 f"변동 {p1 / p0 - 1:+.2%}"]
    lines.append("관찰용 · 매매 규칙 변경 없음")
    msg = "\n".join(lines)
    log(msg)
    if not send_telegram(msg):
        raise Fail("텔레그램 전송 실패")


if __name__ == "__main__":
    try:
        main()
    except Fail as e:
        log(f"[실패] {e}")
        send_telegram(f"⚠️ 밤사이 관찰(08:30) 실패: {e}")
        sys.exit(1)
    except Exception as e:
        log(f"[실패] 예상 못 한 오류 {type(e).__name__}: {str(e)[:150]}")
        send_telegram(f"⚠️ 밤사이 관찰(08:30) 실패: 예상 못 한 오류 {type(e).__name__}")
        sys.exit(1)
