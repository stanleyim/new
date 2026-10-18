# [Colab] 포워드 27건 KRX 재평가 + 신호 선정 재현 (읽기 전용, 레포 변경 없음)
# audit_clone_cell.py · audit_krx_cell.py 를 실행한 같은 런타임에서 실행 (KRX 로그인 상태 사용)
import os, sys, json, glob, time
import numpy as np, pandas as pd
DST = "/content/audit_main"
OUTD = "/content/drive/MyDrive/new_data_audit_out" if os.path.isdir("/content/drive/MyDrive") else "/content/new_data_audit_out"
os.makedirs(OUTD, exist_ok=True)
assert os.environ.get("KRX_ID"), "audit_krx_cell.py 를 먼저 실행한 런타임에서 실행"
sys.path.insert(0, DST)
import signal_runner as SR            # 레포 운용 코드 그대로 사용 (__main__ 가드라 실행되지 않음)
from pykrx import stock
t0 = time.time()
START, WARM = pd.Timestamp("2026-06-29"), pd.Timestamp("2025-12-01")
PX = ["시가", "고가", "저가", "종가", "거래량", "등락률"]

def load(p):
    x = pd.read_parquet(p); x["date"] = pd.to_datetime(x["date"]); x["ticker"] = x["ticker"].astype(str)
    return x[x["date"] >= WARM].copy()
oh, flow, short = load(SR.OHLCV_PATH), load(SR.FLOW_PATH), load(SR.SHORT_PATH)
univ = pd.read_parquet(SR.UNIVERSE_PATH); univ["ticker"] = univ["ticker"].astype(str)
dates = sorted(oh.loc[oh["date"] >= START, "date"].unique())

# ---- KRX 일봉 (거래량 포함) ----
rows = []
for d in dates:
    ds = pd.Timestamp(d).strftime("%Y%m%d"); x = None
    for _ in range(3):
        try: x = stock.get_market_ohlcv(ds, market="ALL"); break
        except Exception: time.sleep(2)
    assert x is not None and len(x), f"KRX 조회 실패 {ds}"
    x = x.reset_index(); x.columns = ["ticker"] + list(x.columns[1:]); x["ticker"] = x["ticker"].astype(str)
    x["date"] = pd.Timestamp(d); rows.append(x[["date", "ticker"] + PX]); time.sleep(0.5)
krx = pd.concat(rows)
m = oh.merge(krx, on=["date", "ticker"], how="left", suffixes=("", "_k"))
fix = (m["date"] >= START) & m["종가_k"].notna()
print(f"KRX 교체 대상 {int(fix.sum()):,}/{int((m['date'] >= START).sum()):,}행 · 거래량 다름 "
      f"{int((fix & ((m['거래량'] - m['거래량_k']).abs() > 0.5)).sum()):,}")
ohk = m.copy()
for c in PX:
    ohk.loc[fix, c] = ohk.loc[fix, c + "_k"]
ohk = ohk[oh.columns]

# ---- KOSDAQ 과열 (운용 코드와 같은 식) ----
ix = stock.get_index_ohlcv_by_date(WARM.strftime("%Y%m%d"), pd.Timestamp(dates[-1]).strftime("%Y%m%d"), SR.KOSDAQ_IDX_CODE)
px = pd.Series(pd.to_numeric(ix["종가"], errors="coerce").values, index=pd.to_datetime(ix.index)).sort_index()
px = px[px > 0]; ma = px.rolling(60).mean()
hot = ((px / ma - 1.0) > SR.HOT_THR)[ma.notna()]
hot_map = {pd.Timestamp(d): bool(v) for d, v in hot.items()}

def build(o):
    df = SR.compute_features(o.copy(), flow.copy(), short.copy())
    df = SR.add_cap_class(df, univ)
    df["kosdaq_hot"] = df["date"].map(hot_map)
    return df
A, B = build(oh), build(ohk)      # A = 현재 파일, B = KRX 교정

def pre_hot(df, d):               # select_signals 에서 과열 판정만 뺀 것 (데이터 영향 전체 보기)
    t = df[(df["date"] == d) & (df["n_signals"] >= 3)].dropna(subset=["거래대금_20ma"])
    t = t[(t["거래대금_20ma"] >= SR.LIQ_TH) & (t["gap"] <= SR.GAP_CAP)]
    return list(t.nlargest(min(5, len(t)), "signal_score")["ticker"])

# ---- [1] 신호 선정 비교 ----
diff_days, rec1 = 0, []
for d in dates:
    a1, b1 = pre_hot(A, d), pre_hot(B, d)
    a2 = [r["ticker"] for r in SR.select_signals(A, d)]; b2 = [r["ticker"] for r in SR.select_signals(B, d)]
    rec1.append({"date": pd.Timestamp(d).date(), "hot": hot_map.get(pd.Timestamp(d)),
                 "file_top5": ",".join(a1), "krx_top5": ",".join(b1), "file_v8": ",".join(a2), "krx_v8": ",".join(b2)})
