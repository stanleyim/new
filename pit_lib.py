"""
공정한 풀(PIT) ML 섀도 — 공통 파이프라인.
pit_run.py(POOL=topcap) + pit_feat.py + pit_ml.py 의 로직을 그대로 옮긴 것이다(변경 금지).
  풀    : 보통주(끝자리 0) 중 20일 평균 거래대금이 한 번이라도 25억 이상인 종목(tk),
          그날 = 전일 시총 순위 상위 439 ∩ 20일 평균 거래대금 30억 이상, 상장폐지 포함
  가격  : KRX 등락률 누적으로 수정주가 복원(종가×거래량 = 실제 거래대금 유지)
  피처  : 47개 + 시장 5개, 날짜별 순위(pct) 변환
  모델  : HistGradientBoosting(150, 0.05, depth 4, leaf 800, l2 5.0, seed 0), 연 1회 재학습,
          학습 종료 = 해당 연도 1월 1일 - 35일(20일 목표) / - 65일(60일 목표)
데이터 : <D>/{ohlcv,flow,short}_YYYY.parquet + <D>/daily/{ohlcv,flow,short}_YYYYMMDD.parquet
"""
import glob
import gc
import re
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

FIRST_YEAR = 2014
NCAP = 439
TV_EVER = 2.5e9      # 풀(tk) 편입 기준: 20일 평균 거래대금 최대값
TV_LIQ = 3e9         # 그날 유동성 기준
MODEL_GAPS = {20: 35, 60: 65}
HGB_PARAMS = dict(max_iter=150, learning_rate=0.05, max_depth=4, min_samples_leaf=800,
                  l2_regularization=5.0, random_state=0)


def rd(D, name, cols=None):
    """연도 파일(2014~) + 일일 증분 파일을 이어 붙여 읽는다. (date, ticker) 중복은 마지막 것만 남긴다."""
    files = []
    for f in sorted(glob.glob(f'{D}/{name}_[0-9][0-9][0-9][0-9].parquet')):
        if int(re.search(r'_(\d{4})\.parquet$', f).group(1)) >= FIRST_YEAR:
            files.append(f)
    files += sorted(glob.glob(f'{D}/daily/{name}_[0-9]*.parquet'))
    if not files:
        raise FileNotFoundError(f'{name} 데이터 없음: {D}')
    use = None if cols is None else list(dict.fromkeys(['date', 'ticker'] + list(cols)))
    df = pd.concat([pd.read_parquet(f, columns=use) for f in files], ignore_index=True)
    df['date'] = pd.to_datetime(df['date'])
    n0 = len(df)
    df = df.drop_duplicates(['date', 'ticker'], keep='last').reset_index(drop=True)
    if len(df) != n0:
        print(f'  [{name}] (date,ticker) 중복 {n0 - len(df)}행 제거', flush=True)
    return df


def universe(D):
    """pit_run.py POOL=topcap 의 종목 풀(tk)과 일별 편입(MEMBER, 전일 시총 순위 상위 439)."""
    o = rd(D, 'ohlcv', ['종가', '거래량', '시가총액'])
    o = o[o.ticker.str[-1] == '0']
    o['tv'] = o['종가'].astype('float64') * o['거래량'].astype('float64')
    o = o.sort_values(['ticker', 'date'])
    o['tv20'] = o.groupby('ticker')['tv'].transform(lambda x: x.rolling(20).mean())
    ever = o.groupby('ticker')['tv20'].max()
    tk = sorted(ever[ever >= TV_EVER].index)
    capm = o.pivot(index='date', columns='ticker', values='시가총액').astype('float64')
    rk = capm.rank(axis=1, ascending=False, method='first').shift(1)     # 전일 시가총액 순위
    member = (rk <= NCAP)
    del o, capm, rk
    gc.collect()
    return tk, member


