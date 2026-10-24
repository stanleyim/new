# New5 모의 시험 코드 실데이터 대조 셀 — 레포에 올린 overnight_paper.py의 함수를 그대로 써서
# 2026-02-02~10-22 매수일을 계산하고, c7(분봉 기반) 날짜별 결과와 비교
# 기대: 명단에 불일치 8종목이 없는 날은 c7과 같아야 함(마지막 10-23 매도일은 c7 분봉 끝 문제로 제외)
import os, urllib.request, importlib.util, numpy as np, pandas as pd
D = globals().get('D', '/content/md/minute_data')
OUT = globals().get('OUT', '/content/drive/MyDrive/new5_minute')
SRC = globals().get('SRC', '/content/overnight_paper.py')
if not os.path.exists(SRC) or globals().get('SRC') is None:
    urllib.request.urlretrieve('https://raw.githubusercontent.com/stanleyim/new/main/overnight_paper.py', SRC)
os.environ['OP_DIR'] = '/tmp/op_check'
spec = importlib.util.spec_from_file_location('op', SRC); op = importlib.util.module_from_spec(spec); spec.loader.exec_module(op)
BAD = {'207940', '00088K', '006800', '060230', '036810', '102260', '017860', '072990'}
k = pd.read_parquet(f'{D}/krx_daily.parquet')
k['ticker'] = k['ticker'].astype(str).str.zfill(6)
k['date'] = k['date'].astype(str).str.replace('-', '').str[:8]
cal, opn, cl, tv20 = op.pivots(k)
op.TEST_START, op.NO_BUY = '20260130', set()            # 대조용: 2026-02-02 매도분부터, 수능 제외 없음(c7과 같게)
tr = op.settle(op.record_buys(pd.DataFrame(columns=op.COLS), cal, cl, tv20)[0], cal, opn)
dly = op.daily_table(tr).set_index('sell_date')
c7 = pd.read_csv(f'{OUT}/c7_overnight_daily.csv', index_col=0); c7.index = c7.index.astype(str)
s7 = c7['20'].astype(float)
days = [d for d in dly.index if '20260202' <= d < '20261023' and d in s7.index]
bad_day = {b: any(t in BAD for t in tr.loc[tr['buy_date'] == b, 'ticker']) for b in dly['buy_date']}
res = pd.DataFrame({'script': dly.loc[days, 'net'], 'c7': s7.loc[days],
                    'bad': [bad_day[dly.at[d, 'buy_date']] for d in days]})
res['diff'] = (res['script'] - res['c7']).abs()
same = res['diff'] < 1e-6
print(f'대조 {len(res)}일 · 일치 {int(same.sum())} · 불일치 {int((~same).sum())} (그중 명단에 불일치 8종목 있는 날 {int((~same & res.bad).sum())})')
print(f'  명단에 불일치 8종목 없는 날의 불일치: {int((~same & ~res.bad).sum())}일 (0이어야 정상)')
for d in res[~same & ~res.bad].index[:10]:
    b = dly.at[d, 'buy_date']; x = tr[tr['buy_date'] == b]
    print(f'   {d} script {res.at[d, "script"]:+.3%} c7 {res.at[d, "c7"]:+.3%} · 제외 표시: ' +
          ', '.join(f'{t}:{s}' for t, s in zip(x.ticker, x.status) if s != '완료'))
print(f'평균(대조 날짜) script {res.script.mean():+.3%} · c7 {res.c7.mean():+.3%}')
