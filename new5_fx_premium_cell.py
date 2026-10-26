# [Colab 셀] 바이낸스 한국 주식 선물 vs KRX — 밤사이 차이를 환율·프리미엄으로 나누기. 읽기 전용, 계정·키 불필요, 레포·운용 변경 없음.
# 시각(KST): 매수일 15:30(KRX 종가) → 다음 거래일 09:00(KRX 시가) → 그날 15:30(KRX 종가)
# 비율 R = 선물가(USDT) × 원·달러 환율 ÷ KRX 가격.  밤사이 초과(해외−KRX, USDT 기준) = ΔR(프리미엄 변화) − Δ환율
# 판정(결과 보기 전 고정)
#  A 환율로 설명됨: 환율을 빼고 남은 밤사이 프리미엄 변화 평균이 ±2 표준오차 안
#  B 낮에 되돌아감: 밤사이 프리미엄 변화와 그날 낮 프리미엄 변화의 관계(상관·평균)
#  C 실제 미반영 몫: 환율 뺀 밤사이 초과가 큰 날(위 절반) vs 작은 날(아래 절반)의 다음날 KRX 시가→종가 차이
#     있음 = 3종목 모두 전체 차이 > +2표준오차 AND 앞·뒤 절반 모두 플러스 / 반대 = 모두 마이너스로 같은 조건 / 그 외 = 모름(검출 가능한 최소 크기 함께 출력)
# 환율: yfinance KRW=X 1시간봉(전 기간) → 시각 사이 직선 보간. 최근 59일은 5분봉으로 보간 오차를 직접 잼. USDT는 1달러로 취급(차이 미보정).
import io, os, re, sys, zipfile, subprocess, datetime as dt
import numpy as np, pandas as pd, requests

MAP = {"005930": "SAMSUNGUSDT", "000660": "SKHYNIXUSDT", "005380": "HYUNDAIUSDT"}
OUT = "/content/new5_fx"
S = requests.Session()

def get(url, **kw):
    try:
        r = S.get(url, timeout=30, **kw)
        return r.status_code, r
    except Exception as e:
        return f"오류 {type(e).__name__}", None

def load_krx():
    if not os.path.isdir("/content/main/overnight_paper"):
        subprocess.run("git clone -q --depth 1 -b main https://github.com/stanleyim/new.git /content/main", shell=True, check=True)
    k = pd.read_parquet("/content/main/overnight_paper/krx_daily.parquet")
    k["ticker"] = k["ticker"].astype(str).str.zfill(6)
    k["date"] = k["date"].astype(str).str.replace("-", "").str[:8]
    return k

def vis_days(sym):
    st, r = get("https://s3-ap-northeast-1.amazonaws.com/data.binance.vision",
                params={"prefix": f"data/futures/um/daily/klines/{sym}/1m/", "delimiter": "/"})
    return sorted(set(re.findall(rf"{sym}-1m-(\d{{4}}-\d{{2}}-\d{{2}})\.zip<", r.text))) if st == 200 else []

def load_perp(sym, days):
    rows = []
    for d in days:
        st, r = get(f"https://data.binance.vision/data/futures/um/daily/klines/{sym}/1m/{sym}-1m-{d}.zip")
        if st != 200:
            continue
        z = zipfile.ZipFile(io.BytesIO(r.content))
        df = pd.read_csv(z.open(z.namelist()[0]), header=None)
        if not str(df.iloc[0, 0]).isdigit():
            df = df.iloc[1:]
        rows.append(df.iloc[:, :2].astype(float))
    m = pd.concat(rows).drop_duplicates(0)
    m.index = pd.to_datetime(m[0], unit="ms", utc=True).dt.tz_convert("Asia/Seoul").dt.strftime("%Y%m%d%H%M")
    return m[1]                                   # 1분봉 시가(그 분 00초 가격)

def load_fx(interval, **kw):
    try:
        import yfinance as yf
    except ImportError:
        subprocess.run("pip -q install yfinance", shell=True, check=True)
        import yfinance as yf
    h = yf.Ticker("KRW=X").history(interval=interval, auto_adjust=False, **kw)
    if h.empty:
        return None
    if h.index.tz is None:
        print(f"    주의: 환율 {interval} 시각대 정보 없음 → UTC로 간주")
        h.index = h.index.tz_localize("UTC")
    return h[["Open", "Close"]].dropna()

