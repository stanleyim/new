# [Colab 셀] 바이낸스 한국 주식 무기한 선물 — 공개 자료 확인 + 밤사이 가격 비교. 읽기 전용, 계정·키 불필요, 거래 없음.
# 1) 바이낸스 선물 공개 API(fapi) 접속 가능 여부 → 한국 관련 종목 이름·상장일 찾기
#    (Colab 서버가 미국이면 바이낸스 API가 막힐 수 있음 → 공개 자료실 data.binance.vision으로 대신 시도)
# 2) 1분봉으로 매수일 15:30(KST) 가격 → 다음 거래일 08:59 가격 변화(해외 밤사이) vs KRX 종가 → 다음날 시가 변화(국내 밤사이)
#    주의: 선물 가격은 USDT(달러) 기준 → 원·달러 환율 변화가 섞임(이 셀은 보정 안 함, 결과 해석 때 감안)
import io, os, sys, json, zipfile, subprocess, datetime as dt
import numpy as np, pandas as pd, requests

KEYS = ("SAMSUNG", "HYNIX", "SKHY", "HYUNDAI", "EWY", "KOREA", "KOSPI")
MAP = {"005930": ["SAMSUNGUSDT"], "000660": ["SKHYNIXUSDT", "SKHYUSDT"], "005380": ["HYUNDAIUSDT"]}
KST = dt.timezone(dt.timedelta(hours=9))
S = requests.Session()

def get(url, **kw):
    try:
        r = S.get(url, timeout=30, **kw)
        return r.status_code, r
    except Exception as e:
        return f"오류 {type(e).__name__}", None

# ---------- 1. 종목 찾기 ----------
st, r = get("https://fapi.binance.com/fapi/v1/exchangeInfo")
API_OK = st == 200
print(f"[1] 바이낸스 선물 API: {st}" + ("" if API_OK else f" · 응답 {r.text[:150] if r is not None else ''}"))
found = {}
if API_OK:
    for s in r.json().get("symbols", []):
        if any(k in s["symbol"] for k in KEYS):
            found[s["symbol"]] = dict(status=s.get("status"), type=s.get("contractType"),
                                      onboard=dt.datetime.fromtimestamp(s.get("onboardDate", 0) / 1000, KST).strftime("%Y-%m-%d"))
    print("    한국 관련 종목:", found if found else "없음")
# 공개 자료실 확인(1분봉 일별 파일 목록)
VIS = {}
for tk, syms in MAP.items():
    for sym in syms:
        st2, r2 = get("https://s3-ap-northeast-1.amazonaws.com/data.binance.vision",
                      params={"prefix": f"data/futures/um/daily/klines/{sym}/1m/", "delimiter": "/"})
        days = []
        if st2 == 200:
            import re
            days = sorted(set(re.findall(rf"{sym}-1m-(\d{{4}}-\d{{2}}-\d{{2}})\.zip<", r2.text)))
        VIS[sym] = days
        print(f"    자료실 {sym}: 응답 {st2} · 1분봉 일별 파일 {len(days)}개" + (f" ({days[0]}~{days[-1]})" if days else ""))
print("    (자료실 목록은 한 번에 1,000개까지만 보임 — 기간 확인용)")

def klines_api(sym, start_ms, end_ms):
    out, t = [], start_ms
    while t < end_ms:
        st, r = get("https://fapi.binance.com/fapi/v1/klines",
                    params={"symbol": sym, "interval": "1m", "startTime": t, "endTime": end_ms, "limit": 1500})
        if st != 200 or not r.json():
            break
        k = r.json()
        out += k
        t = k[-1][0] + 60_000
    return out

def klines_vis(sym, day):
    st, r = get(f"https://data.binance.vision/data/futures/um/daily/klines/{sym}/1m/{sym}-1m-{day}.zip")
    if st != 200:
        return []
    z = zipfile.ZipFile(io.BytesIO(r.content))
    df = pd.read_csv(z.open(z.namelist()[0]), header=None)
    if not str(df.iloc[0, 0]).isdigit():
        df = df.iloc[1:]
    return df.iloc[:, :6].astype(float).values.tolist()