def adjusted_ohlcv(D, tickers):
    """수정주가 복원(pit_feat.py 와 동일). tickers 만 읽는다."""
    tks = set(tickers)
    o = rd(D, 'ohlcv', ['시가', '고가', '저가', '종가', '거래량', '등락률'])
    o = o[o.ticker.isin(tks)].sort_values(['ticker', 'date']).reset_index(drop=True)
    ret = (o['등락률'].astype('float64') / 100).fillna(0.0)
    ret[o['종가'] <= 0] = 0.0
    A = (1 + ret).groupby(o['ticker']).cumprod()
    fac = (A / o['종가'].where(o['종가'] > 0)).groupby(o['ticker']).ffill().groupby(o['ticker']).bfill()
    for c in ['시가', '고가', '저가', '종가']:
        o[c] = o[c].astype('float64') * fac
    o['거래량'] = o['거래량'].astype('float64') / fac
    return o


def build_features(D, tk, MEMBER):
    """pit_feat.py 그대로. 반환: X(피처+fret5/10/20), mk(시장 5+1), cl, op (수정주가 피벗)."""
    tks = set(tk)
    o = adjusted_ohlcv(D, tks)
    f = rd(D, 'flow')
    f = f[f.ticker.isin(tks)]
    s = rd(D, 'short')
    s = s[s.ticker.isin(tks)].drop(columns=['market'])
    gc.collect()
    P = lambda d, c: d.pivot(index='date', columns='ticker', values=c)
    op, hi, lo, cl, vo = [P(o, c) for c in ['시가', '고가', '저가', '종가', '거래량']]
    cal = cl.index
    fg, ins, ind = [P(f, c).reindex(cal) for c in ['외국인합계', '기관합계', '개인']]
    sr = P(s, '비중').reindex(cal)
    del o, f, s
    gc.collect()
    tv = cl * vo
    tv20 = tv.rolling(20).mean()
    ret1 = cl.pct_change(fill_method=None).clip(-0.35, 0.35)
    mkt = ret1.mean(axis=1)

    liq = (tv20 >= TV_LIQ)
    M = liq.values & MEMBER.reindex(index=cal, columns=cl.columns).fillna(False).values & (np.arange(len(cal)) >= 250)[:, None]
    di, ti = np.where(M)
    idx = pd.MultiIndex.from_arrays([cal[di], cl.columns[ti]], names=['date', 'ticker'])
    cols = {}

    def add(name, df):
        cols[name] = df.reindex(index=cal, columns=cl.columns).values.astype('float32')[M]

    for n in (5, 10, 20, 60, 120):
        add(f'ret{n}', cl.pct_change(n, fill_method=None))
    add('mom_12_1', cl.shift(20) / cl.shift(250) - 1)
    add('dist_hi250', cl / hi.rolling(250, min_periods=200).max() - 1)
    add('dist_hi60', cl / hi.rolling(60).max() - 1)
    add('dist_lo60', cl / lo.rolling(60).min() - 1)
    add('vol_surge', vo.rolling(5).mean() / vo.rolling(20).mean())
    add('vol_surge20_60', vo.rolling(20).mean() / vo.rolling(60).mean())
    add('vol_5_60', vo.rolling(5).mean() / vo.rolling(60).mean())
    add('ivol20', ret1.rolling(20).std())
    add('ivol60', ret1.rolling(60).std())
    add('hl20', ((hi - lo) / cl).rolling(20).mean())
    add('illiq', (ret1.abs() / tv.replace(0, np.nan)).rolling(20).mean() * 1e9)
    add('logtv', np.log(tv20.replace(0, np.nan)))
    day_body = (cl - op) / op
    gap_on = op / cl.shift(1) - 1
    add('day_body', day_body)
    add('ret1', ret1)
    add('gap_on', gap_on)
    add('close_loc', (cl - lo) / (hi - lo).replace(0, np.nan))
    add('max20', ret1.rolling(20).max())
    add('min20', ret1.rolling(20).min())
    add('skew20', ret1.rolling(20).skew())
    beta = ret1.rolling(60).cov(mkt) / mkt.rolling(60).var()
    add('beta60', beta)
    add('resid20', cl.pct_change(20, fill_method=None) - beta.mul(mkt.rolling(20).sum(), axis=0))
    add('overnight20', gap_on.rolling(20).sum())
    add('intraday20', day_body.rolling(20).sum())
    add('updays20', (ret1 > 0).rolling(20).mean())
    add('ma20d', cl / cl.rolling(20).mean() - 1)
    add('ma60d', cl / cl.rolling(60).mean() - 1)
    dlt = cl.diff()
    up = dlt.clip(lower=0).rolling(14).mean()
    dn = (-dlt.clip(upper=0)).rolling(14).mean()
    add('rsi14', 100 - 100 / (1 + up / dn.replace(0, np.nan)))
    for n in (1, 5, 20, 60):
        add(f'frgn{n}', fg.rolling(n).sum() / tv.rolling(n).sum())
        add(f'inst{n}', ins.rolling(n).sum() / tv.rolling(n).sum())
        add(f'indv{n}', ind.rolling(n).sum() / tv.rolling(n).sum())
    add('frgn_z', (fg - fg.rolling(20).mean()) / fg.rolling(20).std())
    add('short_ratio5', sr.rolling(5).mean())
    add('short_chg', sr.rolling(5).mean() - sr.rolling(20).mean())
    for h in (5, 10, 20):
        add(f'fret{h}', (cl.shift(-(h + 1)) / op.shift(-1) - 1))
    X = pd.DataFrame(cols, index=idx).replace([np.inf, -np.inf], np.nan)
    mk = pd.DataFrame({'mkt_ret5': mkt.rolling(5).sum(), 'mkt_ret20': mkt.rolling(20).sum(), 'mkt_ret60': mkt.rolling(60).sum(),
                       'mkt_vol20': mkt.rolling(20).std(), 'breadth20': (cl.pct_change(20, fill_method=None) > 0).mean(axis=1)})
    eq = mkt.cumsum().apply(np.exp)
    mk['mkt_dd'] = eq / eq.rolling(120).max() - 1
    return X, mk, cl, op


