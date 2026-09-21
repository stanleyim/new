"""
KRX 일일 수집기 — 공정한 풀(PIT) ML 섀도 기록용.
사용: python pit_daily.py --data <pit_data 폴더> [--today YYYY-MM-DD]
- <data> 안의 마지막 거래일 다음 날부터 오늘(KST)까지 빠진 날을 채운다.
- 거래일마다 전 종목 시세(시가총액 포함)·투자자별 순매수대금(4종)·공매도(KOSPI·KOSDAQ)를 받아
  <data>/daily/{ohlcv,flow,short}_YYYYMMDD.parquet 로 저장한다. 기존 연도 파일은 읽기만 하고 고치지 않는다.
- 호출은 pit_collect.py 와 같다(pykrx, KRX_ID/KRX_PW 자동 로그인). 단일 작업으로 순차 실행한다(병렬 로그인 금지).
- 하루치가 불완전하면 그날은 저장하지 않는다. 오늘 자료가 아직 올라오지 않았으면 경고만 하고 정상 종료(다음 실행이 채운다).
  과거 날짜가 불완전하거나 호출이 실패하면 거기서 멈추고 실패(빈 날짜가 끼어들면 롤링 피처가 깨지기 때문).
종료 코드: 0 정상(새 날짜 없음·오늘 자료 미공개 포함), 1 오류.
"""
import argparse
import datetime as dt
import glob
import os
import re
import sys
import time
from zoneinfo import ZoneInfo

import pandas as pd
from pykrx import stock

KST = ZoneInfo('Asia/Seoul')
SLEEP = float(os.environ.get('PIT_SLEEP', '0.15'))
INVESTORS = {'기관합계': '기관합계', '개인': '개인', '외국인': '외국인합계', '기타법인': '기타법인'}
MIN_OHLCV_VS_RECENT = 0.90     # 전 5거래일 중앙값 대비 시세 행 수
MIN_FLOW_VS_OHLCV = 0.85       # 시세 행 수 대비 수급 행 수(실측 0.92~0.97)
MIN_SHORT_VS_OHLCV = 0.90      # 시세 행 수 대비 공매도 행 수(실측 0.96)
MAX_GAP_DAYS = 30


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


def data_state(D):
    """(마지막 거래일, 최근 5거래일 시세 행 수 목록)."""
    counts = {}
    ys = sorted(glob.glob(f'{D}/ohlcv_[0-9][0-9][0-9][0-9].parquet'))
    if not ys:
        raise SystemExit(f'ohlcv 연도 파일이 없다: {D}')
    for f in ys[-2:]:
        s = pd.read_parquet(f, columns=['date'])['date'].value_counts()
        for k, v in s.items():
            counts[pd.Timestamp(k).date()] = int(v)
    for f in sorted(glob.glob(f'{D}/daily/ohlcv_[0-9]*.parquet')):
        d = dt.datetime.strptime(re.search(r'_(\d{8})\.parquet$', f).group(1), '%Y%m%d').date()
        counts[d] = len(pd.read_parquet(f, columns=['date']))
    last = max(counts)
    recent = [counts[k] for k in sorted(counts)[-5:]]
    return last, recent


def save(D, name, df, ds):
    os.makedirs(f'{D}/daily', exist_ok=True)
    tmp = f'{D}/daily/.{name}_{ds}.tmp'
    df.to_parquet(tmp, index=False)
    os.replace(tmp, f'{D}/daily/{name}_{ds}.parquet')


def fetch_day(d, recent):
    """반환: ('holiday',) / ('incomplete', 사유) / ('fail', 사유) / ('ok', {name: df})."""
    ds = d.strftime('%Y%m%d')
    a = call(lambda: stock.get_market_ohlcv_by_ticker(ds, market='ALL'))
    if a is None:
        return ('fail', '시세 호출 실패')
    if a.empty or float(a['거래량'].fillna(0).sum()) <= 0:
        return ('holiday',)
    med = sorted(recent)[len(recent) // 2]
    if len(a) < MIN_OHLCV_VS_RECENT * med:
        return ('incomplete', f'시세 {len(a)}행 < 최근 중앙값 {med}의 {MIN_OHLCV_VS_RECENT:.0%}')
    oh = tag(a, d)
    cols = {}
    for inv, name in INVESTORS.items():
        f = call(lambda: stock.get_market_net_purchases_of_equities_by_ticker(ds, ds, 'ALL', inv))
        if f is None:
            return ('fail', f'수급 호출 실패({inv})')
        if f.empty:
            return ('incomplete', f'수급 비어 있음({inv})')
        cols[name] = f['순매수거래대금']
    fdf = pd.DataFrame(cols)
    if len(fdf) < MIN_FLOW_VS_OHLCV * len(a):
        return ('incomplete', f'수급 {len(fdf)}행 < 시세 {len(a)}행의 {MIN_FLOW_VS_OHLCV:.0%}')
    fl = tag(fdf, d)
    parts = []
    for mk in ('KOSPI', 'KOSDAQ'):
        s = call(lambda: stock.get_shorting_volume_by_ticker(ds, market=mk))
        if s is None:
            return ('fail', f'공매도 호출 실패({mk})')
        if s.empty:
            return ('incomplete', f'공매도 비어 있음({mk})')
        parts.append(tag(s, d, market=mk))
    sh = pd.concat(parts, ignore_index=True)
    if len(sh) < MIN_SHORT_VS_OHLCV * len(a):
        return ('incomplete', f'공매도 {len(sh)}행 < 시세 {len(a)}행의 {MIN_SHORT_VS_OHLCV:.0%}')
    return ('ok', {'ohlcv': oh, 'flow': fl, 'short': sh})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', required=True)
    ap.add_argument('--today', default=None, help='테스트용: YYYY-MM-DD (기본: 지금 KST 날짜)')
    args = ap.parse_args()
    D = args.data
    today = dt.date.fromisoformat(args.today) if args.today else dt.datetime.now(KST).date()
    last, recent = data_state(D)
    print(f'데이터 마지막 거래일 {last} | 오늘(KST) {today}', flush=True)
    if (today - last).days > MAX_GAP_DAYS:
        print(f'::error::데이터가 {(today - last).days}일 비어 있다(한도 {MAX_GAP_DAYS}일). 수동 확인 필요.')
        return 1
    d = last + dt.timedelta(days=1)
    n_new = 0
    while d <= today:
        if d.weekday() < 5:
            r = fetch_day(d, recent)
            if r[0] == 'holiday':
                print(f'{d} 휴장(또는 미공개)', flush=True)
            elif r[0] == 'ok':
                ds = d.strftime('%Y%m%d')
                for name, df in r[1].items():
                    save(D, name, df, ds)
                recent = (recent + [len(r[1]['ohlcv'])])[-5:]
                n_new += 1
                print(f'{d} 저장: 시세 {len(r[1]["ohlcv"])} 수급 {len(r[1]["flow"])} 공매도 {len(r[1]["short"])}', flush=True)
            elif r[0] == 'incomplete' and d == today:
                print(f'::warning::{d} 자료가 아직 완전하지 않다({r[1]}). 저장하지 않고 종료한다. 다음 실행에서 다시 시도.')
                break
            else:
                print(f'::error::{d} {r[1]} — 여기서 멈춘다(빈 날짜를 남기지 않기 위해).')
                return 1
        d += dt.timedelta(days=1)
    print(f'완료: 새 거래일 {n_new}개', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