def fx_points(h, minutes):
    """봉 시작 = Open, 봉 끝 = Close 점으로 바꿈(같은 시각이면 다음 봉 Open 우선)."""
    a = pd.Series(h["Open"].values, index=h.index)
    b = pd.Series(h["Close"].values, index=h.index + pd.Timedelta(minutes=minutes))
    p = pd.concat([a, b])
    p = p[~p.index.duplicated(keep="first")].sort_index()
    p.index = p.index.tz_convert("Asia/Seoul")
    return p

def fx_at(p, t, max_gap):
    """t(KST Timestamp)에서 앞뒤 점 직선 보간. 앞뒤 간격이 max_gap(분) 넘으면 None."""
    if p is None or len(p) == 0:
        return None
    z = pd.Timestamp(0, tz="UTC")
    x = ((p.index - z) / pd.Timedelta(seconds=1)).values      # 초 단위(시각 해상도 차이 영향 없음)
    v = p.values
    tt = (t - z) / pd.Timedelta(seconds=1)
    i = np.searchsorted(x, tt)
    if i < len(x) and x[i] == tt:
        return float(v[i])
    if i == 0 or i == len(x):
        return None
    if (x[i] - x[i - 1]) > max_gap * 60:
        return None
    w = (tt - x[i - 1]) / (x[i] - x[i - 1])
    return float(v[i - 1] + w * (v[i] - v[i - 1]))

def kst(day, hhmm):
    return pd.Timestamp(f"{day} {hhmm[:2]}:{hhmm[2:]}", tz="Asia/Seoul")

def build(k, perp, fx60, fx5):
    """종목별 밤사이·낮 행 만들기."""
    res = []
    cal = sorted(k["date"].unique())
    for tk, px in perp.items():
        kk = k[k.ticker == tk].set_index("date")
        for d, n in zip(cal[:-1], cal[1:]):
            if d not in kk.index or n not in kk.index:
                continue
            C, O, Cn = kk.at[d, "종가"], kk.at[n, "시가"], kk.at[n, "종가"]
            pc, po, pcn = px.get(d + "1530"), px.get(n + "0900"), px.get(n + "1530")
            t = [kst(d, "1530"), kst(n, "0900"), kst(n, "1530")]
            f = [fx_at(fx60, x, 120) for x in t]
            if None in (pc, po, pcn) or None in f or not (C > 0 and O > 0 and Cn > 0):
                continue
            f5 = [fx_at(fx5, x, 10) for x in t]
            lk = [np.log(pc / C), np.log(po / O), np.log(pcn / Cn)]
            lf = np.log(f)
            res.append(dict(tk=tk, d=d, n=n, mon=pd.Timestamp(n).weekday() == 0,
                            R=np.exp(lk[0] + lf[0]),
                            ex_night=lk[1] - lk[0],                       # 해외−KRX 밤사이(USDT 기준)
                            fx_night=lf[1] - lf[0],                       # 환율 변화(+ = 원화 약세)
                            prem_night=(lk[1] + lf[1]) - (lk[0] + lf[0]), # 환율 뺀 프리미엄 변화
                            prem_day=(lk[2] + lf[2]) - (lk[1] + lf[1]),
                            krx_day=np.log(Cn / O),
                            fx_night5=(np.log(f5[1] / f5[0]) if None not in f5[:2] else np.nan)))
    return pd.DataFrame(res)

def se(x):
    x = np.asarray(x, float)
    return x.std(ddof=1) / np.sqrt(len(x)) if len(x) > 1 else np.nan

def split_diff(g, col="prem_night"):
    hi = g[col] > g[col].median()
    a, b = g.loc[hi, "krx_day"], g.loc[~hi, "krx_day"]
    return a.mean() - b.mean(), np.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))

