# new5_night_shape_cell.py — 읽기 전용 (레포·운용 변경 없음)
# 목적: 밤 흐름의 '모양'을 본다. 오르는 구간이 언제 시작해 얼마나 갔는지. 판정·신호 없음.
# 대상: KODEX 200(069500), 2015-01-02 ~ 실행일.
# 밤 정의(①·②와 같음): 종가 매수 → 다음 거래일 시가 매도, 다음 거래일 월요일이면 쉼, 비용 왕복 0.006%.
# 구간을 자동으로 나누지 않는다(나누는 기준에 근거가 없음). 그림·연도·분기·월 표로 모양만 본다.
# 저장: Drive /MyDrive/new5_shape/ (일봉 parquet, 그림 png)
import os, sys, subprocess
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "pykrx"], check=True)
from google.colab import userdata
os.environ["KRX_ID"] = userdata.get("KRX_ID")
os.environ["KRX_PW"] = userdata.get("KRX_PW")
import numpy as np, pandas as pd
import matplotlib
import matplotlib.pyplot as plt
from pykrx import stock

T, NM = "069500", "KODEX 200"
S = "20150101"
E = pd.Timestamp.now(tz="Asia/Seoul").strftime("%Y%m%d")
COST = 0.00006
OUT = "/content/drive/MyDrive/new5_shape"

# ---------- 0. 이름 ----------
real = str(stock.get_etf_ticker_name(T))
print(f"[0] {T} 기대 '{NM}' · KRX 이름 '{real}'" + ("" if real.replace(" ", "") == NM.replace(" ", "") else "  ← 다름, 결과 쓰지 말 것"))

# ---------- 1. 자료 ----------
d = stock.get_etf_ohlcv_by_date(S, E, T)
print(f"[1] {len(d)}일 {d.index.min().date()}~{d.index.max().date()} (실행일 {E})")
try:
    if not os.path.exists("/content/drive/MyDrive"):
        from google.colab import drive
        drive.mount("/content/drive")
    os.makedirs(OUT, exist_ok=True)
    d.to_parquet(f"{OUT}/{T}.parquet")
    print(f"[1] 저장 {OUT}/{T}.parquet")
except Exception as e:
    OUT = None
    print(f"[1] Drive 저장 실패(계산은 계속): {type(e).__name__} {str(e)[:120]}")

# ---------- 2. 최근 5일 기간조회·날짜조회 대조 ----------
bad = []
for dd in d.index[-5:]:
    b = stock.get_etf_ohlcv_by_ticker(dd.strftime("%Y%m%d"))
    if T not in b.index:
        bad.append(f"{dd.date()} 없음"); continue
    for c in ("시가", "종가"):
        if float(d.at[dd, c]) != float(b.at[T, c]):
            bad.append(f"{dd.date()} {c} {d.at[dd, c]} vs {b.at[T, c]}")
print("[2] 대조:", "최근 5일 시가·종가 일치" if not bad else "불일치 → " + " / ".join(bad))
print("    (마지막 날 종가는 어떤 밤에도 안 쓰임: 그 밤은 다음 날 시가가 없어 빠짐)")

# ---------- 3. 밤 ----------
d = d[(d["종가"] > 0) & (d["시가"] > 0)]
on = d["시가"].shift(-1)
x = pd.DataFrame({"r": on / d["종가"] - 1, "nxt": pd.Series(d.index, index=d.index).shift(-1)}).dropna()
x = x[x.r.abs() < 0.30]
x = x[pd.to_datetime(x.nxt).dt.weekday != 0]          # 다음 거래일 월요일이면 쉼
x["n"] = x.r - COST
eq = (1 + x.n).cumprod()
px = d["종가"] / d["종가"].iloc[0]                    # 참고: 그냥 들고 있기(종가→종가)
print(f"[3] 밤 {len(x)}개 {x.index.min().date()}~{x.index.max().date()} · 누적 {(eq.iloc[-1] - 1) * 100:+.1f}%")

# ---------- 4. 연도·분기 ----------
print("\n[4] 연도: 밤수 · 순수익(복리) · 하루평균 · 플러스비율 | 참고 들고있기")
for y, g in x.groupby(x.index.year):
    py = d["종가"][d.index.year == y]
    prev = d["종가"][d.index.year < y]
    base = prev.iloc[-1] if len(prev) else py.iloc[0]
    print(f"  {y}  {len(g):3d}  {((1 + g.n).prod() - 1) * 100:+6.1f}%  {g.n.mean() * 100:+.3f}%  "
          f"{(g.n > 0).mean() * 100:5.1f}% | {(py.iloc[-1] / base - 1) * 100:+6.1f}%")
print("\n[4] 분기: 밤수 · 순수익(복리)")
q = x.groupby(x.index.to_period("Q")).n.agg(["size", lambda s: (1 + s).prod() - 1])
q.columns = ["k", "ret"]
for i in range(0, len(q), 4):
    print("  " + "  ".join(f"{p} {int(k):2d} {r * 100:+5.1f}%" for p, k, r in q.iloc[i:i + 4].itertuples()))

# ---------- 5. 월 ----------
print("\n[5] 월별 순수익(복리) %: 1월 ~ 12월")
m = x.groupby(x.index.to_period("M")).n.apply(lambda v: (1 + v).prod() - 1)
for y in sorted(set(m.index.year)):
    mm = m[m.index.year == y]
    print(f"  {y} " + " ".join(f"{mm[p] * 100:+5.1f}" if p in mm.index else "    ." for p in pd.period_range(f"{y}-01", f"{y}-12", freq="M")))

# ---------- 6. 그림 ----------
fig, ax = plt.subplots(figsize=(12, 5))
ax.plot(eq.index, eq.values, label="KODEX 200 nights (net)", lw=1.3)
ax.plot(px.index, px.values, label="KODEX 200 buy&hold (ref)", lw=0.8, alpha=0.6)
ax.set_yscale("log"); ax.grid(alpha=0.3); ax.legend()
ax.set_title(f"Night flow shape 2015~{E[:4]} (log scale)")
if OUT:
    fig.savefig(f"{OUT}/night_shape.png", dpi=120, bbox_inches="tight"); print(f"\n[6] 그림 저장 {OUT}/night_shape.png")
plt.show()
print("끝. 출력 전체와 그림을 붙여 주세요.")
