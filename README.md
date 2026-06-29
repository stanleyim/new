# quant-v2 forward test

매일 KST 20:00 (UTC 11:00) 자동 신호 산출 시스템.

## 시스템 v7 final
- Universe: 439 종목 (KOSPI+KOSDAQ 보통주)
- Signal: n_signals >= 3 (SIG1~SIG7)
- Liquidity: 거래대금_20ma >= 30억
- Selection: signal_score 상위 5종목
- n_pick filter: >= 3
- Position limit: 동시 보유 5종목
- Weight: 1종목 max 25%
- Entry: T+1 시가 / Exit: T+20 종가
- Cost: 0.206% 왕복

## 12년 backtest 결과
- mean/day: +3.46% | median: +0.69% | DD: -44.85% | hit: 51.8%
- 95% CI: [+1.14%, +5.98%]

## 자동화
- GitHub Actions cron: 매일 11:00 UTC (KST 20:00)
- Telegram 알림
- output/signals/YYYY-MM-DD.json + output/holdings.json
