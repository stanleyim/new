# new3_off_read_cell.py — 읽기 전용, 재계산 없음 (10-21 New3 실행 때 Drive에 저장된 결과만 읽음)
# 목적: 판정에 안 쓴 '시장 룰 OFF'(매수 중단 없이 F2 그대로) 성과와 선별력 D20을 세 구간·연도별로 확인
import os, json
import pandas as pd

P = "/content/drive/MyDrive/new3_s1"
if not os.path.exists(P):
    from google.colab import drive
    drive.mount("/content/drive")
print("파일:", sorted(os.listdir(P)))

s = json.load(open(f"{P}/summary.json", encoding="utf-8"))
f = lambda m: f"CAGR {m['CAGR']:+.1%} · Sharpe {m['Sharpe']:.2f} · MDD {m['MDD']:.1%}"
print("\n[구간별] ON = 시장 룰 켬(판정), OFF = 시장 룰 끔, 지수 = D450 동일가중 지수")
for name, r in s["segments"].items():
    print(f"■ {name} {r['seg'][0]}~{r['seg'][1]}")
    print(f"   ON   {f(r['on'])}  · 매수 {r['st_on']['buys']} · 매수 중단일 {r['st_on']['blocked_days']}")
    print(f"   OFF  {f(r['off'])}  · 매수 {r['st_off']['buys']} · 폐지 −100% {r['st_off']['writeoff'] + r['st_off']['writeoff_end']}")
    print(f"   지수 {f(r['idx'])}")

e = pd.read_csv(f"{P}/equity_daily.csv", parse_dates=["date"])
print("\n[연도별 수익] OFF vs 지수")
for name, g in e.groupby("구간", sort=False):
    g = g.sort_values("date").reset_index(drop=True)
    prev_v, prev_i = 1.0, 1.0
    for y, gy in g.groupby(g["date"].dt.year):
        v, i = gy["V_off"].iloc[-1], gy["index"].iloc[-1]
        print(f"   {name} {y}: OFF {v / prev_v - 1:+.1%} · 지수 {i / prev_i - 1:+.1%} · 차이 {(v / prev_v) - (i / prev_i):+.1%}p")
        prev_v, prev_i = v, i

q = pd.read_csv(f"{P}/d20_quarterly.csv")
print("\n[D20 선별력] F2 상위 20% − D450 평균, 20일 수익(비용 전), 분기 평균")
for name, g in q.groupby("구간", sort=False):
    print(f"   {name}: 평균 {g['D'].mean():+.3%} · 플러스 분기 {(g['D'] > 0).sum()}/{len(g)} · 최저 {g['D'].min():+.2%} · 최고 {g['D'].max():+.2%}")
print("\n끝. 출력 전체를 붙여 주세요.")
