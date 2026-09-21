"""
ML 섀도 포워드 기록기 (기록 전용)

- signal_runner.py, data/ 를 수정하지 않는다. output/shadow_ml/ 에만 쓴다.
- 검증에 쓴 백테스트(build_features.py + walkforward.py, 20일 보유)와 동일한 피처·모델·학습구간 규칙:
  날짜가 속한 연도 Y 의 예측은 (Y-01-01 - 35일) 이전 데이터만으로 학습한 모델을 쓴다.
  (연 1회 재학습, 파라미터는 백테스트와 동일. 결과를 보고 바꾸지 않는다.)
- 실행할 때마다 아직 기록되지 않은 거래일의 ML 점수(유동성 필터 통과 전 종목)를 CSV 로 저장한다.
  파일: output/shadow_ml/YYYY-MM-DD.csv  (컬럼: date,ticker,name,p,rank,train_cutoff — name=종목명은 표시용, 2026-09-22 추가)
  ticker 는 문자열(앞자리 0 유지). 읽을 때 dtype={'ticker': str} 지정.

환경변수 (테스트용, 운영에서는 미사용)
  SHADOW_ASOF      이 날짜까지의 데이터만 사용
  SHADOW_START     이 날짜 이후 모든 거래일을 (덮어쓰며) 기록
  SHADOW_DATA_DIR  데이터 폴더 (기본: ./data)
  SHADOW_OUT_DIR   출력 폴더 (기본: ./output/shadow_ml)
"""
import os
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')
from sklearn.ensemble import HistGradientBoostingRegressor

ROOT = Path(__file__).parent
DATA = Path(os.environ.get('SHADOW_DATA_DIR', ROOT / 'data'))
OUT = Path(os.environ.get('SHADOW_OUT_DIR', ROOT / 'output' / 'shadow_ml'))
ASOF = os.environ.get('SHADOW_ASOF')
START = os.environ.get('SHADOW_START')
H = 20            # 보유기간(백테스트의 20일 모델)
MAX_CATCHUP = 30  # 누락일 따라잡기 상한(거래일)
MIN_TRAIN_ROWS = 100000

# 종목명(표시용, 예측·평가와 무관). 못 찾으면 빈 칸.
try:
    _u = pd.read_parquet(DATA / 'universe.parquet', columns=['ticker', 'name'])
    NAMES = dict(zip(_u['ticker'].astype(str), _u['name']))
except Exception as _e:
    print('종목명 로드 실패(빈 칸으로 기록):', repr(_e)[:100])
    NAMES = {}
t0 = time.time()


def load(name):
    d = pd.read_parquet(DATA / f'{name}_full.parquet')
    d['date'] = pd.to_datetime(d['date'])
    if ASOF:
        d = d[d['date'] <= pd.Timestamp(ASOF)]
    return d.reset_index(drop=True)


o, f, s = load('ohlcv'), load('flow'), load('short')

# ---------------------------------------------------------------- 피처 (build_features.py 와 동일)
P = lambda d, c: d.pivot(index='date', columns='ticker', values=c)
op, hi, lo, cl, vo = [P(o, c) for c in ['시가', '고가', '저가', '종가', '거래량']]
cal = cl.index
fg, ins, ind = [P(f, c).reindex(cal) for c in ['외국인합계', '기관합계', '개인']]
sr = P(s, '비중').reindex(cal)
tv = cl * vo
tv20 = tv.rolling(20).mean()
ret1 = cl.pct_change(fill_method=None).clip(-0.35, 0.35)
mkt = ret1.mean(axis=1)

liq = (tv20 >= 3e9)
M = liq.values & (np.arange(len(cal)) >= 250)[:, None]
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

# ---------------------------------------------------------------- v7 신호 (make_prep.py 와 동일: 레포의 compute_features 사용)
import signal_runner as SR

sig = (SR.compute_features(o, f, s)
       .set_index(['date', 'ticker'])[['SIG1', 'SIG2', 'SIG3', 'SIG4', 'SIG5', 'SIG6', 'SIG7', 'n_signals']]
       .astype('float32'))

# ---------------------------------------------------------------- 모델 입력 (walkforward.py 와 동일)
feats = [c for c in X.columns if not c.startswith('fret')]
R = X.groupby(level=0)[feats].rank(pct=True).astype('float32')
R.columns = [c + '_r' for c in feats]
D = pd.concat([R, sig.reindex(X.index)], axis=1)
mkv = mk.reindex(X.index.get_level_values(0)).astype('float32')
mkv.index = X.index
D = pd.concat([D, mkv], axis=1)
dcols = list(D.columns)
dt = X.index.get_level_values(0)
fr = X[f'fret{H}']
y = fr.groupby(level=0).rank(pct=True) - 0.5
ok = y.notna().values

# ---------------------------------------------------------------- 기록 대상 거래일 결정
OUT.mkdir(parents=True, exist_ok=True)
all_dates = sorted(set(dt))
if not all_dates:
    print('데이터에 거래일이 없음'); sys.exit(1)
done = sorted(pd.Timestamp(p.stem) for p in OUT.glob('????-??-??.csv'))
if START:
    targets = [d for d in all_dates if d >= pd.Timestamp(START)]
elif done:
    targets = [d for d in all_dates if d > done[-1]]
else:
    targets = all_dates[-1:]
targets = targets[-MAX_CATCHUP:]
if not targets:
    print(f'기록할 새 거래일 없음 (마지막 기록 {done[-1].date() if done else None}, 데이터 마지막 {all_dates[-1].date()})')
    sys.exit(0)

# ---------------------------------------------------------------- 연도별 모델 학습 → 예측 기록
models = {}
for Y in sorted({d.year for d in targets}):
    cutoff = pd.Timestamp(f'{Y}-01-01') - pd.Timedelta(days=35)
    trn = (dt < cutoff) & ok
    if trn.sum() < MIN_TRAIN_ROWS:
        print(f'{Y}년 학습 행수 부족: {int(trn.sum())}'); sys.exit(1)
    m = HistGradientBoostingRegressor(max_iter=150, learning_rate=0.05, max_depth=4, min_samples_leaf=800,
                                      l2_regularization=5.0, random_state=0)
    m.fit(D.loc[trn, dcols], y[trn])
    models[Y] = (m, cutoff)
    print(f'{Y}년 모델 학습: 학습 {int(trn.sum())}행, 학습 종료 기준 {cutoff.date()}')

for d in targets:
    m, cutoff = models[d.year]
    te = (dt == d)
    tk = X.index[te].get_level_values(1)
    p = m.predict(D.loc[te, dcols])
    out = pd.DataFrame({'date': d.strftime('%Y-%m-%d'), 'ticker': tk.astype(str), 'p': p})
    out.insert(2, 'name', out['ticker'].map(NAMES).fillna(''))
    out['rank'] = out['p'].rank(ascending=False, method='first').astype(int)
    out = out.sort_values('rank')
    out['train_cutoff'] = cutoff.strftime('%Y-%m-%d')
    out.to_csv(OUT / f'{d:%Y-%m-%d}.csv', index=False, float_format='%.8f')
    print(f'기록: {d:%Y-%m-%d} ({len(out)}종목)')
print('완료', round(time.time() - t0), '초')
