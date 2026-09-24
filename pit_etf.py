"""
인버스 ETF 가격 데이터 수집기 — 코스피200 인버스 헤지 그리드서치용.
기존 pit_data / pit_data_extra 는 읽지도 쓰지도 않는다 (같은 브랜치에 파일만 추가).
사용: python pit_etf.py
대상 티커: 252670(KODEX 200선물인버스2X, 2배), 114800(KODEX 인버스, 1배)
  - T+20 고정보유 구조라 2X 디케이(음의 복리효과) 영향 비교용으로 1배도 같이 수집
출력: $PIT_OUT(기본 pit_out_etf)/etf_{ticker}.parquet, etf_summary.txt
  각 parquet 컬럼: date, ticker, name, NAV, 시가, 고가, 저가, 종가, 거래량, 거래대금, 기초지수
  (기초지수 컬럼으로 트래킹에러 = ETF수익률 - 기초지수수익률 직접 계산 가능, 별도 API 불필요)
환경변수 KRX_ID, KRX_PW 로 pykrx 자동 로그인 (기존 pit_collect.py와 동일 관례).
PIT_FROM(수집 시작일, 기본 20140101 — 레포 기존 데이터 시작일과 동일).
"""
import datetime as dt
import os
import sys
import time

from pykrx import stock

OUT = os.environ.get('PIT_OUT', 'pit_out_etf')
FROM = os.environ.get('PIT_FROM', '20140101')
TO = dt.date.today().strftime('%Y%m%d')
SLEEP = float(os.environ.get('PIT_SLEEP', '0.3'))
TICKERS = {
    '252670': 'KODEX 200선물인버스2X',
    '114800': 'KODEX 인버스',
}
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


def main():
    lines = []
    ok = True
    for tk, name in TICKERS.items():
        df = call(lambda tk=tk: stock.get_etf_ohlcv_by_date(FROM, TO, tk))
        if df is None or df.empty:
            lines.append(f'{tk}({name}): 데이터 없음/실패')
            ok = False
            continue
        df = df.copy()
        df.index.name = 'date'
        df = df.reset_index()
        df.insert(1, 'ticker', tk)
        df.insert(2, 'name', name)
        df.to_parquet(f'{OUT}/etf_{tk}.parquet', index=False)
        lines.append(f'{tk}({name}): {len(df)}행, {df["date"].min()}~{df["date"].max()}')

    with open(f'{OUT}/etf_summary.txt', 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(lines))
    print('\n'.join(lines))
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
