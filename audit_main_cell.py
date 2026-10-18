# [Colab] 운용 데이터 무결성 감사 (읽기 전용, 레포 변경 없음) — 먼저 audit_clone_cell.py 실행
import os, json, glob, time
import numpy as np, pandas as pd
t0 = time.time()
DST = "/content/audit_main"
CUT = pd.Timestamp("2026-06-29")      # 이 날부터 KIS 일별 추가분
TICK_FROM = pd.Timestamp("2023-01-25") # 현행 호가단위 적용일
assert os.path.isfile(f"{DST}/data/ohlcv_full.parquet"), "audit_clone_cell.py 먼저 실행"

# 저장 위치: Drive (실패 시 /content)
OUTD = "/content/new_data_audit_out"
try:
    from google.colab import drive
    drive.mount("/content/drive")
    OUTD = "/content/drive/MyDrive/new_data_audit_out"
except Exception as e:
    print("Drive 마운트 실패 → /content 저장:", e)
os.makedirs(OUTD, exist_ok=True)

def krx_tick(p):  # signal_runner.krx_tick 과 동일
    return (1 if p < 2000 else 5 if p < 5000 else 10 if p < 20000 else 50 if p < 50000
            else 100 if p < 200000 else 500 if p < 500000 else 1000)

# ---------- 로드 ----------
df = pd.read_parquet(f"{DST}/data/ohlcv_full.parquet")
df["date"] = pd.to_datetime(df["date"]); df["ticker"] = df["ticker"].astype(str)
print(f"rows {len(df):,} · dup(date,ticker) {df.duplicated(['date','ticker']).sum()}")
df = df.sort_values(["ticker", "date"]).reset_index(drop=True)
cal = np.sort(df["date"].unique())
prev_cal = pd.Series(np.r_[np.datetime64("NaT"), cal[:-1]], index=cal)
g = df.groupby("ticker")
df["prev_close"] = g["종가"].shift(1)
df["prev_date"] = g["date"].shift(1)
ok = (df["prev_close"] > 0) & (df["종가"] > 0)
df["ret"] = np.where(ok, df["종가"] / df["prev_close"] - 1, np.nan)
df["diff"] = df["등락률"] - df["ret"] * 100
df["gap_day"] = df["prev_date"].notna() & (df["prev_date"].values != prev_cal.reindex(df["date"]).values)
df["P1"] = df["date"] >= CUT

# ---------- A. 등락률 vs 종가 변동 ----------
v = df["ret"].notna()
df["mis"] = v & (df["diff"].abs() > 0.5)
df["sign_mis"] = v & (df["ret"].abs() > 0.005) & (df["등락률"].abs() > 0.5) & (np.sign(df["ret"]) != np.sign(df["등락률"]))
df["zero_rate"] = v & (df["ret"].abs() > 0.005) & (df["등락률"] == 0)
print("\n[A] 등락률 vs 종가 변동 (|차이|>0.5%p)")
for lab, m in [("~06-26", ~df["P1"]), ("06-29~", df["P1"])]:
    s = df[m & v]
    print(f"  {lab}: rows {len(s):,} · 불일치 {s['mis'].sum():,} · 부호 반대 {s['sign_mis'].sum():,} · "
          f"등락률=0인데 변동 {s['zero_rate'].sum():,} · 결측일 건너뜀 {s['gap_day'].sum():,}")
p1m = df[df["P1"] & df["mis"]].copy()
if len(p1m):
    p1m["impl_ratio"] = (p1m["종가"] / (1 + p1m["등락률"] / 100)) / p1m["prev_close"]
    print(f"  06-29~ 불일치 중 결측일 건너뜀 비중 {p1m['gap_day'].mean():.1%} · "
          f"함의 전일종가/기록 전일종가 분위 p5/p50/p95 = "
          f"{p1m['impl_ratio'].quantile(.05):.3f}/{p1m['impl_ratio'].median():.3f}/{p1m['impl_ratio'].quantile(.95):.3f}")
    byd = p1m.groupby("date").agg(n=("ticker", "size"), gap=("gap_day", "mean"),
                                  sign=("sign_mis", "sum"), ratio=("impl_ratio", "median"))
    byd["of_day"] = df[df["P1"]].groupby("date").size().reindex(byd.index)
    print("  불일치 많은 날 상위 12 (n/당일종목수 · 결측일 비중 · 부호반대 · 함의비 중앙):")
    for d, r in byd.sort_values("n", ascending=False).head(12).iterrows():
        print(f"    {d.date()}  {int(r.n)}/{int(r.of_day)} · {r.gap:.0%} · {int(r.sign)} · {r.ratio:.3f}")
    byd.to_csv(f"{OUTD}/A_mismatch_by_date.csv")
    p1m.to_csv(f"{OUTD}/A_mismatch_rows_P1.csv", index=False)