def report(x):
    P = lambda v: f"{v * 100:+.3f}%"
    print(f"\n[결과] 종목별 (로그 수익, 하루 평균)")
    verdC = []
    for tk, g in x.groupby("tk"):
        g = g.sort_values("d")
        n = len(g)
        print(f"\n■ {tk} {MAP[tk]} · {n}일({g.d.iloc[0]}~{g.n.iloc[-1]}) · 월요일 넘김 {int(g.mon.sum())}일")
        print(f"  비율 R(15:30) 중앙 {g.R.median():.4f} · 일별 흔들림(표준편차) {g.R.std():.4f} · 기간 첫→끝 {g.R.iloc[0]:.4f}→{g.R.iloc[-1]:.4f}")
        ex, fxp, pr = g.ex_night.mean(), -g.fx_night.mean(), g.prem_night.mean()
        print(f"  밤사이 초과(해외−KRX) {P(ex)} = 환율 몫 {P(fxp)} + 프리미엄 몫 {P(pr)}  (프리미엄 몫 표준오차 {P(se(g.prem_night))})")
        A = "설명됨" if abs(pr) < 2 * se(g.prem_night) else "남음"
        print(f"  A 환율로 설명: {A}  ·  월요일 넘김 초과 {P(g[g.mon].ex_night.mean()) if g.mon.any() else '-'} / 그 외 {P(g[~g.mon].ex_night.mean())}")
        c = np.corrcoef(g.prem_night, g.prem_day)[0, 1]
        print(f"  B 낮 되돌림: 밤 프리미엄 {P(pr)} · 낮 프리미엄 {P(g.prem_day.mean())} (표준오차 {P(se(g.prem_day))}) · 밤↔낮 상관 {c:+.2f}")
        dif, s = split_diff(g)
        h = n // 2
        d1, _ = split_diff(g.iloc[:h]); d2, _ = split_diff(g.iloc[h:])
        dex, sex = split_diff(g, "ex_night")
        print(f"  C 미반영: 환율 뺀 초과 위 절반 − 아래 절반의 다음날 KRX 시가→종가 {P(dif)} (표준오차 {P(s)}, 검출 최소 ±{P(2 * s)[1:]}) · 앞 절반 {P(d1)} · 뒤 절반 {P(d2)}")
        print(f"     참고(환율 안 뺀 초과로 나눔) {P(dex)} (표준오차 {P(sex)})")
        verdC.append((dif > 2 * s and d1 > 0 and d2 > 0, dif < -2 * s and d1 < 0 and d2 < 0, 2 * s))
        v5 = g.dropna(subset=["fx_night5"])
        if len(v5):
            e = (v5.fx_night - v5.fx_night5).abs()
            print(f"  환율 보간 오차(1시간봉 vs 5분봉, {len(v5)}일): 중앙 {e.median() * 100:.3f}%p · 최대 {e.max() * 100:.3f}%p")
    C = "있음" if all(a for a, _, _ in verdC) else "반대" if all(b for _, b, _ in verdC) else "모름"
    print(f"\n[판정 C] 실제 미반영 몫: {C}" + (f" (이 표본으로 검출 가능한 최소 크기 약 ±{max(m for *_, m in verdC) * 100:.2f}%p)" if C == "모름" else ""))

if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    k = load_krx()
    print(f"[0] krx_daily {k.date.min()}~{k.date.max()}")
    perp = {}
    for tk, sym in MAP.items():
        days = vis_days(sym)
        if not days:
            print(f"[1] {sym}: 자료실 목록 못 받음 → 건너뜀"); continue
        perp[tk] = load_perp(sym, days)
        print(f"[1] {sym}: 일별 파일 {len(days)}개({days[0]}~{days[-1]}) · 1분봉 {len(perp[tk]):,}개")
    if not perp:
        print("[1] 선물 자료 없음 → 중단"); sys.exit()
    first = min(v.index.min() for v in perp.values())[:8]
    st = (pd.Timestamp(first) - pd.Timedelta(days=3)).strftime("%Y-%m-%d")
    h60 = load_fx("60m", start=st, end=(pd.Timestamp.today() + pd.Timedelta(days=1)).strftime("%Y-%m-%d"))
    h5 = load_fx("5m", period="59d")
    if h60 is None:
        print("[2] 환율 1시간봉을 못 받음 → 중단(출력 붙여 주세요)"); sys.exit()
    print(f"[2] 환율 KRW=X 1시간봉 {len(h60):,}개({h60.index.min()}~{h60.index.max()}) · 값 범위 {h60.Close.min():,.1f}~{h60.Close.max():,.1f}")
    print(f"    5분봉 {0 if h5 is None else len(h5):,}개" + ("" if h5 is None else f"({h5.index.min()}~{h5.index.max()})"))
    x = build(k, perp, fx_points(h60, 60), None if h5 is None else fx_points(h5, 5))
    if x.empty:
        print("[3] 비교 가능한 날 없음 → 중단"); sys.exit()
    x.to_csv(f"{OUT}/fx_premium_rows.csv", index=False)
    if os.path.isdir("/content/drive/MyDrive"):
        os.makedirs("/content/drive/MyDrive/new5_fx", exist_ok=True)
        x.to_csv("/content/drive/MyDrive/new5_fx/fx_premium_rows.csv", index=False)
    report(x)
    print("\n끝. 출력 전체를 붙여 주세요.")
