"""
KRX 추가 자료 수집기 (연도 단위) — 성능 개선 연구용. 기존 pit_data 는 읽지도 쓰지도 않는다.
사용: python pit_extra.py 2019
거래일 목록은 코스피 지수 일봉(1회 호출)에서 얻는다.
매 거래일:
  fund   전종목 PER/PBR/EPS/BPS/배당수익률/DPS
  forgn  전종목 외국인 보유수량·지분율·한도소진율
  sbal   공매도 잔고 (KOSPI·KOSDAQ)
  inv    세부 투자자 순매수거래대금 8종(금융투자·보험·투신·사모·은행·기타금융·연기금·기타외국인)
매월 첫 거래일: sector  업종 분류(KOSPI·KOSDAQ)
연 1회: idx  코스피(1001)·코스닥(2001)·코스피200(1028) 일봉
출력: $PIT_OUT(기본 pit_out_extra)/{fund,forgn,sbal,inv,sector,idx}_YYYY.parquet, failed_extra_YYYY.txt, extra_summary_YYYY.txt
환경변수 KRX_ID, KRX_PW 로 pykrx 자동 로그인. PIT_SLEEP(호출 간격), PIT_LIMIT(테스트용 거래일 수 제한).
"""
import datetime as dt
import os
import sys
import time

import pandas as pd
from pykrx import stock

YEAR = int(sys.argv[1])
OUT = os.environ.get('PIT_OUT', 'pit_out_extra')
SLEEP = float(os.environ.get('PIT_SLEEP', '0.15'))
LIMIT = int(os.environ.get('PIT_LIMIT', '0'))
INV = ['금융투자', '보험', '투신', '사모', '은행', '기타금융', '연기금', '기타외국인']
INDEX = {'1001': 'KOSPI', '2001': 'KOSDAQ', '1028': 'KOSPI200'}
MAX_FAILED = 40
os.makedirs(OUT, exist_ok=True)


def call(fn, tries=3):
    """예외(네트워크·세션 오류)는 재시도 후 None, 빈 결과는 빈 DataFrame 그대로 반환."""
    err = ''
    for i in range(tries):
        try:
            time.sleep(SLEEP)
            return fn()
        except Exception as e:
            err = repr(e)[:200]
            time.sleep(2 * (i + 1) ** 2)
    print('  호출 실패:', err, flush=True)
    return None


def tag(df, d, **extra):
    df = df.copy()
    df.index.name = 'ticker'
    df = df.reset_index()
    df.insert(0, 'date', pd.Timestamp(d))
    for k, v in extra.items():
        df[k] = v
    df['ticker'] = df['ticker'].astype(str)
    return df


def trading_days(year):
    end = min(dt.date(year, 12, 31), dt.date.today())
    df = call(lambda: stock.get_index_ohlcv_by_date(f'{year}0101', end.strftime('%Y%m%d'), '1001'))
    if df is None or df.empty:
        return []
    days = sorted(pd.to_datetime(df.index).date)
    return days[:LIMIT] if LIMIT else days


def main():
    t0 = time.time()
    days = trading_days(YEAR)
    if not days:
        print(f'{YEAR}: 거래일 목록을 얻지 못함')
        return 1
    print(f'{YEAR}: 거래일 {len(days)}개', flush=True)
    parts = {k: [] for k in ('fund', 'forgn', 'sbal', 'inv', 'sector', 'idx')}
    failed, seen_month = [], set()

    for n, d in enumerate(days, 1):
        ds = d.strftime('%Y%m%d')
        f = call(lambda: stock.get_market_fundamental_by_ticker(ds, market='ALL'))
        if f is None: failed.append(f'{ds}:fund')
        elif not f.empty: parts['fund'].append(tag(f, d))
        g = call(lambda: stock.get_exhaustion_rates_of_foreign_investment_by_ticker(ds, market='ALL'))
        if g is None: failed.append(f'{ds}:forgn')
        elif not g.empty: parts['forgn'].append(tag(g, d))
        for mk in ('KOSPI', 'KOSDAQ'):
            s = call(lambda: stock.get_shorting_balance_by_ticker(ds, market=mk))
            if s is None: failed.append(f'{ds}:sbal:{mk}')
            elif not s.empty: parts['sbal'].append(tag(s, d, market=mk))
        cols = {}
        for inv in INV:
            v = call(lambda: stock.get_market_net_purchases_of_equities_by_ticker(ds, ds, 'ALL', inv))
            if v is None: failed.append(f'{ds}:inv:{inv}')
            elif not v.empty: cols[inv] = v['순매수거래대금']
        if cols:
            parts['inv'].append(tag(pd.DataFrame(cols), d))
        if (d.year, d.month) not in seen_month:
            seen_month.add((d.year, d.month))
            for mk in ('KOSPI', 'KOSDAQ'):
                c = call(lambda: stock.get_market_sector_classifications(ds, mk))
                if c is None: failed.append(f'{ds}:sector:{mk}')
                elif not c.empty: parts['sector'].append(tag(c, d, market=mk))
        if n % 20 == 0:
            print(f'{YEAR}: {n}/{len(days)}일 ({d}), 실패 {len(failed)}, {round(time.time() - t0)}초', flush=True)

    for tk, nm in INDEX.items():
        x = call(lambda: stock.get_index_ohlcv_by_date(f'{YEAR}0101', days[-1].strftime('%Y%m%d'), tk))
        if x is None: failed.append(f'idx:{nm}')
        elif not x.empty:
            x = x.copy(); x.index.name = 'date'; x = x.reset_index(); x.insert(1, 'index', nm); parts['idx'].append(x)

    lines = []
    for name, ps in parts.items():
        if ps:
            df = pd.concat(ps, ignore_index=True)
            df.to_parquet(f'{OUT}/{name}_{YEAR}.parquet', index=False)
            nd = df['date'].nunique()
            lines.append(f'{name}_{YEAR}: {len(df)}행, {nd}일' + (f', 종목 {df.ticker.nunique()}개' if 'ticker' in df else ''))
        else:
            lines.append(f'{name}_{YEAR}: 데이터 없음')
    with open(f'{OUT}/failed_extra_{YEAR}.txt', 'w') as fh:
        fh.write('\n'.join(failed))
    with open(f'{OUT}/extra_summary_{YEAR}.txt', 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(lines))
    print('\n'.join(lines))
    print(f'{YEAR} 완료: 거래일 {len(days)}, 실패 {len(failed)}건, {round(time.time() - t0)}초')
    return 1 if len(failed) > MAX_FAILED else 0


if __name__ == '__main__':
    sys.exit(main())
