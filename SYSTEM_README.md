# quant-v2 v7 Final — 시스템 설명서

> **Repository**: `stanleyim/new`
> **목적**: 한국 KOSPI+KOSDAQ 모멘텀 알고리즘 트레이딩 시스템 (신호 알림만, 자동 매매 ❌)
> **운영 시점**: 2026-06-30 ~ (forward test 진행 중)
> **버전**: v7 final

---

## 1. 시스템 개요

### 1.1 본질
- **신호 알림 시스템** = 매일 KST 20:00 매수 후보 종목을 산출하여 Telegram 알림
- **실제 매매 / 자금 관리 / 손절 = 사용자 자체 판단** (시스템 영역 아님)
- 12년 backtest로 검증된 v7 final 규칙 그대로 forward test

### 1.2 시스템 구성
```
┌─────────────────────┐
│  GitHub Actions     │  매 평일 KST 20:00 자동 실행
│  (cron 11 UTC)      │  공휴일/주말 자동 skip
└──────────┬──────────┘
           ↓
┌─────────────────────┐
│  KIS API            │  OHLCV / 외인-기관 / 공매도 fetch
│  (한국투자증권)     │  439종목 약 4분
└──────────┬──────────┘
           ↓
┌─────────────────────┐
│  Feature 계산       │  7개 신호 SIG1~SIG7
│  (signal_runner.py) │  거래대금 필터 (≥30억)
└──────────┬──────────┘
           ↓
┌─────────────────────┐
│  신호 산출          │  n_signals≥3 + signal_score 상위 5
│  + 위험 종목 필터   │  (거래정지/관리/투자주의/정리매매 등)
└──────────┬──────────┘
           ↓
┌─────────────────────┐
│  Holdings 추적      │  K=5 동시 보유, T+1 매수 / T+20 매도
└──────────┬──────────┘
           ↓
┌─────────────────────┐
│  Telegram 알림      │  매수 후보 + 보유 종목 + 종결 종목
│  + Auto commit      │  data/ output/ 자동 push
└─────────────────────┘
```

---

## 2. v7 Final 시스템 정의

### 2.1 Universe
- **439종목** (KOSPI + KOSDAQ 보통주)
- 시가총액 ≥ 1,015억
- 거래대금 20일평균 ≥ 30억
- 외인 지분율 ≥ 5%
- 보통주만 (우선주 제외)

### 2.2 7개 신호 (SIG1 ~ SIG7)

| 신호 | 조건 | 가중치 |
|---|---|---|
| **SIG1** | 20일 신고가 돌파 + vol_surge>2.0 + frgn_z>2.0 | 1.87 |
| **SIG2** | 60일 신고가 돌파 + vol_surge>2.0 | 1.64 |
| **SIG3** | vol_surge>2.0 + frgn_z>2.0 | 1.61 |
| **SIG4** | 등락률 < -5% (큰 갭다운) | 1.50 |
| **SIG5** | vol_surge>2.0 + frgn_z>2.0 + short_chg<-0.5 | 1.42 |
| **SIG6** | 20일 신고가 돌파 + vol_surge>2.0 | 1.38 |
| **SIG7** | vol_ratio>1.5 (HL range 변동성 증가) | 1.34 |

**Feature 정의**:
- `vol_surge` = 5일 평균 거래량 / 20일 평균 거래량
- `frgn_z` = (외인 순매수 - 20일 평균) / 20일 표준편차
- `short_chg` = 5일 공매도 비중 - 20일 공매도 비중
- `vol_ratio` = 5일 평균 HL 변동폭 / 20일 평균 HL 변동폭
- `is_breakout_20` = 종가 > 직전 20일 고가 최대값
- `is_breakout_60` = 종가 > 직전 60일 고가 최대값

### 2.3 선정 규칙
- **n_signals ≥ 3** (7개 신호 중 3개 이상 활성)
- **거래대금_20ma ≥ 30억** (유동성 필터)
- **signal_score 상위 5종목** (가중치 합)
- **n_pick ≥ 3** (3개 미만 매매 보류)
- **K_MAX = 5** (동시 보유 최대 5종목)
- **max_weight = 25%** (종목당 자금 비중)
- **위험 종목 자동 제외** (거래정지/관리종목/투자주의/정리매매/시장경고/공매도과열)

