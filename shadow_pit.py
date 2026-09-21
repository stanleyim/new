"""
공정한 풀(PIT) ML 섀도 기록기 — 매일 저녁 1회.
사용: python shadow_pit.py --data <pit_data 폴더> [--out output/shadow_pit]
- 데이터의 마지막 거래일 T 의 풀 종목에 대해 20일·60일 목표 ML 예측을 <out>/T.csv 로 저장한다.
  컬럼: date, ticker, name, p20, p60, rank20, rank60, train_cutoff20, train_cutoff60  (rank 1 = 예측 최상위)
  name(종목명)은 보기 위한 열이며 예측·판정에 쓰이지 않는다(2026-09-22 추가, 2026-09-21 기록에는 없음).
- 이미 그 날짜 파일이 있으면 아무것도 하지 않는다(덮어쓰지 않는다).
- 다음 영업일 09:00 KST 이후에 만들 수 없는 기록이면 만들지 않고 실패한다("장 마감 후·다음 시가 전" 기록만 유효).
- 모델·풀·피처는 pit_lib.py (변경 금지). 예측은 T+1 시가 진입을 가정한다.
종료 코드: 0 기록함/이미 있음, 1 오류·무효.
"""
import argparse
import datetime as dt
import os
import sys
from zoneinfo import ZoneInfo

import pandas as pd

import pit_lib as L

KST = ZoneInfo('Asia/Seoul')
MIN_POOL, MAX_POOL = 150, 500          # 실측 일평균 317종목
MAX_HOLE_DAYS = 7                      # 최근 60일 안에 이보다 긴 거래일 공백이 있으면 데이터 결함
MAX_NAN_FRAC = 0.20                    # T 의 수급·공매도 피처 결측 허용 비율


def deadline_for(T):
    """T 다음 영업일(주말 제외) 09:00 KST. 공휴일은 무시한다(다음 날이 휴일이면 실제 다음 시가보다 마감이 빨라져 더 엄격하므로 안전)."""
    n = T + dt.timedelta(days=1)
    while n.weekday() >= 5:
        n += dt.timedelta(days=1)
    return dt.datetime(n.year, n.month, n.day, 9, 0, tzinfo=KST)


def load_names(tickers, universe_path):
    """종목명 조회(표시용). pykrx → data/universe.parquet 순. 실패해도 기록은 계속한다(빈 이름)."""
    names = {}
    try:
        from pykrx import stock
        fails = 0
        for t in tickers:
            try:
                n = stock.get_market_ticker_name(t)
            except Exception:
                n = None
            if isinstance(n, str) and n.strip():
                names[t] = n.strip()
                fails = 0
            else:
                fails += 1
                if fails >= 5 and not names:      # 처음부터 계속 실패 → 조회 포기
                    break
    except Exception as e:
        print(f'::warning::pykrx 종목명 조회 실패: {repr(e)[:120]}')
    miss = [t for t in tickers if t not in names]
    if miss and os.path.exists(universe_path):
        try:
            u = pd.read_parquet(universe_path, columns=['ticker', 'name'])
            um = dict(zip(u['ticker'].astype(str), u['name']))
            for t in miss:
                if isinstance(um.get(t), str) and um[t].strip():
                    names[t] = um[t].strip()
        except Exception as e:
            print(f'::warning::universe 종목명 읽기 실패: {repr(e)[:120]}')
    miss = [t for t in tickers if t not in names]
    if miss:
        print(f'::warning::종목명 없음 {len(miss)}개 (빈 칸으로 기록): {miss[:8]}')
    return names


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', required=True)
    ap.add_argument('--out', default='output/shadow_pit')
    ap.add_argument('--universe', default='data/universe.parquet', help='종목명 보조 출처')
    ap.add_argument('--now', default=None, help='테스트용: ISO 시각(KST). 기본: 지금')
    args = ap.parse_args()
    now = dt.datetime.fromisoformat(args.now).replace(tzinfo=KST) if args.now else dt.datetime.now(KST)

    dates = pd.Series(sorted(L.rd(args.data, 'ohlcv', ['종가'])['date'].unique()))
    T = pd.Timestamp(dates.iloc[-1])
    path = f'{args.out}/{T.date()}.csv'
    if os.path.exists(path):
        print(f'{T.date()} 기록이 이미 있다 — 건너뜀')
        return 0
    if now >= deadline_for(T.date()):
        print(f'::error::LATE — {T.date()} 기록은 {deadline_for(T.date())} 이전에 만들어야 한다(지금 {now}). 기록하지 않는다.')
        return 1
    recent = dates[dates >= T - pd.Timedelta(days=60)]
    if recent.diff().dt.days.max() > MAX_HOLE_DAYS:
        print(f'::error::최근 60일 안에 거래일 공백 {int(recent.diff().dt.days.max())}일 — 데이터 결함 의심, 기록 중단.')
        return 1

    tk, member = L.universe(args.data)
    X, mk, cl, op = L.build_features(args.data, tk, member)
    if X.index.get_level_values(0).max() != T:
        print(f'::error::마지막 피처 날짜 {X.index.get_level_values(0).max().date()} ≠ 데이터 마지막 날짜 {T.date()}')
        return 1
    XT = X.xs(T, level=0)
    n = len(XT)
    if not (MIN_POOL <= n <= MAX_POOL):
        print(f'::error::풀 종목 수 {n}가 정상 범위({MIN_POOL}~{MAX_POOL})를 벗어남 — 기록 중단.')
        return 1
    bad = XT[['frgn1', 'inst1', 'indv1', 'short_ratio5']].isna().mean().max()
    if bad > MAX_NAN_FRAC:
        print(f'::error::T 의 수급·공매도 피처 결측 {bad:.0%} — 자료 불완전, 기록 중단.')
        return 1
    del XT

    res, cuts = L.predict_date(X, mk, cl, op, T)
    res = res.sort_index()
    names = load_names(list(res.index.astype(str)), args.universe)
    out = pd.DataFrame({
        'date': T.date().isoformat(),
        'ticker': res.index.astype(str),
        'name': [names.get(t, '') for t in res.index.astype(str)],
        'p20': res['p20'].values,
        'p60': res['p60'].values,
        'rank20': res['p20'].rank(ascending=False, method='first').astype(int).values,
        'rank60': res['p60'].rank(ascending=False, method='first').astype(int).values,
        'train_cutoff20': cuts[20].isoformat(),
        'train_cutoff60': cuts[60].isoformat(),
    })
    os.makedirs(args.out, exist_ok=True)
    tmp = f'{args.out}/.{T.date()}.tmp'
    out.to_csv(tmp, index=False)
    os.replace(tmp, path)
    print(f'기록: {path} ({len(out)}종목, 학습 종료 {cuts[20]} / {cuts[60]}, 실행 {now:%Y-%m-%d %H:%M} KST)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
