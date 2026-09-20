"""
KRX 시점(PIT) 데이터 수집기 — 상장폐지 종목 포함 전 종목, 연도 단위.
사용: python pit_collect.py 2019
- 매 거래일: 전 종목 시세(시가총액 포함), 투자자별 순매수거래대금(기관합계·개인·외국인·기타법인), 공매도(KOSPI·KOSDAQ)
- 결과: $PIT_OUT(기본 pit_out)/ohlcv_YYYY.parquet, flow_YYYY.parquet, short_YYYY.parquet, failed_YYYY.txt
- 환경변수 KRX_ID, KRX_PW 가 있으면 pykrx 가 자동 로그인한다.
- 기존 레포 파일은 읽지도 쓰지도 않는다.
"""
import os
import sys
import time
import datetime as dt

import pandas as pd
from pykrx import stock

YEAR = int(sys.argv[1])
OUT = os.environ.get('PIT_OUT', 'pit_out')
SLEEP = float(os.environ.get('PIT_SLEEP', '0.15'))
INVESTORS = {'기관합계': '기관합계', '개인': '개인', '외국인': '외국인합계', '기타법인': '기타법인'}
os.makedirs(OUT, exist_ok=True)


def call(fn, tries=3):
    """예외(네트워크·세션 오류)는 재시도 후 None, 빈 결과(휴장 등)는 빈 DataFrame 그대로 반환."""
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


oh, fl, sh, failed = [], [], [], []
n_days = 0
d = dt.date(YEAR, 1, 1)
end = min(dt.date(YEAR, 12, 31), dt.date.today())
t0 = time.time()
while d <= end:
    if d.weekday() < 5:
        ds = d.strftime('%Y%m%d')
        a = call(lambda: stock.get_market_ohlcv_by_ticker(ds, market='ALL'))
        if a is None:
            failed.append(ds)
        elif not a.empty and float(a['거래량'].fillna(0).sum()) > 0:      # 거래일
            n_days += 1
            oh.append(tag(a, d))
            cols = {}
            for inv, name in INVESTORS.items():
                f = call(lambda: stock.get_market_net_purchases_of_equities_by_ticker(ds, ds, 'ALL', inv))
                if f is None:
                    failed.append(f'{ds}:flow:{inv}')
                elif not f.empty:
                    cols[name] = f['순매수거래대금']
            if cols:
                fl.append(tag(pd.DataFrame(cols), d))
            for mk in ('KOSPI', 'KOSDAQ'):
                s = call(lambda: stock.get_shorting_volume_by_ticker(ds, market=mk))
                if s is None:
                    failed.append(f'{ds}:short:{mk}')
                elif not s.empty:
                    sh.append(tag(s, d, market=mk))
            if n_days % 20 == 0:
                print(f'{YEAR}: {n_days}거래일 완료 ({d}), {round(time.time() - t0)}초', flush=True)
    d += dt.timedelta(days=1)

for name, parts in (('ohlcv', oh), ('flow', fl), ('short', sh)):
    if parts:
        df = pd.concat(parts, ignore_index=True)
        df.to_parquet(f'{OUT}/{name}_{YEAR}.parquet', index=False)
        print(f'{name}_{YEAR}: {len(df)}행, 종목 {df.ticker.nunique()}개')
    else:
        print(f'{name}_{YEAR}: 데이터 없음')
with open(f'{OUT}/failed_{YEAR}.txt', 'w') as fh:
    fh.write('\n'.join(failed))
print(f'{YEAR} 완료: 거래일 {n_days}, 실패 {len(failed)}건, {round(time.time() - t0)}초')
if n_days == 0 or len(failed) > 10:
    sys.exit(1)
