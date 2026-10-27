# new5_etf_breadth_v2_cell.py — 읽기 전용 (레포·운용 변경 없음)
# 목적: 밤사이 규칙이 반도체 ETF 하나의 우연인지, 국내주식형 ETF 전체에서 넓게 통하는지 확인.
# v2(v1이 [2]에서 50분 멈춤): KRX 요청마다 60초 제한·실패 시 재시도 2회·ETF마다 진행 표시·
#     받은 자료는 Drive new5_etf_breadth/에 저장(다시 돌리면 받은 것은 건너뜀). 대상·규칙·판정은 v1과 동일.
#
# ── 사전 고정 (결과 보기 전에 정함, 결과 보고 바꾸지 않음) ──
# 대상: 2015-01-02에 상장돼 있던 ETF 중 이름으로 국내주식형만(EXCL 단어가 이름에 있으면 제외). 포함/제외 목록 전부 출력.
#       2015~2024 정식 밤 500개 미만이면 평가 제외(표시만).
# 규칙: 종가 동시호가 매수 → 다음 거래일 시가 동시호가 매도, 다음 거래일이 월요일이면 보유 안 함. 비용 왕복 0.006%.
# 평가 구간: 2015~2024(판정). 2025~2026은 참고만.
# 판정 "넓게 통한다" = ① 평가 대상 중 하루 평균 순수익 > 0 인 ETF 비율 ≥ 80%
#                      ∧ ② 평가 대상 전체 동일가중(매일 밤 평균) CAGR > 0 ∧ 플러스 연도 ≥ 7/10
# 한계: 2015 이후 상장폐지돼 이름 조회가 안 되는 ETF는 분류 불가로 제외(개수 출력).
import os, sys, time, json, socket, subprocess
socket.setdefaulttimeout(60)          # KRX가 응답 없으면 60초 뒤 오류로 끊음(무한 대기 방지)
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "pykrx"], check=True)
from google.colab import userdata, drive
os.environ["KRX_ID"] = userdata.get("KRX_ID")
os.environ["KRX_PW"] = userdata.get("KRX_PW")
import numpy as np, pandas as pd
from pykrx import stock
if not os.path.exists("/content/drive/MyDrive"):
    drive.mount("/content/drive")
OUT = "/content/drive/MyDrive/new5_etf_breadth"
os.makedirs(OUT, exist_ok=True)
T0 = time.time()
el = lambda: f"{time.time() - T0:5.0f}초"

def call(f, *a, tries=3):
    for i in range(tries):
        try:
            return f(*a)
        except Exception as ex:
            if i == tries - 1:
                raise
            print(f"    {el()} 재시도 {i + 1}/{tries - 1} ← {type(ex).__name__}: {str(ex)[:80]}", flush=True)
            time.sleep(5)

SNAP, S, E = "20150102", "20150101", "20261026"
COST = 0.00006
EXCL = ["레버리지", "인버스", "2X", "선물", "채권", "국고", "국채", "통안", "회사채", "크레딧", "단기", "금리", "머니",
        "MMF", "CD", "달러", "엔화", "유로", "위안", "골드", "금은", "원유", "WTI", "구리", "농산물", "원자재", "커머디티",
        "미국", "중국", "차이나", "일본", "유럽", "글로벌", "선진", "신흥", "이머징", "베트남", "인도", "브라질", "러시아",
        "라틴", "나스닥", "S&P", "다우", "항셍", "니케이", "합성", "커버드콜", "혼합", "TDF", "대만", "아시아", "호주", "독일", "유동", "물가", "KTB", "블룸버그", "리츠"]


# ---------- 1. 대상 목록 ----------
NJ = f"{OUT}/names_{SNAP}.json"
if os.path.exists(NJ):
    names = json.load(open(NJ, encoding="utf-8"))
    tick = list(names)
    print(f"{el()} [0] 이름 목록: Drive 저장본 {len(tick)}개", flush=True)
else:
    tick = call(stock.get_etf_ticker_list, SNAP)
    print(f"{el()} [0] {SNAP} ETF 목록 {len(tick)}개", flush=True)
    names = {}
    for i, t in enumerate(tick, 1):
        try:
            names[t] = stock.get_etf_ticker_name(t) or ""   # 폐지 ETF는 오류 → 이름 없음(재시도 안 함)
        except Exception:
            names[t] = ""
        if i % 20 == 0:
            print(f"{el()}     이름 조회 {i}/{len(tick)}", flush=True)
    json.dump(names, open(NJ, "w", encoding="utf-8"), ensure_ascii=False)
noname = [t for t in tick if not names[t]]
inc = [t for t in tick if names[t] and not any(k.upper() in names[t].upper() for k in EXCL)]
exc = [t for t in tick if names[t] and t not in inc]
print(f"[1] {SNAP} 상장 ETF {len(tick)}개 → 국내주식형 {len(inc)} · 제외 {len(exc)} · 이름 조회 불가(폐지 추정) {len(noname)}")
print("  포함:", ", ".join(f"{t} {names[t]}" for t in inc))
print("  제외:", ", ".join(f"{t} {names[t]}" for t in exc))
if noname:
    print("  이름 불가:", ", ".join(noname))

