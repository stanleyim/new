# new5_lev_overnight_cell.py — 읽기 전용 (레포·운용 변경 없음)
# 목적: 레버리지 ETF(코스피200 하루 2배)로 밤사이 흐름을 2배로 먹을 수 있는지, ETF 자기 가격으로 실측.
# 매매 가정: 종가 동시호가 매수 → 다음 거래일 시가 동시호가 매도. 비용 왕복 0.006%(수수료, 거래세 없음).
# 세금: 레버리지는 '기타 ETF' — 매도 때 MIN(실제 차익, 과표기준가 상승분) × 15.4%.
#       국내 레버리지는 과표 상승분이 거의 0이라 실제 세금 ≈ 0. pykrx에 과표기준가가 없어 둘 다 보고:
#       [세금0] 실제에 가까움 / [최악] 이긴 밤마다 순차익의 15.4%(손실 상계 없음).
# 판정(사전 고정, 2015~2024, 정식 밤 = 다음 거래일 월요일 제외):
#       CAGR > KODEX 200 밤사이 CAGR ∧ 플러스 연도 ≥ 7/10. MDD는 보고만.
# 점검: ① 종목 이름 ② 정규장 값 대조(최근 5일) ③ 하루·밤 수익이 KODEX 200의 약 2배인지(기울기).
# 저장: Drive /MyDrive/new5_lev/ (일봉 parquet, 다음 검증에서 재사용)
import os, sys, subprocess
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "pykrx"], check=True)
from google.colab import userdata
os.environ["KRX_ID"] = userdata.get("KRX_ID")
os.environ["KRX_PW"] = userdata.get("KRX_PW")
import numpy as np, pandas as pd
from pykrx import stock

BASE = "069500"
ETF = {"069500": "KODEX 200", "122630": "KODEX 레버리지", "123320": "TIGER 레버리지"}
S, E = "20150101", "20261027"
COST, TAX = 0.00006, 0.154
OUT = "/content/drive/MyDrive/new5_lev"

# ---------- 0. 이름 확인 ----------
name_ok = True
for t, nm in ETF.items():
    real = str(stock.get_etf_ticker_name(t))
    ok = nm.replace(" ", "") == real.replace(" ", "")
    name_ok &= ok
    print(f"[0] {t} 기대 '{nm}' · KRX 이름 '{real}'" + ("" if ok else "  ← 다름"))
if not name_ok:
    print("[0] 이름 불일치 → 아래 결과 쓰지 말 것")

# ---------- 1. 자료 ----------
px = {}
for t, nm in ETF.items():
    d = stock.get_etf_ohlcv_by_date(S, E, t)
    px[t] = d
    print(f"[1] {t} {nm}: {len(d)}일 {d.index.min().date()}~{d.index.max().date()}")
try:
    if not os.path.exists("/content/drive/MyDrive"):
        from google.colab import drive
        drive.mount("/content/drive")
    os.makedirs(OUT, exist_ok=True)
    for t, d in px.items():
        d.to_parquet(f"{OUT}/{t}.parquet")
    print(f"[1] 저장 {OUT}/ ({len(px)}개)")
except Exception as e:
    print(f"[1] Drive 저장 실패(계산은 계속): {type(e).__name__} {str(e)[:120]}")

# ---------- 2. 정규장 값 대조 ----------
bad = []
for dd in px[BASE].index[-5:]:
    b = stock.get_etf_ohlcv_by_ticker(dd.strftime("%Y%m%d"))
    for t in ETF:
        if dd not in px[t].index or t not in b.index:
            bad.append(f"{dd.date()} {t} 없음")
            continue
        for c in ("시가", "종가"):
            if float(px[t].at[dd, c]) != float(b.at[t, c]):
                bad.append(f"{dd.date()} {t} {c} 기간조회 {px[t].at[dd, c]} vs 날짜조회 {b.at[t, c]}")
print("[2] 정규장 대조:", "최근 5일 × 3종목 시가·종가 모두 일치" if not bad else "불일치 → 아래 결과 쓰지 말 것\n    " + "\n    ".join(bad))

# ---------- 3. 계산 ----------
def nights(d):
    d = d[d["종가"] > 0]
    on = d["시가"].shift(-1)
    r = (on / d["종가"] - 1).where(on > 0)
    nxt = pd.Series(d.index, index=d.index).shift(-1)
    x = pd.DataFrame({"r": r, "nxt": nxt}).dropna()
    x = x[x.r.abs() < 0.30]
    x["mon"] = pd.to_datetime(x.nxt).dt.weekday == 0
    x["y"] = x.index.year
    x["n0"] = x.r - COST                                    # 세금0
    x["nT"] = x.n0 - TAX * x.n0.clip(lower=0)               # 최악 세금
    return x

def days(d):
    d = d[(d["시가"] > 0) & (d["종가"] > 0)]
    return d["종가"] / d["시가"] - 1

