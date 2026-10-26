# [Colab 셀] NXT 실측 결과 점검 — KIS 분봉 가격과 KRX 일봉 가격의 단위 어긋남(수정주가 의심) 확인 → 어긋난 건 빼고 재비교.
# 입력: Drive /MyDrive/new5_nxt_out/nxt_items.csv (new5_nxt_full_cell 결과). KIS 호출 없음, 1분 이내.
# 어긋남 판정(사전 고정):
#  ① 9-14 이전 J 마지막 봉이 15:30인 건: KIS J 15:30 가격 / KRX 종가가 ±0.5% 밖이면 어긋남 (같은 가격이어야 함)
#  ② 모든 건: KIS 가격(NX 20시·NX 프리 첫·끝) / KRX 가격(종가·다음날 시가)이 ±25% 밖이면 어긋남 (하루 가격제한 밖 = 단위 차이)
#  어긋난 (종목, 매수일) 건은 제외하고 원래 셀과 같은 방식으로 재비교. 판정 기준도 같음(앞·뒤 절반 모두 '현재'보다 높아야 채택).
import os, numpy as np, pandas as pd
P = "/content/drive/MyDrive/new5_nxt_out/nxt_items.csv"
if not os.path.exists(P):
    from google.colab import drive
    drive.mount("/content/drive")
df = pd.read_csv(P, dtype={"d": str, "n": str, "t": str, "A_nx_t": str, "A_j_t": str, "P0_t": str, "P50_t": str})
df["t"] = df["t"].str.zfill(6)
for c in ("A_nx_t", "A_j_t", "P0_t", "P50_t"):
    df[c] = df[c].fillna("").str.replace(".0", "", regex=False).str.zfill(6).where(df[c].notna(), "")
COST = 0.00206
print(f"읽음 {len(df)}건 · 매수일 {df.d.min()}~{df.d.max()}")

# ① 15:30 가격 대 KRX 종가
j1530 = (df.d < "20260914") & (df.A_j_t == "153000") & df.A_j.gt(0)
df["rj"] = np.where(j1530, df.A_j / df.C, np.nan)
bad1 = j1530 & ((df.rj - 1).abs() > 0.005)
print(f"\n① 9-14 이전 J 15:30 건 {int(j1530.sum())} 중 KRX 종가와 ±0.5% 밖 {int(bad1.sum())}건")
q = df.loc[j1530, "rj"]
print("   비율(J 15:30 / KRX 종가) 분포:", {k: round(float(v), 4) for k, v in q.quantile([0, .01, .05, .5, .95, .99, 1]).items()})
# ② 가격제한 밖 비율
lr = lambda a, b: np.log(df[a] / df[b]).abs()
lim = np.log(1.25)
bad2 = (lr("A_nx", "C") > lim) | (lr("P0", "O") > lim) | (lr("P50", "O") > lim)
bad2 = bad2.fillna(False)
print(f"② KIS 가격 / KRX 가격이 ±25% 밖 {int(bad2.sum())}건")
df["bad"] = bad1 | bad2
b = df[df.bad]
if len(b):
    g = b.groupby("t").agg(건=("d", "size"), 첫날=("d", "min"), 끝날=("d", "max"),
                           J1530비율=("rj", "median"), NX20시비율=("A_nx", "size"))
    g["NX20시비율"] = b.assign(x=b.A_nx / b.C).groupby("t").x.median()
    g["프리끝비율"] = b.assign(x=b.P50 / b.O).groupby("t").x.median()
    print("\n어긋난 종목(건 많은 순, 비율은 중앙값):")
    print(g.sort_values("건", ascending=False).round(4).head(25).to_string())
    print(f"어긋난 건 {len(b)} · 종목 {b.t.nunique()} · 9-14 이후 {int((b.d >= '20260914').sum())}건")

# ---------- 재비교 (어긋난 건 제외) ----------
df["r_now"] = df.O / df.C - 1
df["r_A"] = df.P0 / df.C - 1
df["r_B"] = df.P50 / df.C - 1
df["r_C"] = df.O / df.A_nx - 1
df["r_Cj"] = df.O / df.A_j - 1
cm = df[df[["A_nx", "P0", "P50", "A_j"]].gt(0).all(axis=1) & ~df.bad].copy()
reg = cm[cm.kind == "정식"]
days = sorted(reg.d.unique())
h = len(days) // 2
M = {"현재(종가→KRX시가)": "r_now", "A(종가→NX 08:00)": "r_A", "B(종가→NX 프리끝)": "r_B",
     "C(NX 20시→KRX시가)": "r_C", "Cj(KRX 20시→KRX시가)": "r_Cj"}
day = reg.groupby("d")[list(M.values())].mean() - COST

def met(s):
    eq = (1 + s).cumprod()
    return f"하루 {s.mean() * 100:+.3f}% · Sharpe {s.mean() / s.std() * np.sqrt(252):.2f} · 누적 {(eq.iloc[-1] - 1) * 100:+.1f}% · MDD {(eq / eq.cummax() - 1).min() * 100:.1f}%"

print(f"\n[재비교] 어긋난 건 제외 · 정식일 {len(days)}일 · {len(reg)}건 · 비용 {COST:.3%}")
print(f"  앞 절반 {days[0]}~{days[h - 1]} · 뒤 절반 {days[h]}~{days[-1]}")
for nm, c in M.items():
    s = day[c]
    print(f"  {nm:22} 전체 {met(s)}\n  {'':22} 앞 {s.iloc[:h].mean() * 100:+.3f}% · 뒤 {s.iloc[h:].mean() * 100:+.3f}%")
print("\n[판정] 앞·뒤 절반 모두 '현재'보다 높아야 채택")
for nm, c in list(M.items())[1:]:
    w1 = day[c].iloc[:h].mean() > day["r_now"].iloc[:h].mean()
    w2 = day[c].iloc[h:].mean() > day["r_now"].iloc[h:].mean()
    print(f"  {nm:22} 앞 {'우세' if w1 else '열세'} · 뒤 {'우세' if w2 else '열세'} → {'채택' if (w1 and w2) else '불채택'}")
lg = lambda a, b: np.log(reg[a] / reg[b])
leg = pd.DataFrame({"d": reg.d, "① 종가→NX 20시": lg("A_nx", "C"), "② NX 20시→다음날 08:00": lg("P0", "A_nx"),
                    "③ 프리 08:00→끝": lg("P50", "P0"), "④ 프리끝→KRX 시가": lg("O", "P50")}).groupby("d").mean()
print("\n[밤사이 수익 구간 분해] 정식일 하루 평균 (로그수익 %)")
for c in leg.columns:
    print(f"  {c:20} 전체 {leg[c].mean() * 100:+.3f}% · 앞 {leg[c].iloc[:h].mean() * 100:+.3f}% · 뒤 {leg[c].iloc[h:].mean() * 100:+.3f}%")
print(f"  {'합계':20} 전체 {leg.sum(axis=1).mean() * 100:+.3f}%")
print("\n끝. 출력 전체를 붙여 주세요.")