### 2.4 매매 규칙
- **Entry**: T+1 시가 매수 (신호일 다음 영업일)
- **Exit**: T+20 종가 매도 (매수 후 20영업일 보유)
- **Cost**: 0.206% 왕복 (수수료 + 슬리피지)
- **중간 처분 없음**: Stop loss / Profit target / 신호 약화 모두 alpha 감소 검증 → T+20 고정

### 2.5 Backtest 성능 (12년)
| 지표 | 값 |
|---|---|
| 누적 수익 | +394.63% |
| 매매당 평균 수익 | +3.89% |
| 매매당 표준편차 | 16.6% |
| Sharpe (매매일 기준) | 0.234 |
| **Sharpe (연환산)** | **1.278** |
| 최대 낙폭 | -44.85% |
| 적중률 | 51.8% |
| 매매일 | 364회 (연 30회) |
| CI 95% | [+1.14%, +5.98%] |
| p(>0) | 100% |

---

## 3. 기술 스택 & 인프라

### 3.1 Repository
- **GitHub**: `https://github.com/stanleyim/new` (public)
- **Branch**: main

### 3.2 데이터 소스
- **KIS API** (한국투자증권 OpenAPI)
  - Base URL: `https://openapi.koreainvestment.com:9443`
  - 3개 endpoint:
    - OHLCV: `/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice` (FHKST03010100)
    - 외인-기관: `/uapi/domestic-stock/v1/quotations/inquire-investor` (FHKST01010900)
    - 공매도: `/uapi/domestic-stock/v1/quotations/daily-short-sale` (FHPST04830000)
    - 위험 종목 검증: `/uapi/domestic-stock/v1/quotations/inquire-price` (FHKST01010100)
  - Rate limit: 초당 10건 (안전 설정, KIS 한도 20건/초)
  - 토큰: 24시간 유효
- **pykrx**: GitHub Actions runner IP 차단으로 미사용 (로컬 백테스트 전용)

### 3.3 자동화
- **GitHub Actions**
  - Workflow: `.github/workflows/daily_signal.yml`
  - Cron: `0 11 * * 1-5` (UTC 11:00 = KST 20:00, 월~금)
  - Manual trigger: workflow_dispatch
- **한국 공휴일 체크**: `holidays.SouthKorea()` 자동 반영 + 임시공휴일/대체공휴일 포함
- **주말 체크**: `weekday() >= 5`

### 3.4 알림
- **Telegram Bot**
  - 메시지 제목: `📊 stanleyim/new`
  - 최대 길이 1,608자 (4,096자 한도 내, 안전)
  - 매수 후보 + 보유 종목 + 종결 결과 + 휴장 안내

### 3.5 Secrets (GitHub Actions)
| 변수명 | 용도 |
|---|---|
| `KIS_APP_KEY` | KIS API 인증 키 |
| `KIS_APP_SECRET` | KIS API 시크릿 |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot 토큰 |
| `TELEGRAM_CHAT_ID` | 알림 수신 채팅 ID |

### 3.6 GitHub Token
- Fine-grained token (Repo: `stanleyim/new`)
- 권한: Contents R/W + Workflows R/W
- 만료: 2026-09-19 (90일 주기)
- **만료 전 갱신 필요**

---

## 4. 파일 구조

```
stanleyim/new/
├── .github/workflows/
│   └── daily_signal.yml          # Actions 자동 실행 정의
├── data/
│   ├── universe.parquet          # 439종목 + 시총/거래대금 등 메타
│   ├── ohlcv_full.parquet        # 시가/고가/저가/종가/거래량/등락률
│   ├── flow_full.parquet         # 외인/기관/개인/기타법인/전체
│   └── short_full.parquet        # 공매도 거래량 + 비중
├── output/
│   ├── signals/
│   │   └── YYYY-MM-DD.json       # 매일 신호 산출 결과
│   ├── holdings.json             # 현재 보유 종목 list
│   └── results.json              # 종결 결과 누적
├── signal_runner.py              # 메인 실행 스크립트
├── requirements.txt              # Python 의존성
└── README.md                     # (이 파일 — 시스템 설명서)
```

---

## 5. 운영 흐름 (매일)

### 5.1 자동 실행 (KST 20:00)

