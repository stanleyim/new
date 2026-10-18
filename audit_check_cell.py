# [Colab] 감사 후속: HTS 대조용 행 출력 + 호가 위반 성격 확인 (읽기 전용, audit_main_cell.py 실행한 같은 런타임)
import json, glob, numpy as np, pandas as pd
DST = "/content/audit_main"
df = pd.read_parquet(f"{DST}/data/ohlcv_full.parquet")
df["date"] = pd.to_datetime(df["date"]); df["ticker"] = df["ticker"].astype(str)
df = df.sort_values(["ticker", "date"]).reset_index(drop=True)
df["전일종가"] = df.groupby("ticker")["종가"].shift(1)
def krx_tick(p):
    return (1 if p < 2000 else 5 if p < 5000 else 10 if p < 20000 else 50 if p < 50000
            else 100 if p < 200000 else 500 if p < 500000 else 1000)

# 1) 06-29~ 호가 위반 값이 '반 호가'(중간가)인지
p1 = df[df["date"] >= "2026-06-29"]
for c in ["고가", "저가", "종가"]:
    p = p1[c][p1[c] > 0]
    t = p.apply(krx_tick); r = p % t
    bad = r != 0
    print(f"[1] {c}: 위반 {int(bad.sum())} · 그중 정확히 반 호가 {int((bad & (r == t / 2)).sum())}")

# 2) 제우스 09-23 기록
for lf in glob.glob(f"{DST}/output/all_signals_log/2026-09-23.json"):
    for r in json.load(open(lf, encoding="utf-8"))["records"]:
        if str(r["ticker"]) == "079370":
            print("[2] 제우스 기록:", {k: r.get(k) for k in ["signal_date", "virtual_entry_date", "virtual_entry_price",
                  "virtual_exit_date_planned", "virtual_exit_price", "net_ret", "status"]})

# 3) HTS 대조용 행 (파일 값)
chk = [("456010", "2026-09-18"), ("078350", "2026-09-21"), ("005290", "2026-10-01"),
       ("011200", "2026-10-14"), ("011200", "2026-10-15"), ("000100", "2026-07-01")]
x = df.set_index(["ticker", "date"])
print("[3] 파일 값 (HTS KRX 일봉과 대조)")
for t, d in chk:
    try:
        r = x.loc[(t, pd.Timestamp(d))]
        print(f"  {t} {str(r['name'])[:8]:8s} {d}  시{r['시가']:,.0f} 고{r['고가']:,.0f} 저{r['저가']:,.0f} "
              f"종{r['종가']:,.0f} · 전일종{r['전일종가']:,.0f} · 등락률{r['등락률']:+.2f}%")
    except KeyError:
        print(f"  {t} {d} 행 없음")
