# [낙폭(MDD) 분해 셀 — 실제 운용 구조 W20 경로] 읽기 전용·측정 전용(판정 없음)
# 입력: sig_base_cell.py가 저장한 equity_W20.csv·trades_W20.csv(Drive new_sigbase_out), 지수 idx_*.parquet, universe.parquet(종목명).
# 질문: 큰 낙폭(<= -20%) 구간이 언제·얼마나 깊었는지, 같은 기간 지수는 어땠는지(시장 β·R²), 낙폭 구간에 청산된 거래 중 어떤 종목이 손실을 만들었는지.
# "자산 대비" = 거래 손익 / 신호일 종가 시점 자산. 한계: 종목별 일 단위 손익은 저장돼 있지 않아 "구간 내 청산된 거래" 기준으로 귀속(구간 말 보유 중 포지션 손익은 제외). 비용 0.206% 반영 후 수익률.
import os
import numpy as np
import pandas as pd

BASE_DIR = "/content/drive/MyDrive/new_sigbase_out"
MAIN_DIR = "/content/main"
PIT_EXTRA = "/content/pit/pit_data_extra"
IDX_NAMES = ("KOSPI200", "KOSDAQ")
HMAX = 10
MIN_DEPTH = -0.20
TOP_EP = 5


# ===== 원문 복사(g2_hedge.py) =====
def load_idx_returns(pit_extra_dir, cal):
    """cal(DatetimeIndex, 마스터 거래일) 위 지수 종가→일수익률 (N×2, 열=IDX_NAMES). 결측·중복·이름 부재는 즉시 중단.
    첫 행은 이전 종가가 없어 0으로 두며(평가에서 미사용 assert), 이후 행은 직전 캘린더일 종가 대비."""
    cal = pd.DatetimeIndex(cal)
    frames = [pd.read_parquet(f"{pit_extra_dir}/idx_{y}.parquet", columns=["date", "index", "종가"])
              for y in range(cal[0].year, cal[-1].year + 1)]
    d = pd.concat(frames, ignore_index=True)
    assert not d.duplicated(["date", "index"]).any(), "idx (date,index) 중복"
    px = d.pivot(index="date", columns="index", values="종가")
    for n in IDX_NAMES:
        assert n in px.columns, f"지수 {n} 없음: {list(px.columns)}"
    px = px[list(IDX_NAMES)].reindex(cal)
    assert px.notna().all().all(), "마스터 거래일 중 지수 종가 결측"
    assert (px.values > 0).all(), "지수 종가 <= 0"
    r = np.array(px.pct_change().values, dtype=float)
    r[0] = 0.0
    return r


def find_episodes(eq, first, min_depth=MIN_DEPTH):
    """first 이후 자산곡선의 낙폭 에피소드(고점->신고점 회복 전까지). 반환 list[dict]."""
    eps = []; peak_i = first; peak_v = eq[first]; trough_i = first; trough_v = eq[first]
    for i in range(first + 1, len(eq)):
        if eq[i] >= peak_v:
            if trough_v / peak_v - 1.0 <= min_depth:
                eps.append(dict(peak=peak_i, trough=trough_i, rec=i, depth=trough_v / peak_v - 1.0))
            peak_i, peak_v, trough_i, trough_v = i, eq[i], i, eq[i]
        elif eq[i] < trough_v:
            trough_i, trough_v = i, eq[i]
    if trough_v / peak_v - 1.0 <= min_depth:
        eps.append(dict(peak=peak_i, trough=trough_i, rec=None, depth=trough_v / peak_v - 1.0))
    return sorted(eps, key=lambda e: e["depth"])


def beta_r2(y, x):
    X = np.column_stack([np.ones(len(x)), x])
    coef = np.linalg.lstsq(X, y, rcond=None)[0]
    res = y - X @ coef
    r2 = 1.0 - res.var() / y.var() if y.var() > 0 else np.nan
    return float(coef[1]), float(r2)


