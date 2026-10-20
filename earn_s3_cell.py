# [새 정보원 4단계 셀] 잠정실적 영업이익 서프라이즈가 이후 10거래일 초과수익을 예측하는가 — 설계구간(2018-01-02~2022-12-30)만. 읽기 전용·레포 무변경.
# 입력: Drive new_earn_s2/parse_all.csv(earn_s2 결과) + /content/main/data/ohlcv_full.parquet(없으면 main 얕은 클론).
# 사전 고정(10-20 Stanley 승인, 결과 보기 전 확정):
#  표본 = 범위 안 ∧ 성공(검산 일치) ∧ 검산 가능(검산 불가 8건 제외: 전년동기 0·1개월 기간) ∧ 전년동기 ≠ 0 ∧ 매출액 > 0 ∧ 439 일봉 있음
#         같은 종목·같은 접수일 중복은 공시명 '연결' 우선 → rcept_no 빠른 것 1건
#  서프라이즈 S = (당기 영업이익 − 전년동기 영업이익) ÷ 당기 매출액 (같은 문서 숫자끼리, 단위 무관)
#  수익 = 접수일 다음 거래일(E) 종가 매수 → E+10거래일 종가. 초과수익 = 같은 구간 유니버스(일봉 있는 전 종목) 평균 차감
#  단차 제외 = 구간(접수 전 거래일 ~ E+10) 안에 하루 |수익| > 30.5%(가격제한폭 초과 = 미조정 단차)면 그 이벤트 제외. 유니버스 평균도 같은 기준으로 해당 종목 제외
#  청산일(E+10)이 2022-12-30 이후면 제외(설계구간 밖 가격 사용 금지)
#  분기 = 접수일의 달력 분기, 분기마다 그 분기 안에서만 S 순위로 5분위(미래 정보 차단). 이벤트 50건 미만 분기는 제외
#  판정 = 분기별 (상위 20% − 하위 20%) 초과수익 평균 > 0.206% ∧ 분기 기준 t ≥ 2 → 통과, 아니면 DART 정보원 종료
#  참고(판정 아님): 상위−전체·하위−전체(롱온리 관점), 5분위 평균, 분기별 순위상관, 공시 직후 반응(접수 전 거래일 종가→E 종가), 이벤트 단위 t(부풀림 가능)
import os, subprocess, time
import numpy as np
import pandas as pd

S2 = os.environ.get("EARN_S2_OUT", "/content/drive/MyDrive/new_earn_s2")
OUT = os.environ.get("EARN_S3_OUT", "/content/drive/MyDrive/new_earn_s3")
MAIN = os.environ.get("EARN_MAIN", "/content/main")
D0, D1 = pd.Timestamp("2018-01-02"), pd.Timestamp("2022-12-30")
H, JUMP, COST, MIN_Q = 10, 0.305, 0.00206, 50


def tk(s):
    return s.astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)


def tstat(v):
    v = np.asarray(v, float)
    return v.mean() / (v.std(ddof=1) / np.sqrt(len(v))) if len(v) > 1 and v.std(ddof=1) > 0 else np.nan


