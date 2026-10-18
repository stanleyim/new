# [Colab] KRX 공식 일봉과 ohlcv_full(06-29~) 전수 대조 (읽기 전용, 레포 변경 없음)
# audit_clone_cell.py 실행한 런타임에서 실행. KRX ID/PW는 Colab 보안 비밀에서 읽음.
import os, time, subprocess, sys
import numpy as np, pandas as pd
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "pykrx"], check=True)
# Colab 보안 비밀(🔑) KRX_ID·KRX_PW 사용 (노트북 액세스 허용 필요). 값은 출력·저장하지 않음
for key in ["KRX_ID", "KRX_PW"]:
    if not os.environ.get(key):
        try:
            from google.colab import userdata
            os.environ[key] = userdata.get(key)
        except Exception as e:
            raise SystemExit(f"{key} 를 Colab 보안 비밀에서 못 읽음 ({type(e).__name__}) — 왼쪽 🔑에서 이름이 {key} 인지, '노트북 액세스'가 켜져 있는지 확인")
print("KRX_ID·KRX_PW 로드 OK")
from pykrx import stock

DST = "/content/audit_main"
OUTD = "/content/drive/MyDrive/new_data_audit_out" if os.path.isdir("/content/drive/MyDrive") else "/content/new_data_audit_out"
os.makedirs(OUTD, exist_ok=True)

f = pd.read_parquet(f"{DST}/data/ohlcv_full.parquet")
f["date"] = pd.to_datetime(f["date"]); f["ticker"] = f["ticker"].astype(str)
f = f.sort_values(["ticker", "date"]).reset_index(drop=True)
f["prev_close"] = f.groupby("ticker")["종가"].shift(1)
f = f[f["date"] >= "2026-06-29"].copy()
f["kis_prev"] = f["종가"] / (1 + f["등락률"] / 100)          # 다음날 KIS 등락률로 역산한 전일 종가
dates = sorted(f["date"].unique())
tickers = set(f["ticker"])

rows, fail = [], []
for d in dates:
    ds = pd.Timestamp(d).strftime("%Y%m%d")
    for k in range(3):
        try:
            x = stock.get_market_ohlcv(ds, market="ALL")
            break
        except Exception as e:
            x = None; err = e; time.sleep(2)
    if x is None or len(x) == 0:
        fail.append(ds); continue
    x = x.reset_index()
    x.columns = ["ticker"] + list(x.columns[1:])
    x["ticker"] = x["ticker"].astype(str)
    x = x[x["ticker"].isin(tickers)]
    x["date"] = pd.Timestamp(d)
    rows.append(x[["date", "ticker", "시가", "고가", "저가", "종가", "등락률"]])
    time.sleep(0.5)
print(f"KRX 조회 {len(dates)-len(fail)}/{len(dates)}일" + (f" · 실패 {fail}" if fail else ""))
k = pd.concat(rows).rename(columns={c: "krx_" + c for c in ["시가", "고가", "저가", "종가", "등락률"]})
m = f.merge(k, on=["date", "ticker"], how="left")
m = m.sort_values(["ticker", "date"])
m["krx_prev"] = m.groupby("ticker")["krx_종가"].shift(1)
has = m["krx_종가"].notna()
print(f"대조 행 {int(has.sum()):,}/{len(m):,} (KRX에 없음 {int((~has).sum())})")

def eq(a, b): return (a - b).abs() < 0.5
h = m[has]
for c in ["시가", "고가", "저가", "종가"]:
    print(f"  {c}: 파일=KRX {int(eq(h[c], h['krx_'+c]).sum()):,} · 다름 {int((~eq(h[c], h['krx_'+c])).sum()):,}")
z = h[h["krx_prev"].notna()]
print(f"  전일종가 판정 (행 {len(z):,}): KIS역산=KRX {int(eq(z['kis_prev'], z['krx_prev']).sum()):,} · "
      f"파일전일=KRX {int(eq(z['prev_close'], z['krx_prev']).sum()):,}")
bad = h[~eq(h["종가"], h["krx_종가"])].copy()
bad["diff%"] = (bad["종가"] / bad["krx_종가"] - 1) * 100
if len(bad):
    print(f"  종가 다른 행 차이 p5/p50/p95 = {bad['diff%'].quantile(.05):+.2f}/{bad['diff%'].median():+.2f}/{bad['diff%'].quantile(.95):+.2f}%")
    bd = bad.groupby("date").size()
    print("  종가 다른 날 상위 10: " + ", ".join(f"{pd.Timestamp(d).date()} {n}" for d, n in bd.sort_values(ascending=False).head(10).items()))

print("\n[확인 6건]")
for t, d, c in [("011200", "2026-10-14", "종가"), ("456010", "2026-09-17", "종가"), ("078350", "2026-09-18", "종가"),
                ("005290", "2026-09-30", "종가"), ("079370", "2026-10-13", "종가"), ("000100", "2026-07-01", "저가")]:
    r = m[(m["ticker"] == t) & (m["date"] == d)]
    if len(r):
        r = r.iloc[0]
        print(f"  {t} {str(r['name'])[:8]:8s} {d} {c}: 파일 {r[c]:,.0f} · KRX {r['krx_'+c]:,.0f}")
m.to_csv(f"{OUTD}/E_krx_compare.csv", index=False)
print(f"\n저장: {OUTD}/E_krx_compare.csv")