def summ(f, col):
    span = (f.index[-1] - f.index[0]).days / 365.25
    eq = (1 + f[col]).cumprod()
    yrs = f.groupby("y")[col].apply(lambda s: (1 + s).prod() - 1)
    return dict(cagr=eq.iloc[-1] ** (1 / span) - 1, sharpe=f[col].mean() / f[col].std() * np.sqrt(len(f) / span),
                mdd=(eq / eq.cummax() - 1).min(), cum=eq.iloc[-1] - 1, plus=int((yrs > 0).sum()), ny=len(yrs),
                mean=f[col].mean(), n=len(f))

def line(s):
    return (f"하루 {s['mean'] * 100:+.3f}% · CAGR {s['cagr'] * 100:+.1f}% · Sharpe {s['sharpe']:.2f} · "
            f"MDD {s['mdd'] * 100:.1f}% · 누적 {s['cum'] * 100:+.0f}% · 플러스 연도 {s['plus']}/{s['ny']}")

def period(on, lo, hi, mon_in):
    f = on[(on.y >= lo) & (on.y <= hi)]
    return f if mon_in else f[~f.mon]

res = {}
for t, nm in ETF.items():
    on, dy = nights(px[t]), days(px[t])
    res[t] = on
    print(f"\n■ {t} {nm}")
    print("  연도  밤평균(정식)  플러스비율  연 순수익[세금0]  [최악]  낮(시가→종가)")
    for y, g in on.groupby("y"):
        f = g[~g.mon]
        print(f"  {y}  {f.r.mean() * 100:+.3f}%  {(f.r > 0).mean() * 100:5.1f}%  "
              f"{((1 + f.n0).prod() - 1) * 100:+7.1f}%  {((1 + f.nT).prod() - 1) * 100:+7.1f}%  {dy[dy.index.year == y].mean() * 100:+.3f}%")
    for lab, lo, hi in (("2015~2024", 2015, 2024), ("2025~2026", 2025, 2026)):
        for mon_in, ml in ((False, "정식(월요일 제외)"), (True, "전체(월요일 포함)")):
            f = period(on, lo, hi, mon_in)
            print(f"  {lab} {ml} {len(f)}밤")
            print(f"     [세금0] {line(summ(f, 'n0'))}")
            if t != BASE:
                print(f"     [최악] {line(summ(f, 'nT'))}")

# ---------- 4. 2배 점검 (2015~2024, KODEX 200 대비 기울기) ----------
print("\n[4] KODEX 200 대비 기울기(2배면 ≈ 2.0)")
b = px[BASE]
for t, nm in ETF.items():
    if t == BASE:
        continue
    d = px[t]
    cc = pd.DataFrame({"x": b["종가"].pct_change(), "y": d["종가"].pct_change()}).dropna()
    cc = cc[(cc.index.year >= 2015) & (cc.index.year <= 2024)]
    nn = res[t][["r"]].join(res[BASE][["r"]], rsuffix="_b", how="inner")
    nn = nn[(nn.index.year >= 2015) & (nn.index.year <= 2024)]
    s1 = cc.x.cov(cc.y) / cc.x.var()
    s2 = nn.r_b.cov(nn.r) / nn.r_b.var()
    print(f"  {nm}: 하루(종가→종가) 기울기 {s1:.2f} · 상관 {cc.x.corr(cc.y):.3f} ({len(cc)}일) | "
          f"밤 기울기 {s2:.2f} · 상관 {nn.r_b.corr(nn.r):.3f} ({len(nn)}밤)")

# ---------- 5. 판정 (사전 고정) ----------
base = summ(period(res[BASE], 2015, 2024, False), "n0")
print(f"\n[5] 판정 기준: KODEX 200 밤사이 CAGR {base['cagr'] * 100:+.1f}% 초과 ∧ 플러스 연도 ≥ 7/10 (2015~2024 정식 밤)")
for t, nm in ETF.items():
    if t == BASE:
        continue
    f = period(res[t], 2015, 2024, False)
    for col, cl in (("n0", "세금0"), ("nT", "최악")):
        s = summ(f, col)
        ok = s["cagr"] > base["cagr"] and s["plus"] >= 7
        print(f"  {nm} [{cl}]: CAGR {s['cagr'] * 100:+.1f}% · 플러스 {s['plus']}/{s['ny']} · MDD {s['mdd'] * 100:.1f}% → {'통과' if ok else '불통과'}")

print("\n참고: 정식 밤 = 다음 거래일이 월요일이면 보유 안 함(현금). Sharpe = 순수익 평균÷표준편차×√(연간 밤 수).")
print("참고: 실제 세금은 [세금0]에 가까움(과표 상승분 ≈ 0). [최악]은 상한선 참고용.")
print("끝. 출력 전체를 붙여 주세요.")