```
1. 한국 공휴일/주말 체크
   ├─ 휴장: Telegram "휴장입니다" 알림 + 종료
   └─ 평일: 다음 단계 진행

2. KIS API 데이터 fetch (439종목, 약 4분)
   ├─ OHLCV / 외인-기관 / 공매도 별도 fetch
   ├─ 이미 최신 데이터면 자동 skip
   └─ universe.name 결측 자동 보충

3. Feature 계산 (vol_surge, frgn_z, breakout, hl_range 등)

4. 신호 산출 (n_signals ≥ 3 + 거래대금 ≥ 30억 + signal_score 상위)

5. 위험 종목 자동 필터링 (신호 발생 종목만 inquire-price 추가 검증)
   - 거래정지 / 관리종목 / 투자주의 / 정리매매 / 시장경고 / 공매도과열 제외

6. Holdings 업데이트
   ├─ 기존 보유 종목 현재가 update
   ├─ T+1 도달 종목 entry_price (시가) 자동 채움
   ├─ T+20 도달 종목 종결 → results.json
   ├─ 중복 ticker 진입 방지
   └─ K=5 한도 적용 후 신규 진입

7. JSON 출력
   ├─ output/signals/YYYY-MM-DD.json
   ├─ output/holdings.json
   └─ output/results.json (종결 발생 시)

8. Telegram 알림
   ├─ 매수 후보 (신호 종목)
   ├─ 보유 종목 (T+n, 수익률)
   └─ 종결 결과

9. Git auto commit & push
   ├─ data/ output/ 변경분 commit
   └─ author: stanleyim <imroetaeck@gmail.com>
```

### 5.2 수동 실행
- GitHub → Actions → Daily Signal Run → **Run workflow**
- KST 18시 이후 = 당일 6/30 데이터 완전 공개됨

---

## 6. 출력 JSON 형태

### 6.1 `output/signals/YYYY-MM-DD.json`
```json
{
  "date": "2026-06-30",
  "signal_date": "2026-06-30 00:00:00",
  "signals": [
    {
      "ticker": "005930",
      "name": "삼성전자",
      "signal_score": 5.10,
      "n_signals": 3,
      "SIG1": 1, "SIG2": 0, "SIG3": 1, "SIG4": 0,
      "SIG5": 0, "SIG6": 1, "SIG7": 0,
      "vol_surge": 2.45,
      "frgn_z": 2.81,
      "거래대금_20ma": 123456789012.0,
      "cap_class": "초대형"
    }
  ],
  "n_pick_valid": true,
  "n_open_after": 3,
  "n_closed": 0,
  "n_new": 3
}
```

### 6.2 `output/holdings.json`
```json
[
  {
    "entry_date": "2026-06-30",
    "ticker": "005930",
    "name": "삼성전자",
    "signal_score": 5.10,
    "n_signals": 3,
    "entry_price": 51000.0,
    "entry_t1_date": "2026-07-01",
    "exit_date_planned": "2026-07-29",
    "weight": 0.25,
    "current_price": 52500.0,
    "current_ret": 0.0294,
    "days_held": 5,
    "bdays_remaining": 16
  }
]
```

### 6.3 `output/results.json`
```json
[
  {
    "entry_date": "2026-06-01",
    "exit_date": "2026-06-30",
    "ticker": "005930",
    "name": "삼성전자",
    "entry_price": 50000.0,
    "exit_price": 53000.0,
    "gross_ret": 0.06,
    "net_ret": 0.058,
    "days_held": 21
  }
]
```

---

## 7. 검증 완료 항목 (13개)

| # | 항목 | 상태 |
|---|---|---|
| 1 | 거래정지/관리종목 필터 | ✅ |
| 2 | 권리락/배당락 영향 (위험 없음) | ✅ |
| 3 | 6/30 데이터 자동 fetch | ✅ |
| 4 | T+20 영업일 정확 카운트 | ✅ |
| 5 | Telegram 4096자 제한 (최악 1608자) | ✅ |
| 6 | Auto commit & push | ✅ |
| 7 | Entry_price 자동 채움 (T+1 시가) | ✅ |
| 8 | n_pick<3 매매 보류 | ✅ |
| 9 | K=5 동시 보유 제한 | ✅ |
| 10 | 빈 변경 commit skip | ✅ |
| 11 | 재실행 중복 ticker 방지 | ✅ |
| 12 | dtype 통일 (float64) | ✅ |
| 13 | 외인 단위 ×1e6 일관성 (5종목 검증) | ✅ |

**추가 검증**:
- 단위 (외인/등락률/거래량/공매도/비중) ✅
- 수정주가 (FID_ORG_ADJ_PRC=0) ✅
- name 매핑 (universe로 자동 보충) ✅
- 데이터 무결성 (Drive vs Repo 6/26 일치) ✅
- 한국 공휴일 자동 skip ✅
- 진행 로그 "완료: 439/439 종목" 표시 ✅
- Git author email (`imroetaeck@gmail.com`) ✅