# 날짜별 종목 수 (06-29~ 누락일)
cnt = df[df["P1"]].groupby("date").size()
print(f"  06-29~ 날짜별 종목수 min {cnt.min()} · median {int(cnt.median())} · max {cnt.max()} "
      f"(439 미만 날 {int((cnt < 439).sum())}/{len(cnt)})")

# ---------- B. 큰 단차 ----------
big = df[df["P1"] & (df["ret"].abs() > 0.20)].copy()
print(f"\n[B] 06-29~ |종가 변동|>20%: {len(big)}행 (>30% 가격제한 초과: {(big['ret'].abs() > 0.30).sum()})")
for _, r in big.sort_values("date").iterrows():
    print(f"    {r.date.date()} {r.ticker} {str(r.get('name',''))[:10]:10s} 종가변동 {r.ret*100:+.1f}% · 등락률 {r['등락률']:+.1f}%"
          f"{' · 결측일건너뜀' if r.gap_day else ''}")
big.to_csv(f"{OUTD}/B_big_jumps_P1.csv", index=False)

# ---------- C. 호가단위 ----------
print(f"\n[C] 호가단위 위반 ({TICK_FROM.date()}~, 가격>0)")
cols = ["시가", "고가", "저가", "종가"]
tk = df[df["date"] >= TICK_FROM]
for c in cols:
    p = tk[c]
    bad = (p > 0) & ((p != np.round(p)) | (np.round(p) % p.apply(krx_tick) != 0))
    df.loc[tk.index, f"tick_bad_{c}"] = bad
df[[f"tick_bad_{c}" for c in cols]] = df[[f"tick_bad_{c}" for c in cols]].fillna(False).astype(bool)
df["tick_bad"] = df[[f"tick_bad_{c}" for c in cols]].any(axis=1)
for lab, m in [("23-01-25~06-26", (df["date"] >= TICK_FROM) & ~df["P1"]), ("06-29~", df["P1"])]:
    s = df[m]
    print(f"  {lab}: rows {len(s):,} · " + " · ".join(f"{c} {int(s[f'tick_bad_{c}'].sum()):,}" for c in cols))
tb = df[df["P1"] & df["tick_bad"]]
if len(tb):
    td = tb.groupby("date").size()
    print("  06-29~ 위반 많은 날 상위 8: " + ", ".join(f"{d.date()} {n}" for d, n in td.sort_values(ascending=False).head(8).items()))
    print("  예시 6행:")
    for _, r in tb.head(6).iterrows():
        print(f"    {r.date.date()} {r.ticker} 시{r['시가']:g} 고{r['고가']:g} 저{r['저가']:g} 종{r['종가']:g}")
    tb.to_csv(f"{OUTD}/C_tick_bad_P1.csv", index=False)

# ---------- D. 포워드 기록 영향 ----------
print("\n[D] 포워드 기록 영향 (all_signals_log)")
recs = []
for lf in sorted(glob.glob(f"{DST}/output/all_signals_log/*.json")):
    try:
        data = json.load(open(lf, encoding="utf-8"))
    except Exception as e:
        print("  읽기 실패", os.path.basename(lf), e); continue
    for r in data.get("records", []):
        recs.append(r)
print(f"  records {len(recs)} (status: " +
      ", ".join(f"{k} {v}" for k, v in pd.Series([r.get('status') for r in recs]).value_counts().items()) + ")")
flag = df["P1"] & (df["mis"] | (df["ret"].abs() > 0.20) | df["tick_bad"] | df["gap_day"])
dfi = df.set_index(["ticker", "date"])
def px(t, d, c):
    try: return float(dfi.at[(t, pd.Timestamp(d)), c])
    except Exception: return np.nan
