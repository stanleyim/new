# [Colab 셀] NXT 과거 분봉 실측 — KIS 주식일별분봉조회(FHKST03010230), 시장 코드 NX(NXT) vs J(KRX). 읽기만, 레포 무관.
# 확인: ① NX로 과거 날짜 분봉이 나오는가 ② 프리(08:00~08:50)·애프터(15:40~20:00) 봉이 있는가 ③ 몇 시까지 거슬러 받는가
#       ④ NX 프리마켓 마지막 가격 vs KRX 09:00 시가 (같은 날)
# 대상: 005930·000660, 10-22·10-23 (호출 약 50회, 초당 2회 이하)
import os, time, getpass, datetime as dt
import requests, pandas as pd

for k in ("KIS_APP_KEY", "KIS_APP_SECRET"):    # 1순위 Colab 보안 비밀, 없으면 입력창(값은 화면·파일에 안 남음)
    if not os.environ.get(k):
        try:
            from google.colab import userdata
            os.environ[k] = userdata.get(k)
        except Exception as e:
            print(f"{k} 보안 비밀 없음({type(e).__name__}) → 입력창에 직접 입력")
            os.environ[k] = getpass.getpass(f"{k}: ").strip()
BASE = "https://openapi.koreainvestment.com:9443"
r = requests.post(f"{BASE}/oauth2/tokenP", json={"grant_type": "client_credentials",
                  "appkey": os.environ["KIS_APP_KEY"], "appsecret": os.environ["KIS_APP_SECRET"]}, timeout=30)
if r.status_code != 200 or "access_token" not in r.json():
    raise SystemExit(f"KIS 토큰 실패 {r.status_code} {r.text[:200]}")
H = {"content-type": "application/json", "authorization": f"Bearer {r.json()['access_token']}",
     "appkey": os.environ["KIS_APP_KEY"], "appsecret": os.environ["KIS_APP_SECRET"], "tr_id": "FHKST03010230"}
print("KIS 토큰 OK")

def day_bars(mkt, tk, ds):
    """하루 1분봉을 20:00부터 거꾸로 페이지 조회. 반환 (DataFrame, 호출 수, 마지막 응답 메시지)."""
    bars, hour, n, msg = {}, "200000", 0, ""
    for _ in range(15):
        time.sleep(0.5)
        d = requests.get(f"{BASE}/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice", headers=H, timeout=30,
                         params={"FID_COND_MRKT_DIV_CODE": mkt, "FID_INPUT_ISCD": tk, "FID_INPUT_HOUR_1": hour,
                                 "FID_INPUT_DATE_1": ds, "FID_PW_DATA_INCU_YN": "N", "FID_FAKE_TICK_INCU_YN": "N"}).json()
        n += 1
        msg = f"{d.get('rt_cd')} {d.get('msg_cd')} {str(d.get('msg1', '')).strip()[:40]}"
        same = [x for x in (d.get("output2") or []) if x.get("stck_bsop_date") == ds and x.get("stck_cntg_hour")]
        new = [x for x in same if x["stck_cntg_hour"] not in bars]
        for x in new:
            bars[x["stck_cntg_hour"]] = x
        if not new:
            break
        earliest = min(x["stck_cntg_hour"] for x in same)
        if earliest <= "080000":
            break
        hour = earliest
    if not bars:
        return pd.DataFrame(), n, msg
    df = pd.DataFrame(bars.values())
    out = pd.DataFrame({"t": df["stck_cntg_hour"].astype(str), "o": pd.to_numeric(df["stck_oprc"], errors="coerce"),
                        "c": pd.to_numeric(df["stck_prpr"], errors="coerce"), "v": pd.to_numeric(df["cntg_vol"], errors="coerce")})
    return out.sort_values("t").reset_index(drop=True), n, msg

def seg(df, a, b):
    s = df[(df.t >= a) & (df.t <= b)]
    return f"{len(s)}봉 거래량 {int(s.v.sum()):,}" + (f" 첫가 {s.o.iloc[0]:,.0f} 끝가 {s.c.iloc[-1]:,.0f}" if len(s) else "")

for tk in ("005930", "000660"):
    for ds in ("20261022", "20261023"):
        res = {}
        for mkt in ("NX", "J"):
            df, n, msg = day_bars(mkt, tk, ds)
            res[mkt] = df
            print(f"\n{tk} {ds} [{mkt}] 호출 {n}회 · 응답 {msg} · 총 {len(df)}봉"
                  + (f" · 시간 {df.t.iloc[0]}~{df.t.iloc[-1]}" if len(df) else ""))
            if len(df):
                print("   프리 08:00~08:59 :", seg(df, "080000", "085959"))
                print("   정규 09:00~15:30 :", seg(df, "090000", "153059"))
                print("   애프터 15:31~20:00:", seg(df, "153100", "200000"))
        nx, j = res["NX"], res["J"]
        pre = nx[(nx.t >= "080000") & (nx.t <= "085959")] if len(nx) else nx
        if len(pre) and len(j):
            jo = j[j.t >= "090000"].o.iloc[0]
            print(f"   ▶ NX 프리마켓 끝가 {pre.c.iloc[-1]:,.0f} vs KRX 09:00 시가 {jo:,.0f} ({pre.c.iloc[-1] / jo - 1:+.3%})")
print("\n끝. 출력 전체를 붙여 주세요.")