r1 = pd.DataFrame(rec1); r1.to_csv(f"{OUTD}/F_signal_compare.csv", index=False)
d1 = r1[r1["file_top5"] != r1["krx_top5"]]; d2 = r1[r1["file_v8"] != r1["krx_v8"]]
print(f"\n[1] 신호 선정 ({len(dates)}거래일, v8 규칙)")
print(f"  과열 판정 전 top5 다른 날 {len(d1)} · v8 최종 신호 다른 날 {len(d2)}")
for _, r in d2.iterrows():
    print(f"    {r.date}  파일 [{r.file_v8 or '-'}] → KRX [{r.krx_v8 or '-'}]")
nm = dict(zip(univ["ticker"], univ["name"]))
if len(d1):
    print("  과열 판정 전 차이 (최대 15일):")
    for _, r in d1.head(15).iterrows():
        a, b = set(filter(None, r.file_top5.split(","))), set(filter(None, r.krx_top5.split(",")))
        print(f"    {r.date} {'과열' if r.hot else '    '} 빠짐 {[nm.get(t, t) for t in a - b]} · 추가 {[nm.get(t, t) for t in b - a]}")

# ---- [2] 포워드 기록 KRX 재평가 ----
K = krx.set_index(["ticker", "date"])
def kp(t, d, c):
    try: return float(K.at[(t, pd.Timestamp(str(d)[:10])), c])
    except Exception: return np.nan
last = pd.Timestamp(dates[-1]); out = []
for lf in sorted(glob.glob(f"{DST}/output/all_signals_log/*.json")):
    for r in json.load(open(lf, encoding="utf-8")).get("records", []):
        t = str(r["ticker"]); sd = r.get("signal_date"); ed = r.get("virtual_entry_date"); xd = r.get("virtual_exit_date_planned")
        basis = "종가" if r.get("entry_basis") == "T+1_close" else "시가"
        sck, epk = kp(t, sd, "종가"), (kp(t, ed, basis) if ed else np.nan)
        closed = r.get("status") == "closed"
        xpk = kp(t, xd, "종가") if (closed and xd) else kp(t, last, "종가")
        out.append({"signal_date": str(sd)[:10], "ticker": t, "name": r.get("name"), "status": r.get("status"), "basis": basis,
                    "sig_close_log": r.get("signal_close"), "sig_close_krx": sck,
                    "entry_log": r.get("virtual_entry_price"), "entry_krx": epk,
                    "exit_log": r.get("virtual_exit_price"), "exit_krx": xpk if closed else np.nan,
                    "net_log": r.get("net_ret"), "net_krx": (xpk / epk - 1 - SR.TOTAL_COST) if closed else np.nan,
                    "now_ret_krx": (xpk / epk - 1) if not closed else np.nan,
                    "unfilled_krx": bool(basis == "종가" and epk / sck >= SR.FILL_LIMIT) if (epk == epk and sck == sck) else None})
r2 = pd.DataFrame(out); r2.to_csv(f"{OUTD}/G_forward_krx.csv", index=False)
def dif(a, b): return a.notna() & b.notna() & ((a.astype(float) - b.astype(float)).abs() > 0.5)
print(f"\n[2] 포워드 기록 {len(r2)}건 KRX 재평가")
print(f"  KRX와 다름 — 신호일 종가 {dif(r2.sig_close_log, r2.sig_close_krx).sum()} · 매수가 {dif(r2.entry_log, r2.entry_krx).sum()} · "
      f"청산가 {dif(r2.exit_log, r2.exit_krx).sum()} · KRX 기준 미체결 {int((r2.unfilled_krx == True).sum())}")
c = r2[r2.status == "closed"]
print(f"  종결 {len(c)}건 평균 net: 기록 {c.net_log.astype(float).mean()*100:+.2f}% · KRX {c.net_krx.mean()*100:+.2f}%")
for _, r in r2.iterrows():
    tail = (f"net {float(r.net_log)*100:+.2f}%→{r.net_krx*100:+.2f}%" if r.status == "closed"
            else f"현재({last.date()}) {r.now_ret_krx*100:+.2f}%")
    print(f"  {r.signal_date} {str(r['name'])[:8]:8s} {r.basis} 신호종가 {r.sig_close_log}/{r.sig_close_krx:,.0f} · "
          f"매수 {r.entry_log}/{r.entry_krx:,.0f} · 청산 {r.exit_log}/{r.exit_krx:,.0f} · {tail}")
print(f"\n(값 표기: 기록/KRX) 저장: {OUTD}/F_signal_compare.csv · G_forward_krx.csv · {time.time()-t0:.0f}s")