out = []
for r in recs:
    t = str(r.get("ticker")); sd = r.get("signal_date")
    if not sd: continue
    sd = pd.Timestamp(str(sd)[:10])
    gt = df[df["ticker"] == t]
    win = gt[gt["date"] <= sd].tail(61)                     # 60일 고가 + shift(1)
    hold = gt[(gt["date"] > sd)]
    ed, xd = r.get("virtual_entry_date"), r.get("virtual_exit_date_planned")
    if xd: hold = hold[hold["date"] <= pd.Timestamp(str(xd)[:10])]
    basis = "종가" if r.get("entry_basis") == "T+1_close" else "시가"
    ep, xp = r.get("virtual_entry_price"), r.get("virtual_exit_price")
    e_now = px(t, ed, basis) if ed else np.nan
    x_now = px(t, xd, "종가") if (xd and xp) else np.nan
    s_now = px(t, sd, "종가")
    sig_row = gt[gt["date"] == sd]
    out.append({
        "signal_date": sd.date(), "ticker": t, "name": r.get("name"), "status": r.get("status"),
        "win_flag": int(flag.loc[win.index].sum()), "win_mis": int(df.loc[win.index, "mis"].sum()),
        "win_jump20": int((df.loc[win.index, "ret"].abs() > 0.2).sum()),
        "win_gap": int(df.loc[win.index, "gap_day"].sum()),
        "sigday_rate": float(sig_row["등락률"].iloc[0]) if len(sig_row) else np.nan,
        "sigday_ret%": float(sig_row["ret"].iloc[0] * 100) if len(sig_row) else np.nan,
        "hold_flag": int(flag.loc[hold.index].sum()),
        "signal_close_log": r.get("signal_close"), "signal_close_now": s_now,
        "entry_log": ep, f"entry_now": e_now, "entry_basis": basis,
        "entry_tick_ok": (bool(float(ep) % krx_tick(float(ep)) == 0) if ep else None),
        "exit_log": xp, "exit_now": x_now,
        "exit_tick_ok": (bool(float(xp) % krx_tick(float(xp)) == 0) if xp else None),
    })
od = pd.DataFrame(out)
od.to_csv(f"{OUTD}/D_forward_records.csv", index=False)
if len(od):
    def neq(a, b): return a.notna() & b.notna() & ((a.astype(float) - b.astype(float)).abs() > 0.5)
    print(f"  신호 계산 구간(61행)에 이상 행 포함: {(od['win_flag'] > 0).sum()}/{len(od)}건 "
          f"(등락률 불일치 {(od['win_mis'] > 0).sum()} · 20%단차 {(od['win_jump20'] > 0).sum()} · 결측일 {(od['win_gap'] > 0).sum()})")
    print(f"  보유 구간에 이상 행 포함: {(od['hold_flag'] > 0).sum()}건")
    print(f"  신호일 등락률≠종가변동(>0.5%p): {neq(od['sigday_rate'], od['sigday_ret%']).sum()}건")
    print(f"  기록값 vs 현재 파일 불일치 — 신호일 종가 {neq(od['signal_close_log'], od['signal_close_now']).sum()} · "
          f"매수가 {neq(od['entry_log'], od['entry_now']).sum()} · 청산가 {neq(od['exit_log'], od['exit_now']).sum()}")
    print(f"  호가단위 위반 — 매수가 {(od['entry_tick_ok'] == False).sum()} · 청산가 {(od['exit_tick_ok'] == False).sum()}")
    show = od[(od["win_flag"] > 0) | (od["hold_flag"] > 0) | (od["entry_tick_ok"] == False) | (od["exit_tick_ok"] == False)
              | neq(od["sigday_rate"], od["sigday_ret%"])]
    print(f"  해당 기록 {len(show)}건 (최대 25 표시):")
    for _, r in show.head(25).iterrows():
        print(f"    {r.signal_date} {r.ticker} {str(r['name'])[:8]:8s} {str(r.status):8s} 구간{r.win_flag} 보유{r.hold_flag} "
              f"등락률{r.sigday_rate:+.1f}/변동{r['sigday_ret%']:+.1f} 매수{r.entry_log}/{r.entry_now:g} 청산{r.exit_log}/{r.exit_now:g}")
print(f"\n저장: {OUTD} · done {time.time()-t0:.0f}s")