def analyze(eqdf, tr, ridx, names, P=print):
    cal = pd.DatetimeIndex(pd.to_datetime(eqdf["date"]))
    eq, ret, npos = eqdf["eq"].values, eqdf["ret"].values, eqdf["npos"].values
    first = int(np.argmax(npos > 0))
    N = len(eq)
    assert ridx.shape[0] == N, "지수 캘린더 길이 불일치"
    tr = tr.copy()
    tr["entry_sig_date"] = [cal[int(e)] for e in tr["E"]]
    tr["exit_idx"] = tr["E"].astype(int) + HMAX
    tr["name"] = tr["ticker"].astype(str).str.zfill(6).map(names).fillna("?")
    P(f"평가구간 {cal[first].date()} ~ {cal[N-1].date()} · 거래 {len(tr)}건 · 보유 5종목(한도 도달) 일 비중 {np.mean(npos[first:] >= 5):.3f}")
    b_all, r2_all = beta_r2(ret[first + 1:], ridx[first + 1:, 0])
    P(f"전 구간 일수익률 대비 KOSPI200: β {b_all:.2f} · R² {r2_all:.3f}")
    # 거래 전체 손실 분포
    big = tr[tr["net_ret"] <= -0.20]
    P(f"\n[거래 분포] 평균 {tr['net_ret'].mean():+.4f} · 중앙값 {tr['net_ret'].median():+.4f} · 승률 {(tr['net_ret']>0).mean():.3f} · "
      f"-20% 이하 {len(big)}건({len(big)/len(tr):.3f}) · 하위 5% 분위 {tr['net_ret'].quantile(0.05):+.3f} · 최악 {tr['net_ret'].min():+.3f}")
    P("[전체 최악 거래 10건: 신호일 · 종목 · 순수익률 · 자산 대비 손익]")
    tr["pnl_eq"] = [(p - a) / eq[max(int(e) , first)] for p, a, e in zip(tr["proceeds"], tr["alloc"], tr["E"])]
    for _, r in tr.nsmallest(10, "net_ret").iterrows():
        P(f"   {r['entry_sig_date'].date()} {r['ticker']} {r['name']}  {r['net_ret']:+.3f}  자산대비 {r["pnl_eq"]:+.3f}")
    eps = find_episodes(eq, first)
    P(f"\n[낙폭 {MIN_DEPTH:.0%} 이하 에피소드 {len(eps)}개 중 깊은 {min(TOP_EP, len(eps))}개]")
    out = []
    for k, e in enumerate(eps[:TOP_EP], 1):
        pk, tf, rc = e["peak"], e["trough"], e["rec"]
        idx_ret = [float(np.prod(1 + ridx[pk + 1:tf + 1, j]) - 1) for j in range(ridx.shape[1])]
        b, r2 = beta_r2(ret[pk + 1:tf + 1], ridx[pk + 1:tf + 1, 0])
        P(f"\n #{k} 낙폭 {e['depth']:+.3f} · 고점 {cal[pk].date()} -> 저점 {cal[tf].date()} ({tf-pk}거래일) · 회복 "
          f"{cal[rc].date() if rc is not None else '미회복'}")
        P(f"    같은 기간 KOSPI200 {idx_ret[0]:+.3f} KOSDAQ {idx_ret[1]:+.3f} · 일수익률 대비 KOSPI200 β {b:.2f} R² {r2:.3f} · "
          f"기간 평균 보유 {npos[pk:tf+1].mean():.2f}종목 · 저점일 보유 {int(npos[tf])}종목")
        w = tr[(tr["exit_idx"] > pk) & (tr["exit_idx"] <= tf)]
        if len(w):
            tot = w["pnl_eq"].sum(); loss = w[w["pnl_eq"] < 0]["pnl_eq"].sum(); win = w[w["pnl_eq"] > 0]["pnl_eq"].sum()
            P(f"    구간 내 청산 거래 {len(w)}건(승 {(w['pnl_eq']>0).sum()}/패 {(w['pnl_eq']<0).sum()}) · 자산 대비 손익 합 {tot:+.3f} "
              f"(이익 {win:+.3f} / 손실 {loss:+.3f})")
            worst = w.nsmallest(5, "pnl_eq")
            P(f"    손실 상위 5건이 구간 내 손실의 {worst['pnl_eq'].sum() / loss:.2f} 비중:" if loss < 0 else "    손실 거래 없음")
            for _, r in worst.iterrows():
                P(f"       {r['entry_sig_date'].date()} {r['ticker']} {r['name']}  {r['net_ret']:+.3f}  자산대비 {r["pnl_eq"]:+.3f}")
        out.append(dict(rank=k, depth=e["depth"], peak=str(cal[pk].date()), trough=str(cal[tf].date()),
                        rec=str(cal[rc].date()) if rc is not None else None, kospi200=idx_ret[0], kosdaq=idx_ret[1],
                        beta=b, r2=r2))
    return out, tr


def main():
    eqdf = pd.read_csv(f"{BASE_DIR}/equity_W20.csv")
    tr = pd.read_csv(f"{BASE_DIR}/trades_W20.csv", dtype={"ticker": str})
    cal = pd.DatetimeIndex(pd.to_datetime(eqdf["date"]))
    ridx = load_idx_returns(PIT_EXTRA, cal)
    try:
        u = pd.read_parquet(f"{MAIN_DIR}/data/universe.parquet")
        names = dict(zip(u["ticker"].astype(str).str.zfill(6), u["name"]))
    except Exception as e:
        print(f"종목명 로드 실패({e}) -> 티커만 표시"); names = {}
    out, trd = analyze(eqdf, tr, ridx, names)
    pd.DataFrame(out).to_csv(f"{BASE_DIR}/dd_episodes_W20.csv", index=False)
    trd.to_csv(f"{BASE_DIR}/trades_W20_named.csv", index=False)
    print(f"\n저장: {BASE_DIR}/dd_episodes_W20.csv, trades_W20_named.csv")


if os.environ.get("SIGDD_NO_MAIN") != "1":
    main()