---

## 8. 주요 학습 사항 / 함정

### 8.1 KRX vs KIS
- pykrx = 한국거래소(KRX) IP 차단으로 GitHub Actions에서 작동 ❌
- KIS API = 우회 + 안정 작동 ✅
- **KIS 외인 단위 = 백만원** → `×1e6` 변환 필수

### 8.2 컬럼명 통일
- Drive (pykrx) 컬럼 = `비중`
- KIS 응답 변환 시 동일하게 `비중`으로 저장 → merge 후 `short_ratio` rename
- **컬럼명 불일치 = silent NaN 위험**

### 8.3 T+20 영업일 vs 캘린더
- 캘린더 28일 단순화 = 60% 케이스에서 조기 청산
- **trade_dates 인덱스 + 21 = 정확한 종결일**

### 8.4 pandas 2.x groupby apply
- `include_groups=False` 명시 안 하면 ticker 컬럼 사라짐
- `group_keys=True` + `reset_index(level=0)` = ticker 보존

### 8.5 KIS API 안정성
- `RemoteDisconnected` = `requests.Session` 사용 + 재시도 로직
- Rate limit 안전치 = 초당 10건 (KIS 한도 20건의 50%)

### 8.6 한국 공휴일
- `holidays` 패키지 = 매년 자동 업데이트
- 임시공휴일 / 대체공휴일 자동 반영

---

## 9. 향후 계획 (Phase 단계)

### Phase 1: Forward Test (현재 ~ 2026-08-29)
- 매일 KST 20:00 자동 신호 산출
- 사용자 결정 매매 (시스템 = 권고만)
- 결과 누적: `output/results.json`

### Phase 2: 결과 평가 (2026-08-29 ~ 2026-09-30)
- Forward test 결과 vs Backtest 비교
- 신호별 적중률 / 평균 수익 분석
- 시장 환경 변화 검토

### Phase 3: ML 검토 (2026-12 이후, 데이터 충분 시)
- 단순 룰 vs ML 비교
- Overfit 위험 신중 검토
- v7 final 유지 또는 v8 진화 결정

---

## 10. 운영 주의사항

### 10.1 사용자 책임 영역 (시스템 ❌)
- 손절 (Stop Loss) 설정
- 자금 비중 조절
- 시장 환경 판단
- 실거래 주문

### 10.2 시스템 한계
- backtest 결과 ≠ forward test 보장
- 시장 효율화로 alpha 감소 가능성
- 사용자 인내력에 따라 결과 차이 (중간 손실 견뎌야 평균 alpha 도달)

### 10.3 정기 점검
- **매월 1일**: 신호 발생 빈도 + 적중률 확인
- **2026-09-19 이전**: GitHub Token 갱신
- **분기마다**: universe 종목 신선도 검토 (폐지/신규 상장 반영 여부)

---

## 11. Commit History 주요 변경

| Commit | 내용 | 날짜 |
|---|---|---|
| `0ee3aba` | Add forward test system (v7 final + workflow) | 2026-06-29 |
| `8feddbd` | Switch to KIS API (pykrx removed) | 2026-06-29 |
| `b4efa29` | Session + retry on RemoteDisconnected | 2026-06-29 |
| `dabda46` | Fix ticker column after groupby apply | 2026-06-29 |
| `6319d09` | Fix short_ratio→비중 column name | 2026-06-30 |
| `4cdf8a7` | Fix missing name in KIS fetch | 2026-06-30 |
| `93efc15` | Add safety filter for signal stocks | 2026-06-30 |
| `50c0004` | Fix T+20 to business day accurate count | 2026-06-30 |
| `fed272a` | Auto-fill entry_price from T+1 open | 2026-06-30 |
| `c09bad2` | Fix git author email + fetch log | 2026-06-30 |
| `4c63236` | Add Korean holiday check | 2026-06-30 |
| `502bf62` | Fix merge column name 비중→short_ratio | 2026-06-30 |

---

## 12. 연락처 / 기여자

- **Owner**: stanleyim (imroetaeck@gmail.com)
- **System Development**: stanleyim + Claude (Anthropic)
- **License**: Private (개인 운영)

---

*Last updated: 2026-06-30*
*v7 final forward test 시작: 2026-06-30 KST 20:00*