def cutoffs(year):
    return {h: pd.Timestamp(f'{year}-01-01') - pd.Timedelta(days=g) for h, g in MODEL_GAPS.items()}


def predict_date(X, mk, cl, op, T):
    """pit_ml.py 와 동일한 학습(해당 연도 모델)으로 T 날짜 행만 예측한다.
    반환: DataFrame(index=ticker, columns=p20,p60), 학습 종료일 dict."""
    from sklearn.ensemble import HistGradientBoostingRegressor
    T = pd.Timestamp(T)
    Y = T.year
    f60 = (cl.shift(-61) / op.shift(-1) - 1).replace([np.inf, -np.inf], np.nan).stack().rename('fret60')
    f60.index.names = ['date', 'ticker']
    X = X.join(f60, how='left')
    del f60
    gc.collect()
    feats = [c for c in X.columns if not c.startswith('fret')]
    R = X.groupby(level=0)[feats].rank(pct=True).astype('float32')
    R.columns = [c + '_r' for c in feats]
    mkv = mk.reindex(X.index.get_level_values(0)).astype('float32')
    mkv.index = X.index
    D = pd.concat([R, mkv], axis=1)
    del R, mkv
    gc.collect()
    cols = list(D.columns)
    dt = X.index.get_level_values(0)
    te = (dt == T)
    if te.sum() == 0:
        raise RuntimeError(f'{T.date()} 예측 대상 행이 없다(풀 0종목)')
    cuts = cutoffs(Y)
    out = {}
    for h in (20, 60):
        y = X[f'fret{h}'].groupby(level=0).rank(pct=True) - 0.5
        ok = y.notna().values
        trn = (dt < cuts[h]) & ok
        if trn.sum() < 100000:
            raise RuntimeError(f'h{h} 학습행 부족: {int(trn.sum())}')
        m = HistGradientBoostingRegressor(**HGB_PARAMS)
        m.fit(D.loc[trn, cols], y[trn])
        out[f'p{h}'] = m.predict(D.loc[te, cols])
        print(f'  h{h} {Y} 학습행 {int(trn.sum())}', flush=True)
        del m, y
        gc.collect()
    res = pd.DataFrame(out, index=X.index[te].get_level_values('ticker'))
    return res, {h: c.date() for h, c in cuts.items()}