def run():
    t0 = time.time()
    os.makedirs(OUT, exist_ok=True)
    if not os.path.exists(f"{MAIN}/data/ohlcv_full.parquet"):
        subprocess.run(f"rm -rf {MAIN}; git clone -q --depth 1 --single-branch -b main https://github.com/stanleyim/new.git {MAIN}",
                       shell=True, check=True)
    print("main", subprocess.run(f"git -C {MAIN} log -1 --format='%h %ci'", shell=True, capture_output=True, text=True).stdout.strip())

    # ---- 가격: 종가 피벗, 일별 수익, 단차 누적 카운트
    o = pd.read_parquet(f"{MAIN}/data/ohlcv_full.parquet", columns=["date", "ticker", "종가"])
    o["date"] = pd.to_datetime(o["date"]); o["ticker"] = tk(o["ticker"])
    assert not o.duplicated(["date", "ticker"]).any(), "ohlcv (date,ticker) 중복"
    C = o.pivot(index="date", columns="ticker", values="종가").sort_index()
    C = C[C.index <= D1]
    cal = C.index.values
    Cv = C.values.astype(float)
    Cv[Cv <= 0] = np.nan
    R1 = np.full_like(Cv, np.nan); R1[1:] = Cv[1:] / Cv[:-1] - 1
    bad = (np.abs(np.nan_to_num(R1)) > JUMP).astype(int)
    CB = np.vstack([np.zeros((1, Cv.shape[1]), int), np.cumsum(bad, axis=0)])  # CB[i+1] = i일까지 단차 수
    col = {t: j for j, t in enumerate(C.columns)}
    print(f"일봉 {len(cal):,}일 × {Cv.shape[1]}종목 ({pd.Timestamp(cal[0]).date()}~{pd.Timestamp(cal[-1]).date()}) · 하루 |수익|>30.5% {int(bad.sum())}칸")

    def uni(a, b):
        """유니버스 평균 수익 (a일 종가 → b일 종가), a~b 사이 단차 있는 종목 제외."""
        r = Cv[b] / Cv[a] - 1
        ok = (CB[b + 1] - CB[a + 1] == 0) & np.isfinite(r)
        return r[ok].mean()

    # ---- 공시 표본
    P = pd.read_csv(f"{S2}/parse_all.csv", dtype={"rcept_no": str, "rcept_dt": str, "stock_code": str})
    for c in ("ok", "범위", "checked"):
        P[c] = P[c].astype(str) == "True"
    P["dt"] = pd.to_datetime(P["rcept_dt"].str.replace("-", ""), format="%Y%m%d")
    P["ticker"] = tk(P["stock_code"])
    steps = [("parse_all 전체", len(P))]
    P = P[P["범위"] & P["ok"]]; steps.append(("범위 안·성공", len(P)))
    P = P[P["checked"]]; steps.append(("검산 가능(검산 불가 성공 제외)", len(P)))
    P = P[(P["영업이익_yoy"] != 0) & (P["매출액_cur"] > 0)]; steps.append(("전년동기≠0·매출액>0", len(P)))
    P = P[(P["dt"] >= D0) & (P["dt"] <= D1)]; steps.append(("설계구간 접수", len(P)))
    P = P[P["ticker"].isin(col)]; steps.append(("439 일봉 있음", len(P)))
    P = P.assign(cons=P["report_nm"].fillna("").str.replace(" ", "").str.contains("연결")) \
         .sort_values(["ticker", "dt", "cons", "rcept_no"], ascending=[True, True, False, True]) \
         .drop_duplicates(["ticker", "dt"], keep="first")
    steps.append(("같은 종목·같은 날 중복 정리(연결 우선)", len(P)))
    P["S"] = (P["영업이익_cur"] - P["영업이익_yoy"]) / P["매출액_cur"]

    # ---- 이벤트 수익
    recs, drop = [], {"청산일 설계구간 밖": 0, "접수 전 거래일 없음": 0, "가격 없음": 0, "단차": 0}
    for x in P.itertuples():
        e = int(np.searchsorted(cal, np.datetime64(x.dt), side="right"))   # 접수일 다음 거래일
        pv = int(np.searchsorted(cal, np.datetime64(x.dt), side="left")) - 1  # 접수일 전 거래일
        z = e + H
        if z >= len(cal):
            drop["청산일 설계구간 밖"] += 1; continue
        if pv < 0:
            drop["접수 전 거래일 없음"] += 1; continue
        j = col[x.ticker]
        seg = Cv[pv:z + 1, j]
        if not np.isfinite(seg).all():
            drop["가격 없음"] += 1; continue
        if CB[z + 1, j] - CB[pv + 1, j] > 0:
            drop["단차"] += 1; continue
        r = Cv[z, j] / Cv[e, j] - 1
        rr = Cv[e, j] / Cv[pv, j] - 1
        recs.append({"rcept_no": x.rcept_no, "ticker": x.ticker, "corp_name": x.corp_name, "dt": x.dt,
                     "E": pd.Timestamp(cal[e]), "X": pd.Timestamp(cal[z]), "S": x.S,
                     "op_cur": x.영업이익_cur, "op_yoy": x.영업이익_yoy, "sales": x.매출액_cur,
                     "ret": r, "uni": uni(e, z), "react": rr, "uni_react": uni(pv, e)})
    for k, v in drop.items():
        steps.append((f"제외: {k}", -v))
    Ev = pd.DataFrame(recs)
    Ev["ex"] = Ev["ret"] - Ev["uni"]
    Ev["ex_react"] = Ev["react"] - Ev["uni_react"]
    Ev["q"] = Ev["dt"].dt.to_period("Q").astype(str)
    print("\n[표본 단계]\n" + "\n".join(f"  {k}: {v:,}" for k, v in steps) + f"\n  이벤트 최종: {len(Ev):,}")

    # ---- 분기별 5분위
    qrows, used = [], []
    for q, g in Ev.groupby("q"):
        if len(g) < MIN_Q:
            qrows.append({"분기": q, "n": len(g), "사용": False}); continue
        g = g.copy()
        g["k"] = pd.qcut(g["S"].rank(method="first"), 5, labels=[1, 2, 3, 4, 5]).astype(int)
        used.append(g)
        m = g.groupby("k")["ex"].mean()
        qrows.append({"분기": q, "n": len(g), "사용": True, "상위-하위": m[5] - m[1], "상위-전체": m[5] - g["ex"].mean(),
                      "하위-전체": m[1] - g["ex"].mean(), "순위상관": g["S"].corr(g["ex"], method="spearman"),
                      **{f"Q{k}": m[k] for k in range(1, 6)}})
    Q = pd.DataFrame(qrows)
    U = Q[Q["사용"]]
    with pd.option_context("display.width", 250, "display.max_columns", 20, "display.float_format", lambda v: f"{v:.4f}"):
        print("\n[분기별] (초과수익, 소수 = 비율: 0.0100 = 1%)\n" + Q.to_string(index=False))

    sp = U["상위-하위"]
    mean, t = sp.mean(), tstat(sp)
    print(f"\n사용 분기 {len(U)}개 · 이벤트 {int(U['n'].sum()):,}")
    print(f"[판정 지표] 분기별 상위−하위 평균 {100*mean:+.3f}% · 분기 기준 t {t:.2f} · 플러스 분기 {int((sp > 0).sum())}/{len(sp)}")
    for c in ("상위-전체", "하위-전체", "순위상관"):
        v = U[c]
        print(f"[참고] {c}: 분기 평균 {(100*v.mean() if c != '순위상관' else v.mean()):+.3f}{'%' if c != '순위상관' else ''} · t {tstat(v):.2f}")
    print("[참고] 5분위 분기 평균 초과수익(Q1 하위 → Q5 상위): " + " · ".join(f"Q{k} {100*U[f'Q{k}'].mean():+.3f}%" for k in range(1, 6)))
    A = pd.concat(used)
    rq = A.groupby("k")["ex_react"].mean()
    print("[참고] 공시 직후 반응(접수 전 거래일 종가 → E 종가, 초과): " + " · ".join(f"Q{k} {100*rq[k]:+.3f}%" for k in range(1, 6)))
    pooled = A.loc[A["k"] == 5, "ex"].mean() - A.loc[A["k"] == 1, "ex"].mean()
    se = np.sqrt(A.loc[A["k"] == 5, "ex"].var() / (A["k"] == 5).sum() + A.loc[A["k"] == 1, "ex"].var() / (A["k"] == 1).sum())
    print(f"[참고·부풀림 가능] 이벤트 단위 상위−하위 {100*pooled:+.3f}% · t {pooled/se:.2f} (같은 시기 표본을 독립으로 본 값)")
    print(f"[참고] S 분포: " + " · ".join(f"p{p} {np.percentile(A['S'], p):+.4f}" for p in (1, 5, 25, 50, 75, 95, 99)))

    passed = mean > COST and t >= 2
    print(f"\n4단계 판정: {'통과' if passed else '미달 → DART 정보원 종료'} (기준: 분기 평균 > {100*COST:.3f}% → {100*mean:+.3f}% · t ≥ 2 → {t:.2f})")

    print("\n[수기 대조용 예시 3건]")
    for x in A.sample(3, random_state=20261020).itertuples():
        j = col[x.ticker]
        e, z = int(np.searchsorted(cal, np.datetime64(x.E))), int(np.searchsorted(cal, np.datetime64(x.X)))
        print(f"  {x.corp_name}({x.ticker}) 접수 {x.dt.date()} · 영업이익 {x.op_cur:,.0f}/{x.op_yoy:,.0f} · 매출 {x.sales:,.0f} → S {x.S:+.4f} · "
              f"E {x.E.date()} 종가 {Cv[e, j]:,.0f} → X {x.X.date()} 종가 {Cv[z, j]:,.0f} = {100*x.ret:+.2f}% · 유니버스 {100*x.uni:+.2f}% · 초과 {100*x.ex:+.2f}%")
    A.to_csv(f"{OUT}/events_design.csv", index=False, encoding="utf-8-sig")
    Q.to_csv(f"{OUT}/quarters_design.csv", index=False, encoding="utf-8-sig")
    print(f"\n저장: {OUT}/events_design.csv · quarters_design.csv · 소요 {time.time()-t0:.0f}s")
    return passed, Q, A


if os.environ.get("EARN_S3_NO_MAIN") != "1":
    try:
        from google.colab import drive
        drive.mount("/content/drive")
    except Exception:
        pass
    S3_RESULT = run()
