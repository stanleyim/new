# [Colab 셀] 밤사이 규칙 10년 검증 재현(Claude 계산 독립 대조용). 읽기 전용, 로그인 불필요, 약 5~10분.
# 자료: 레포 main data/ohlcv_full.parquet(439종목) + delisted-data 브랜치 delisted_data/(2,916종목) = 전체 시장(겹침 0)
# 규칙: 레포 overnight_paper.py 함수(pivots·top_list) 그대로. 2015-06-15 이전 상한가 기준 +14.5%(당시 가격제한 ±15%). 비용 0.206%.
# 점검: 최근 1년 명단을 krx_daily 명단과 비교(같은 종목 집합) → 거의 완전 일치해야 함.
import os, sys, glob, subprocess
import numpy as np, pandas as pd
sh = lambda c: subprocess.run(c, shell=True, check=True)
if not os.path.isdir("/content/main/overnight_paper"):
    sh("rm -rf /content/main; git clone -q --depth 1 -b main https://github.com/stanleyim/new.git /content/main")
if not os.path.isdir("/content/dl/delisted_data"):
    sh("rm -rf /content/dl; git clone -q --depth 1 --filter=blob:none --sparse -b delisted-data https://github.com/stanleyim/new.git /content/dl")
    sh("git -C /content/dl sparse-checkout set delisted_data")
if not os.path.isdir("/content/pit/pit_data_extra"):
    sh("rm -rf /content/pit; git clone -q --depth 1 --filter=blob:none --sparse -b pit-data https://github.com/stanleyim/new.git /content/pit")
    sh("git -C /content/pit sparse-checkout set --no-cone 'pit_data_extra/idx_*.parquet'")
sys.path.insert(0, "/content/main")
import overnight_paper as OP
COST = 0.00206
cols = ["ticker", "date", "시가", "고가", "저가", "종가", "거래량"]
fr = []
for f in sorted(glob.glob("/content/dl/delisted_data/*.parquet")):
    d = pd.read_parquet(f).rename(columns={"open": "시가", "high": "고가", "low": "저가", "close": "종가", "volume": "거래량"})
    d["ticker"] = os.path.basename(f)[:-8]
    fr.append(d)
a = pd.concat(fr, ignore_index=True)
o = pd.read_parquet("/content/main/data/ohlcv_full.parquet")
for d in (a, o):
    d["ticker"] = d["ticker"].astype(str).str.zfill(6)
    d["date"] = pd.to_datetime(d["date"]).dt.strftime("%Y%m%d")
print(f"자료: delisted_data {a.ticker.nunique()}종목 · ohlcv_full {o.ticker.nunique()}종목 · 겹침 {len(set(a.ticker) & set(o.ticker))}")
h = pd.concat([a[cols], o[cols]], ignore_index=True)
h = h[h.date >= "20140602"]

# ---------- 점검: 최근 1년 명단 대조 ----------
k = pd.read_parquet("/content/main/overnight_paper/krx_daily.parquet")
k["ticker"] = k["ticker"].astype(str).str.zfill(6)
k["date"] = k["date"].astype(str)
c1, _, _, t1 = OP.pivots(k[k.ticker.isin(set(h.ticker)) & (k.date <= "20260923")])
c2, _, _, t2 = OP.pivots(h[(h.date >= "20251010") & (h.date <= "20260923")])
same = sum([t for t, _ in OP.top_list(t1, d)] == [t for t, _ in OP.top_list(t2, d)] for d in c1[19:])
print(f"점검: 최근 명단 {len(c1) - 19}일 중 종목·순서 완전 일치 {same}일 (거래일 같음 {c1 == c2})")

# ---------- 전체 기간 규칙 ----------
cal, op, cl, tv20 = OP.pivots(h)
rows = []
for i, d in enumerate(cal[:-1]):
    if i == 0 or tv20.loc[cal[i - 1]].isna().all():
        continue
    prev, n = cal[i - 1], cal[i + 1]
    lim = 0.145 if d < "20150615" else OP.LIMIT_UP
    rs = []
    for t, _ in OP.top_list(tv20, prev):
        c, c0, op_ = cl.at[d, t], cl.at[prev, t], op.at[n, t]
        if not (c > 0) or (c0 > 0 and c / c0 - 1 >= lim) or not (op_ > 0) or abs(op_ / c - 1) >= OP.CA_ABS:
            continue
        rs.append(op_ / c - 1)
    if rs:
        rows.append(dict(d=d, kind="그림자" if pd.Timestamp(n).weekday() == 0 else "정식", g=np.mean(rs)))
x = pd.DataFrame(rows)
x["ret"] = np.where(x.kind == "정식", x.g - COST, 0.0)
ix = pd.concat([pd.read_parquet(f) for f in glob.glob("/content/pit/pit_data_extra/idx_*.parquet")])
ix = ix[ix["index"] == "KOSPI200"].assign(d=lambda z: pd.to_datetime(z["date"]).dt.strftime("%Y%m%d")).drop_duplicates("d").set_index("d").sort_index()
x["k200"] = x.d.map(ix["시가"].shift(-1) / ix["종가"] - 1)

def met(r):
    eq = (1 + r).cumprod()
    return (eq.iloc[-1] - 1) * 100, (eq / eq.cummax() - 1).min() * 100

print(f"\n연도별 (비용 {COST:.3%}, 정식일만 매매, 하루 % = 정식일 평균)")
print("연도  정식일  총수익   순수익   누적      MDD     K200밤사이")
for y in sorted(set(x.d.str[:4])):
    g = x[x.d.str[:4] == y]
    f = g[g.kind == "정식"]
    cum, mdd = met(g.ret)
    print(f"{y}  {len(f):4d}  {f.g.mean() * 100:+.3f}  {(f.g.mean() - COST) * 100:+.3f}  {cum:+7.1f}%  {mdd:6.1f}%  {f.k200.mean() * 100:+.3f}")
for nm, lo, hi in (("2015~2024", "20150101", "20241231"), ("2025-11~", "20251107", "20261031")):
    g = x[(x.d >= lo) & (x.d <= hi)]
    f = g[g.kind == "정식"]
    cum, mdd = met(g.ret)
    print(f"{nm}: 정식일 {len(f)} · 하루 순 {(f.g.mean() - COST) * 100:+.3f}% · 누적 {cum:+.1f}% · CAGR {((1 + cum / 100) ** (250 / len(g)) - 1) * 100:+.1f}% · MDD {mdd:.1f}%")
print("\n끝. 출력 전체를 붙여 주세요.")
