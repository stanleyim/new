"""
공정한 풀(PIT) ML 섀도 — 정답 자동 부착 + 사전 고정 지표 집계.
사용: python shadow_pit_outcomes.py --data <pit_data 폴더> [--out output/shadow_pit]
- <out>/YYYY-MM-DD.csv 기록 각각에 대해, 20일/60일이 지나 수익이 확정된 것을 <out>/outcomes.csv 에 추가(덮어쓰기·수정 없음).
  수익률 = 수정주가 T+1 시가 진입 → T+21(20일) / T+61(60일) 종가 청산 (거래일 기준, T = 기록 날짜). pit_feat.py 의 fret 정의와 같다.
  청산일 전에 거래가 끝난 종목(상장폐지)은 마지막 종가로 고정(백테스트와 같은 처리), exit_frozen=1 로 표시한다.
  진입 시가가 없거나 0 이면 수익률 공란.
- 지표(SHADOW_PIT_CRITERIA.md 에 사전 고정): 날짜별 순위 IC(스피어만), 예측 상위 10% 의 시장 대비 평균 초과수익,
  상위 20% - 하위 20% 스프레드(참고). 시장 = 그날 기록된 풀 전체 동일가중. 결과는 <out>/summary.json 과 화면에 낸다.
"""
import argparse
import glob
import json
import os
import sys

import numpy as np
import pandas as pd

import pit_lib as L

HS = (20, 60)
MIN_N = 30          # IC 를 내려면 그날 정답 있는 종목이 최소 이 수


def load_records(out):
    fs = sorted(glob.glob(f'{out}/[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].csv'))
    if not fs:
        return pd.DataFrame()
    r = pd.concat([pd.read_csv(f, dtype={'ticker': str}) for f in fs], ignore_index=True)
    r['date'] = pd.to_datetime(r['date'])
    return r


def attach(D, out, rec):
    """새로 확정된 (날짜, 기간) 정답을 outcomes.csv 에 추가하고 추가한 행 수를 돌려준다."""
    path = f'{out}/outcomes.csv'
    done = set()
    if os.path.exists(path):
        old = pd.read_csv(path, dtype={'ticker': str}, usecols=['date', 'h'])
        done = set(zip(pd.to_datetime(old['date']), old['h']))
    cal = pd.Index(sorted(L.rd(D, 'ohlcv', ['종가'])['date'].unique()))
    pos = {d: i for i, d in enumerate(cal)}
    todo = []                                    # (date, h)
    for d in sorted(rec['date'].unique()):
        d = pd.Timestamp(d)
        if d not in pos:
            raise SystemExit(f'::error::기록 날짜 {d.date()} 가 데이터에 없다')
        for h in HS:
            if (d, h) not in done and pos[d] + h + 1 <= len(cal) - 1:
                todo.append((d, h))
    if not todo:
        return 0
    o = L.adjusted_ohlcv(D, rec['ticker'].unique())
    P = lambda c: o.pivot(index='date', columns='ticker', values=c).reindex(cal)
    cl, op = P('종가'), P('시가')
    clf = cl.ffill()                              # 상장폐지 종목: 마지막 종가 고정
    rows = []
    for d, h in todo:
        i = pos[d]
        ex = cal[i + h + 1]
        tick = rec.loc[rec['date'] == d, 'ticker'].tolist()
        entry = op.iloc[i + 1].reindex(tick)
        exitp = clf.iloc[i + h + 1].reindex(tick)
        frozen = cl.iloc[i + h + 1].reindex(tick).isna() & exitp.notna()
        fret = (exitp / entry - 1).replace([np.inf, -np.inf], np.nan)
        rows.append(pd.DataFrame({'date': d.date().isoformat(), 'ticker': tick, 'h': h,
                                  'exit_date': ex.date().isoformat(), 'fret': fret.values,
                                  'exit_frozen': frozen.astype(int).values}))
    new = pd.concat(rows, ignore_index=True)
    new.to_csv(path, mode='a', header=not os.path.exists(path), index=False)
    return len(new)


def rank_ic(p, r):
    return pd.Series(p).rank().corr(pd.Series(r).rank())


def summarize(out, rec):
    path = f'{out}/outcomes.csv'
    res = {'records': int(rec['date'].nunique()), 'first': str(rec['date'].min().date()), 'last': str(rec['date'].max().date())}
    if not os.path.exists(path):
        return res
    oc = pd.read_csv(path, dtype={'ticker': str})
    oc['date'] = pd.to_datetime(oc['date'])
    for h in HS:
        m = rec.merge(oc[oc['h'] == h][['date', 'ticker', 'fret', 'exit_frozen']], on=['date', 'ticker'], how='inner')
        tot = len(m)
        m = m[m['fret'].notna()]
        per = []
        for d, g in m.groupby('date'):
            n = len(g)
            if n < MIN_N:
                continue
            g = g.sort_values(f'rank{h}')
            mkt = g['fret'].mean()
            k10, k20 = int(np.ceil(0.10 * n)), int(np.ceil(0.20 * n))
            per.append({'date': d, 'n': n, 'ic': rank_ic(g[f'p{h}'], g['fret']), 'mkt': mkt,
                        'top10_excess': g['fret'].iloc[:k10].mean() - mkt,
                        'spread': g['fret'].iloc[:k20].mean() - g['fret'].iloc[-k20:].mean()})
        p = pd.DataFrame(per)
        s = {'dates_with_outcome': int(len(p)), 'rows': int(tot), 'rows_missing_fret': int(tot - len(m)),
             'rows_exit_frozen': int(m['exit_frozen'].sum()) if len(m) else 0,
             'approx_independent_windows': round(len(p) / h, 1)}
        if len(p):
            s.update({'ic_mean': float(p['ic'].mean()), 'top10_excess_mean': float(p['top10_excess'].mean()),
                      'spread_mean': float(p['spread'].mean()), 'mkt_mean': float(p['mkt'].mean())})
        res[f'h{h}'] = s
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', required=True)
    ap.add_argument('--out', default='output/shadow_pit')
    args = ap.parse_args()
    rec = load_records(args.out)
    if rec.empty:
        print('기록 없음 — 할 일 없음')
        return 0
    n = attach(args.data, args.out, rec)
    print(f'정답 추가 {n}행')
    s = summarize(args.out, rec)
    with open(f'{args.out}/summary.json', 'w', encoding='utf-8') as fh:
        json.dump(s, fh, ensure_ascii=False, indent=1)
    print(json.dumps(s, ensure_ascii=False, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