# ---------- 2. 밤사이 비교 ----------
if not os.path.isdir("/content/main/overnight_paper"):
    subprocess.run("git clone -q --depth 1 -b main https://github.com/stanleyim/new.git /content/main", shell=True, check=True)
k = pd.read_parquet("/content/main/overnight_paper/krx_daily.parquet")
k["ticker"] = k["ticker"].astype(str).str.zfill(6)
k["date"] = k["date"].astype(str)
cal = sorted(k["date"].unique())
res = []
for tk, syms in MAP.items():
    sym = next((s for s in syms if s in found or VIS.get(s)), None)
    if not sym:
        print(f"\n[2] {tk}: 바이낸스 종목 못 찾음 → 건너뜀")
        continue
    on = dt.datetime.strptime(found[sym]["onboard"], "%Y-%m-%d").strftime("%Y%m%d") if sym in found else VIS[sym][0].replace("-", "")
    days = [d for d in cal if d >= on]
    if len(days) < 3:
        print(f"\n[2] {tk} {sym}: 겹치는 KRX 거래일 부족")
        continue
    t0 = dt.datetime.strptime(days[0], "%Y%m%d").replace(tzinfo=KST)
    t1 = dt.datetime.strptime(days[-1], "%Y%m%d").replace(tzinfo=KST) + dt.timedelta(days=1)
    if API_OK:
        kl = klines_api(sym, int(t0.timestamp() * 1000), int(t1.timestamp() * 1000))
    else:
        kl = []
        for d in pd.date_range(t0.date() - dt.timedelta(days=1), t1.date()):
            kl += klines_vis(sym, d.strftime("%Y-%m-%d"))
    if not kl:
        print(f"\n[2] {tk} {sym}: 1분봉을 못 받음")
        continue
    m = pd.DataFrame([[x[0], float(x[1]), float(x[4])] for x in kl], columns=["t", "o", "c"]).drop_duplicates("t")
    m["kst"] = pd.to_datetime(m.t, unit="ms", utc=True).dt.tz_convert("Asia/Seoul").dt.strftime("%Y%m%d%H%M")
    px = m.set_index("kst")
    kk = k[k.ticker == tk].set_index("date")
    for i, d in enumerate(days[:-1]):
        n = days[i + 1]
        a, b = px.o.get(d + "1530"), px.c.get(n + "0859")
        if a is None or b is None or not (kk.at[d, "종가"] > 0 and kk.at[n, "시가"] > 0):
            continue
        res.append(dict(tk=tk, sym=sym, d=d, mon=pd.Timestamp(n).weekday() == 0,
                        perp=b / a - 1, krx=kk.at[n, "시가"] / kk.at[d, "종가"] - 1))
    print(f"\n[2] {tk} {sym}: 1분봉 {len(m):,}개 · 비교 가능한 밤 {sum(1 for x in res if x['tk'] == tk)}일")
if res:
    x = pd.DataFrame(res)
    print("\n[결과] 해외 밤사이(15:30→다음날 08:59, USDT 기준) vs KRX 밤사이(종가→다음날 시가)")
    for tk, g in x.groupby("tk"):
        c = np.corrcoef(g.perp, g.krx)[0, 1]
        hit = (np.sign(g.perp) == np.sign(g.krx)).mean()
        print(f"  {tk}: {len(g)}일 · 상관 {c:.2f} · 방향 일치 {hit:.0%} · 평균 해외 {g.perp.mean():+.3%} · KRX {g.krx.mean():+.3%}"
              f" · 08:59 이후 추가 변화(KRX−해외) 평균 {(g.krx - g.perp).mean():+.3%}")
print("\n끝. 출력 전체를 붙여 주세요.")
