# stanleyim/new

Korean equity quant signal engine — **v7 Final** (모멘텀 스윙, T+20)

SIG1~SIG7 가중합 + 유동성 필터 + Top5 신호 자동 산출.
12년 backtest 검증, **forward test 진행 중 (2026-06-30~)**.

---

## 빠른 시작

### 1. GitHub Secrets 등록 (Settings → Secrets and variables → Actions)

| Key | 용도 | 필수 |
|---|---|---|
| `KIS_APP_KEY` | KIS API 인증 키 | **필수** |
| `KIS_APP_SECRET` | KIS API 시크릿 | **필수** |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot 토큰 | 선택 |
| `TELEGRAM_CHAT_ID` | 알림 수신 채팅 ID | 선택 |

**Telegram secrets 누락 시 알림 없이 작동.**

### 2. 자동 실행 일정

| 시간 | Workflow | 작업 |
|---|---|---|
| **20:00 KST** | `daily_signal.yml` | OHLCV·수급·공매도 fetch → 7축 신호 → Top5 → Telegram |

Cron: `0 11 * * 1-5` UTC (= 평일 20:00 KST)  
한국 공휴일 자동 skip (`holidays.SouthKorea()`).

### 3. 수동 실행

GitHub → Actions → **Daily Signal Run** → **Run workflow**  
(KST 18시 이후 실행 권장 — 당일 데이터 완전 공개)

---

## 시스템 개요

### 7개 신호 (SIG1 ~ SIG7)

| 신호 | 조건 | 가중치 |
|---|---|---|
| **SIG1** | 20일 신고가 돌파 + vol_surge>2.0 + frgn_z>2.0 | 1.87 |
| **SIG2** | 60일 신고가 돌파 + vol_surge>2.0 | 1.64 |
| **SIG3** | vol_surge>2.0 + frgn_z>2.0 | 1.61 |
| **SIG4** | 등락률 < −5% (큰 갭다운) | 1.50 |
| **SIG5** | vol_surge>2.0 + frgn_z>2.0 + short_chg<−0.5 | 1.42 |
| **SIG6** | 20일 신고가 돌파 + vol_surge>2.0 | 1.38 |
| **SIG7** | vol_ratio>1.5 (HL 변동성 증가) | 1.34 |

**Feature 정의**

- `vol_surge` = 5일 평균 거래량 / 20일 평균 거래량
- `frgn_z` = (외인 순매수 − 20일 평균) / 20일 표준편차
- `short_chg` = 5일 공매도 비중 − 20일 공매도 비중
- `vol_ratio` = 5일 평균 HL 변동폭 / 20일 평균 HL 변동폭

### 선정 규칙

- **Universe**: 439종목 (KOSPI + KOSDAQ 보통주)
- **유동성 필터**: 거래대금_20ma ≥ 30억
- **진입 조건**: n_signals ≥ 3
- **선정**: signal_score 상위 5종목 (n_pick ≥ 3 미만 시 보류)
- **포지션**: 동시 보유 K=5, 1종목 max 25%
- **위험 종목 자동 제외**: 거래정지·관리종목·투자주의·정리매매·시장경고·공매도과열

### 진입·청산 (코드 동결)

- **Entry**: T+1 시가 (신호일 다음 영업일)
- **Exit**: T+20 종가 (매수 후 20 영업일)
- **Cost**: 0.206% 왕복
- **중간 처분 없음** — SL·TP·신호 약화 불개입 (backtest 검증 결과)

---

## 백테스트 결과 (12년)

| 지표 | 값 |
|---|---|
| 누적 수익 | +394.63% |
| CAGR | — |
| 매매당 평균 수익 | +3.89% |
| Sharpe (연환산) | 1.278 |
| 최대 낙폭 (MDD) | −44.85% |
| 적중률 | 51.8% |
| 연간 매매 횟수 | ~30회 |
| p(>0) | 100% |

---

## 파일 구조

```
stanleyim/new/
├── .github/workflows/
│   └── daily_signal.yml          # Actions 자동 실행 정의
├── data/
│   ├── universe.parquet          # 439종목 메타
│   ├── ohlcv_full.parquet        # 시가/고가/저가/종가/거래량/등락률
│   ├── flow_full.parquet         # 외인/기관/개인/기타법인
│   └── short_full.parquet        # 공매도 거래량 + 비중
├── output/
│   ├── signals/
│   │   └── YYYY-MM-DD.json       # 매일 신호 산출 결과
│   ├── all_signals_log/
│   │   └── YYYY-MM-DD.json       # n_signals≥3 전체 후보 (검증용)
│   ├── holdings.json             # 현재 보유 종목
│   └── results.json              # 종결 결과 누적
├── signal_runner.py              # 메인 실행 스크립트
├── requirements.txt
└── README.md
```

---

## 실전 운용 가이드

### 데이터 분리 (혼동 금지)

- `output/signals/` = **실행 신호** (Top5 선정 결과)
- `output/all_signals_log/` = **전체 후보** (n_signals≥3, forward 검증용)
- 실 매매 P&L = **별도 트랙** (사용자 execution layer)

### 검증 트리거 (Phase 2, 2026-08-29)

| 지표 | 양호 | 검토 | 중단 |
|---|---|---|---|
| T+20 적중률 | ≥ 50% | < 45% | — |
| 2개월 CAGR 환산 | ≥ +10% | < +5% | — |
| MDD | > −20% | < −30% | < −45% |

### 주요 운영 주의

- **손절·자금관리·매매 결정 = 사용자 자체 영역** (시스템 미지원)
- **GitHub Token 만료**: 2026-09-19 이전 갱신 필수
- **pykrx 미사용**: GitHub Actions runner에서 KRX IP 차단 → KIS API 전용

---

## 한계 (정직)

1. **MDD −44.85%** — 약세장 장기 손실 가능. 자금관리 필수.
2. **수익보장 X** — 백테스트는 과거. forward 2개월 검증 필수.
3. **Universe 439종목** — 코스닥 소형 급등주 일부 미포함 가능.
4. **신호 부재 구간** — 하락장 모멘텀 소멸 시 연속 무신호 정상.

---

## 핵심 원칙

> **"규칙을 안 바꾸는 능력이 가장 어렵고 가장 중요하다."**

- 코드 동결. 신호·가중치·T+20 변경 X
- 단기 무신호/손실로 흥분·낙담 X. 통계가 말할 때까지 대기
- Phase 3 ML 검토는 2026-12 이후 (데이터 충분 시)

---

## 라이센스

**Private. 무단 배포 금지.**

---

*Forward test 시작: 2026-06-30 / Phase 2 평가: 2026-08-29*