# ---------- 2. 자료 ----------
px = {}
for i, t in enumerate(inc, 1):
    fp = f"{OUT}/{t}.parquet"
    if os.path.exists(fp):
        d, src = pd.read_parquet(fp), "저장본"
    else:
        try:
            d, src = call(stock.get_etf_ohlcv_by_date, S, E, t), "KRX"
        except Exception as ex:
            print(f"{el()} [{i}/{len(inc)}] {t} {names[t]}: 자료 실패 → 제외 ({type(ex).__name__})", flush=True)
            continue
        if len(d):
            d.to_parquet(fp)
        time.sleep(0.2)
    if len(d):
        px[t] = d
    print(f"{el()} [{i}/{len(inc)}] {t} {names[t]}: {len(d)}일 ({src})", flush=True)
print(f"[2] 자료 받음 {len(px)}/{len(inc)}")

# ---------- 3. 정규장 값 대조(최근 5거래일, 받은 전 종목) ----------
days5 = px[max(px, key=lambda t: len(px[t]))].index[-5:]
bad, nchk = [], 0
for dd in days5:
    b = call(stock.get_etf_ohlcv_by_ticker, dd.strftime("%Y%m%d"))
    for t, d in px.items():
        if dd in d.index and t in b.index:
            nchk += 1
            for c in ("시가", "종가"):
                if float(d.at[dd, c]) != float(b.at[t, c]):
                    bad.append(f"{dd.date()} {t} {c} {d.at[dd, c]} vs {b.at[t, c]}")
print(f"[3] 정규장 대조 {nchk}건:", "대조 0건 → 확인 안 됨, 결과 쓰지 말 것" if nchk == 0 else "시가·종가 모두 일치" if not bad else f"불일치 {len(bad)}건 → 결과 쓰지 말 것\n    " + "\n    ".join(bad[:20]))

# ---------- 4. 계산 ----------
def nights(d):
    d = d[d["종가"] > 0]
    on = d["시가"].shift(-1)
    r = (on / d["종가"] - 1).where(on > 0)
    nxt = pd.Series(d.index, index=d.index).shift(-1)
    x = pd.DataFrame({"r": r, "nxt": nxt}).dropna()
    x = x[x.r.abs() < 0.30]
    x = x[pd.to_datetime(x.nxt).dt.weekday != 0]          # 정식 밤만
    return x.r - COST                                      # 순수익

def stats(n):
    span = (n.index[-1] - n.index[0]).days / 365.25
    eq = (1 + n).cumprod()
    yrs = n.groupby(n.index.year).apply(lambda s: (1 + s).prod() - 1)
    return dict(밤=len(n), 평균=n.mean(), CAGR=eq.iloc[-1] ** (1 / span) - 1,
                Sharpe=n.mean() / n.std() * np.sqrt(len(n) / span),
                MDD=(eq / eq.cummax() - 1).min(), 플러스=int((yrs > 0).sum()), 연수=len(yrs))

rows, nets = [], {}
for t, d in px.items():
    n = nights(d)
    a = n[(n.index.year >= 2015) & (n.index.year <= 2024)]
    b = n[n.index.year >= 2025]
    if len(a) < 500:
        print(f"  평가 제외(정식 밤 {len(a)}개) {t} {names[t]}")
        continue
    nets[t] = a
    sa = stats(a)
    sb = stats(b) if len(b) > 20 else None
    rows.append([t, names[t], sa["밤"], sa["평균"] * 100, sa["CAGR"] * 100, sa["Sharpe"], sa["MDD"] * 100,
                 f"{sa['플러스']}/{sa['연수']}", (sb["CAGR"] * 100) if sb else np.nan])
T = pd.DataFrame(rows, columns=["코드", "이름", "밤", "하루평균%", "CAGR%", "Sharpe", "MDD%", "플러스연도", "25~26 CAGR%"])
pd.set_option("display.width", 250); pd.set_option("display.max_rows", 500)
print(f"\n[4] ETF별 2015~2024 (비용 0.006%) — {len(T)}개, CAGR 순")
print(T.sort_values("CAGR%", ascending=False).round(3).to_string(index=False))

W = pd.DataFrame(nets).mean(axis=1).dropna()
sw = stats(W)
share = (T["하루평균%"] > 0).mean()
print(f"\n[5] 동일가중(매일 밤 {len(T)}개 평균) 2015~2024: CAGR {sw['CAGR'] * 100:+.1f}% · Sharpe {sw['Sharpe']:.2f} · "
      f"MDD {sw['MDD'] * 100:.1f}% · 플러스 연도 {sw['플러스']}/{sw['연수']} · 밤 {sw['밤']}")
print(f"    하루 평균 순수익 > 0 인 ETF: {int((T['하루평균%'] > 0).sum())}/{len(T)} = {share:.0%}")
ok = share >= 0.80 and sw["CAGR"] > 0 and sw["플러스"] >= 7
print(f"\n[판정] ① 플러스 비율 ≥80%: {'통과' if share >= 0.80 else '미달'} · ② 동일가중 CAGR>0·플러스 연도≥7/10: "
      f"{'통과' if (sw['CAGR'] > 0 and sw['플러스'] >= 7) else '미달'} → {'넓게 통한다' if ok else '넓게 통한다고 볼 수 없음'}")
print("끝. 출력 전체를 붙여 주세요.")
