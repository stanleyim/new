# New 리빌드 연구 프로토콜 (Phase C 확정본)

- 기준일: 2026-10-03
- 상태: C1~C5 확정, C5 §8 선확정 ①②③ 종결 / ④ 절차 확정(미결 1건) / ⑤ 남음
- 제안 경로: `docs/research-protocol.md`
- 이 문서는 연구 규칙의 기록이다. 어떤 수익률도 아직 전략 성능으로 해석하지 않았다.
- LOCK: 코드/레포 변경·검사 실행은 별도 승인 후에만. `docs/trial-log.md` 생성은 아직 승인되지 않았다.

## 0. 진행 순서
Phase A(노출 감사, 종료) → B(가격 검증, 종료) → C(프로토콜 확정) → D(Feature 후보·코드).
**Phase D 실행은 C5 §8 선확정 항목이 모두 닫히기 전 금지**(코드 작성·실험·레포 변경·E1 열람 포함).

## 1. 기본 원칙
- 노출등급: E0 미노출 / E1 집계노출 / E2 연구노출(Feature·신호·종목·forward return·성과). E0만 순수 홀드아웃 후보, E1 조건부, E2 제외.
- 홀드아웃은 성과 미반영·시간블록만 사용(랜덤분할 금지).
- 시각검증 불가 소스는 1영업일 지연이 기본. `available_datetime <= signal_datetime`(T일 20:00 KST), event/reported/available 날짜 분리.
- inv/forgn/sbal 3,355 커버리지, fund 31~46일 지연, 2014~17 가용성은 "검증필요". sector_age_days는 메타데이터로 관리. ETF 헤지는 별도 트랙.

## 2. C1 구간·홀드아웃
- **2014-04~2017 = HISTORICAL REFERENCE**(비평가·비튜닝 참고 구간). 정식 E1 아님. 학습·튜닝·평가에 사용하지 않는다. "보조 검증" 명칭 금지.
- **E2(제외 유지)**: 2018~2022 / 2023~2024 / 2026-06-29~. 진짜 OOS는 모델 동결 이후 신규 데이터.
- **E1** = 2025-01-01~2026-06-26 중 G1~G6 통과분만(6/29 이후 미포함). 검증 전용, 1회 열람, 재튜닝 금지. 판정은 메타데이터만(수익률·IC·성과 열람 금지).
- 적격 조건
  - G1: 중복·역순·OHLC 논리위반 0. open=0/volume=0은 거래불능 행으로 별도 집계(허용률 임계 없음). 실거래일 결손은 해당 종목/Feature를 E1에서 제외.
  - G2: 미조정 corporate-action 단차 0건 + DART 등 검증 가능 자료 필요. 검증불가 종목/구간은 제외. **예외(§8-A)**: A 523종목은 DART 커버리지 0%이므로 일반 규칙으로 제외하지 않는다.
  - G3: available_datetime ≤ T 20:00 KST. 검증불가 소스는 1영업일 지연.
  - G4: 마스터 거래일 대비 비정상 공백 0(휴장·상장전·폐지후·정당한 비거래일 제외).
  - G5: 3,355 목록 ↔ 월별 로스터 대조 누락 의심 0. 확인불가 종목은 제외하고 사유 기록.
  - G6: Feature-level gate. 필요 원천 전부가 E1에서 PIT·연속성·완전성을 충족하지 못하면 그 Feature는 E1 검증에서 제외.
- E1 결과를 보고 Feature를 재선택하지 않는다.
- E1 신호일 상한 = 구간 끝 −21거래일. 학습 측 E1 경계 ±21거래일 격리. 롤링 워밍업은 평가 제외(최대 룩백은 Phase D에서 확정).
- 6/29 이후 미조정 7종목(476830, 356860, 327260, 025560, 196170, 183300, 475830)은 수익률 사용 보류 유지.

## 3. C2 Walk-forward
- 개발 데이터 = 2018-01~2024-12 고정(2014~2017은 학습에도 미사용).
- Expanding window, 최소 학습 24개월, 테스트 6개월, 6개월 재학습, 시간 순서.
- 설계 2018~22(내부 OOS 2020-01~2022-12, 튜닝은 여기서만) → 근접 2023~24(튜닝 금지, E2라 독립성 약함) → E1 최종 1회.
- Purge = 21거래일 고정, 모든 테스트 경계 적용. forward-only라 별도 미래방향 embargo 없음.
- horizon 후보 5D/10D/20D. 선택은 2018~2022 설계 영역에서만, 모든 후보 실험을 Trial Log에 기록.
- G5 방법: 3,355 목록 ↔ E1 기간 DART `corp_cls ∈ {KOSPI, KOSDAQ}` + stock_code 대조. 누락 의심 → 원인 확인 → 확인불가 시 제외. DART 무공시 종목은 탐지되지 않는 한계를 명시한다.

## 4. C3 데이터·Target
- 가격: Target은 가격에서 직접 계산(저장된 `등락률` 컬럼 사용 금지). 무거래 행은 NaN, forward-fill 금지. KIS 수정주가와 439 가격계열을 분리. 가격 수준 의존 Feature는 조정 의존성을 명시.
- PIT: 가격·거래량은 T일 20:00 KST, flow/short는 T−1, DART는 접수 다음 영업일. fund/sector/inv/forgn/sbal은 Phase D 전까지 사용 금지.
- Target: T+1 시가 진입 → T+h 종가 청산, h = 5/10/20. T1 절대수익률, T2 횡단면 순위, T3 동일가중 시장 초과수익. 비용 제외 gross. Target과 Feature는 분리.
- 진입 불가(T+1 open=0 또는 volume=0) 라벨을 NaN으로 제외하면 미래 시점 거래 가능 여부로 표본이 결정되므로 선택편향 가능성이 있다. 영향 규모는 검증 전 미확인으로 기록한다.
- 정리매매·저가주 처리(1,000원 필터 등)는 미확정. 근거 없이 숫자를 확정하지 않는다.

## 5. C4 Trial Log
- 레포 내 append-only `docs/trial-log.md`(git 이력 추적, 기존 기록 수정 금지, 정정은 새 항목, 사전등록 → 실행 후 결과 추가). **생성은 별도 승인 필요.**
- 필드: 분류(전략 시도/진단·감사), 가설, 데이터 구간·E등급, 소스·가용 규칙, 유니버스, Target(h·T1~T3·L1/L2), WF 블록, 비용 가정, 판정 기준, 결과, 결정, 스크립트/커밋, 노출 영향, parent_trial_id, code_version, data_version, execution_timestamp.
- 소급 목록: 과거 시도 후보를 근거 수준 [로그 기반]/[메모리 기록 기반]/[미확인]으로 구분. 횟수 미확인이면 숫자를 만들지 않는다. "기각"은 당시 판정 근거가 확인될 때만 쓴다(아니면 "기록상 기각/판정 미확인").
  - 후보: v7 K_MAX/weight 격자, SIG1-7 분해, 역방향 SIG, RSIG1-5, 숏/상대강도 페어, DART 피처 3종, KOSPI200 인버스 헤지 12조합, v7 보정 baseline 재현
  - 진단·감사: Step 1 ①②③, Phase B
- 사전등록 최소 요건: 가설 → 데이터 구간/E등급 → Feature/소스 → Target → horizon → 유니버스 → WF 구조 → 비용 가정 → 판정 기준. 실행 후 결과를 보고 변경하면 새 trial.
- E1은 열람 → 결과 확인 → Feature/Target/임계값 변경 금지. 변경이 필요하면 E1 재최적화 없이 새 개발 실험으로 분리한다.
- Family(예: HORIZON-01 = 5D/10D/20D)와 개별 trial_id를 구분. Family 수 + Trial 수 + E1 열람 횟수를 보고할 수 있어야 한다.
- E1-READ-001은 독립 이벤트로 기록(열람 시각·모델/코드 버전·데이터 버전·E1 적격 집합·목적·결과 확인 여부·이후 변경 여부). E1 열람 횟수는 1회가 원칙.

## 6. C5 평가·비용·Gate
- 비교기준: ① PIT 풀 동일가중 ② 동일 날짜·종목 수 permutation null ③ v7 보정 baseline(323건/Sharpe 0.47/MDD −64.0%는 [메모리 기록 기반 참고치], 재현 전에는 확정 benchmark가 아님).
- 지표
  - Feature: 일별 Spearman IC 평균·t, 분위 스프레드, 블록별 부호 일치율
  - Portfolio: CAGR, Sharpe, MDD, turnover, 승률, worst period, gross/net 병기
  - 거래 수·유효 표본 수 필수 병기
- 비용: **왕복 0.206% 단일 가정**(§8-①). 슬리피지는 사전등록 함수.
- 다중시도: Feature는 permutation null + FDR, Portfolio는 DSR. 최종 보고에 raw·보정 결과·전체 trial 수·family 수.
- Gate 1: 2018~2022에서 사전등록 Feature → permutation null → 사전등록 유의성 기준 → FDR → 시간안정성 → 분위 스프레드로 기준값을 산출·고정.
- Gate 2: 2018~2022 내부 WF의 gross·net·turnover·MDD·Sharpe·거래 수. 2023~24 임계값 조정 금지.
- Gate 3: E1 1회. 실패해도 E1 → 수정 → 재실행 금지.
- Gate 4: 모델 동결 이후 신규 데이터. 최소 기간·최소 거래 수·미달 시 조치는 동결 전 확정. Gate 숫자는 임의로 기재하지 않고 산출 절차로 결정한다.
- L2(마지막 실제 거래 가능일의 실제 가격으로 조기 청산)가 기본 후보, L1(폐지일 이후 NaN)은 민감도 분석으로 병행. 성과로 선택하지 않는다. 선택 기준은 "관측 가능한 실제 거래 가격 사용 + 폐지종목의 체계적 표본 탈락 최소화".
- 진입 불가 선택편향은 진단만 한다(전체 후보 수, T+1 진입 가능/불가 수, 종목·기간별 분포, L1/L2별 유효 라벨 수). 결과로 Target 규칙을 변경하지 않는다.

## 7. C5 §8 선확정 항목 진행 현황

| # | 항목 | 상태 |
|---|---|---|
| ① | 거래세율·수수료율 | 종결 |
| ② | L2 세부 규칙 5개 | 종결 |
| ③ | FDR 적용 단위·family 정의 | 종결 |
| ④ | Gate 1 유의성·시간안정성 기준 | 절차 확정, 미결 1건 |
| ⑤ | Gate 4 최소 기간·최소 거래 수·미달 조치 | 미착수 |

### §8-① 비용
- 비용(왕복)은 0.206% 단일 가정(0.203%는 정정 전 수치). 증권사·계좌별 수수료 조사는 하지 않는다.
- 한계: 과거 거래세율 변동은 반영하지 않는다(C5 원문의 "거래일·시장별 법정 세율"과 다름).

### §8-A G2 예외·G5 정정 (2026-10-03 결정)
- A 523종목은 DART 커버리지가 0%이므로 G2의 일반 규칙으로 제외하지 않는다.
- Phase B 기준선 ①(KIS 수정주가 취급)을 적용한다. DART 분할비율 검증 대신 가격 단차 스캔으로 대체한다.
- 한계: **A의 DART 분할비율 미확인.**
- 목적: E1 구간의 2025년 A 75종목이 통째로 빠지는 생존편향 방지.
- G5의 `corp_cls`는 `{Y,K}`가 아니라 `{KOSPI, KOSDAQ}`로 정정한다.

### §8-② L2 규칙 (2026-10-03 결정, 패턴 기반)
1. 마지막 거래 가능일 = 마지막 `volume > 0`인 날짜
2. 정리매매 시작·종료일은 별도 확정하지 않는다. 데이터상 거래불능 구간으로 처리한다.
3. 정리매매 가격 사용 여부 = 마지막 `volume > 0`일의 종가를 사용하고 이후 행은 사용하지 않는다.
4. 거래정지 상태는 정리매매와 구분하지 않는다. 이후 거래불능 구간으로 처리한다.
5. 마지막 거래일의 `volume = 0`은 마지막 거래일로 인정하지 않는다. 마지막 `volume > 0`일이 기준일이다.

> 한계: 실제 청산 가능 가격과 폐지·정리매매·거래정지의 정확한 상태는 원천 데이터만으로 확인할 수 없으므로, 마지막 volume>0일 종가를 청산 기준으로 사용하는 것은 데이터 기반 대체 규칙이다.

### §8-③ FDR
> FDR은 사전 정의된 경제적 가설별 family 내부에 적용한다. 각 Feature × Horizon × Target 조합을 하나의 검정으로 취급하며, family 구성은 실행 전 Trial Log에 고정하고 결과 확인 후 변경하지 않는다. Benjamini–Hochberg 절차로 family 내부 FDR을 통제하며, 서로 다른 family 간 검정은 통합하지 않는다.

- 유의수준 q의 수치는 C5에 없다. ④에서 확정한다.
- 한계: family 간 보정이 없으므로 시도한 가설이 많을수록 전체 위양성이 늘어난다. 최종 보고에 전체 trial 수·family 수를 병기해 보완한다.

### §8-④ Gate 1 절차 (2026-10-03 결정)
1. q: 수치를 사전 지정하지 않는다. 설계구간 2018~2022 permutation null에서 기준을 산출한 뒤 고정한다.
2. 일별 Spearman IC: Feature × Horizon × Target별 일별 IC 시계열 → IC 평균·t 계산. 최소 통과 기준은 permutation null 기반으로 결정한다.
3. 블록별 부호 일치율: 설계구간을 사전 정의된 시간 블록으로 나누고 블록별 IC 부호를 확인한다. 기준값은 permutation null에서 산출해 고정한다.
4. 분위 스프레드: 방향과 단조성을 함께 평가한다. 기준은 permutation null 기반으로 산출한다.
5. 기준 고정 원칙: 모든 Gate 1 기준은 2018~2022 설계구간의 permutation null에서 산출하고, 산출 후 고정한다. 이후 관측구간의 성과를 보고 기준을 바꾸지 않는다.
6. 구체 숫자(q, IC 평균/t, 블록 부호 일치율, 분위 스프레드 방향·단조성)는 산출 단계에서 확정한다. 관행적 임계값을 사전에 삽입하지 않는다.

**미결 1건**: null에서 기준을 뽑으려면 어느 percentile을 쓸지와 q 선택이 필요하다. 이 선택은 실제 Feature 결과를 열람하기 전에 고정해야 한다(연구자 자유도 차단).

### §8-⑤ 미착수
- Gate 4의 최소 기간·최소 거래 수·미달 시 조치(예: 기간·거래 수 미달 = 판정 보류, 사전 위험 한계 위반 = 실패)를 동결 전에 확정한다.

## 부록 A. L2 점검 결과 (A 523종목, 2026-10-03, Colab 재현 확인)
점검 코드는 부록 B. 재현 기준: 데이터 브랜치 clone 시점(delisted-data 2,916 parquet, 마지막 행 2026-09-23).

**① 마지막 행 날짜 ↔ sector 로스터 마지막 관측일**
- 마지막 행이 마지막 관측일보다 빠른 종목 0개, 같은 날 17개, 이후 506개. 차이 중앙값 13일, 75% 21일 이내, 95% 28일 이내.
- 마지막 행이 "로스터에서 처음 사라진 스냅샷일" 이후인 종목 0개.
- 31일 초과 9개는 sector_2014의 8~12월 공백 구간 종목.

**② 마지막 30행 volume=0·가격 패턴**
- 마지막 행 volume=0이 201개(38.4%). 마지막 volume>0일 이후 행이 남은 종목 198개 중 197개는 OHLC가 전부 직전종가인 flat 행이고 1개(081210)는 flat이 아니다.
- 꼬리 길이 중앙값 14(12~15행이 대부분), 최대 1,016. 30행 초과 6개(058220은 1,016행, 058530은 933행).
- 꼬리가 없는 322개 중 약 60%(Colab 194개 / 샌드박스 190개, 정의 차이)는 마지막 7행 누적 −50% 이하. 꼬리가 있는 198개 중에는 0개.
- 마지막 30행 open=0 종목 0개. 마지막 행 OHLC 논리위반 0건. 마지막 종가 1,000원 이하 180개.
- 마지막 30행 volume=0 평균 비율 약 0.48~0.49.
- volume=0 행 전체 86,081행 중 86,072행이 flat(꼬리 4,898행, 내부 81,183행/445종목). 거래량이 한 번도 없는 종목 3개(002797, 00341A, 00341D).
- **미확인**: volume=0 행이 거래정지인지 단순 무거래인지는 구분 불가(상태 컬럼 없음). −50% 이하 누적하락이 정리매매인지는 정황일 뿐. 마지막 30행 내 5일 초과 날짜 공백 64종목의 원인은 미조사.

**③ DART 공시 유형**
- 관련 유형은 존재한다: 상장폐지 1,564건/362종목, 정리매매 58건/28종목, 거래정지 4,181건/1,223종목, 관리종목 637건/266종목, 상장적격성 1,345건/258종목.
- `주권매매거래정지해제(상장폐지에따른정리매매개시)` 25건(24종목, 196490이 2회). 전부 B 소속.
- `상장폐지결정`에는 코스닥시장이전상장 42건이 섞여 있어 폐지 확정 신호로 쓸 수 없다.
- **A 523종목 중 DART에 행이 있는 종목 0개**(B는 2,393개 중 2,204개). 마지막 관측이 2018 이상인 354종목도 0개, 169종목은 DART 수집 범위(2018~) 밖. 상장폐지 키워드 종목은 KIS 318 / 439 44.
- **미확인**: A가 DART에 없는 원인(수집이 현재 상장 코드 기준인지 등).
- 정리매매개시 24종목은 로스터 마지막 관측 2026-09-01, KIS 마지막 행 2026-09-23. 22종목은 마지막 행 volume=0, 192410과 196490은 volume>0.

## 부록 B. 점검 코드 (Colab)
셀 1(clone)은 셀 2·3 앞에 실행한다. `MAIN`, `c`, `np`는 셀 2에서 정의된다.

```python
# 셀 1
import os, subprocess
repo = "https://github.com/stanleyim/new.git"
for br, d in [("main","main"),("delisted-data","delisted"),("dart-data","dart"),("pit-data","pit")]:
    if not os.path.exists(f"/content/{d}"):
        subprocess.run(["git","clone","--depth","1","--single-branch","-b",br,repo,f"/content/{d}"],check=True)
print(len(os.listdir("/content/delisted/delisted_data")), os.listdir("/content/dart/dart_data"), len(os.listdir("/content/pit/pit_data_extra")))
```

```python
# 셀 2 — 점검 ①②
import pandas as pd, numpy as np, glob
MAIN,DEL,PIT="/content/main","/content/delisted/delisted_data","/content/pit/pit_data_extra"
c=pd.read_csv(f"{MAIN}/delisted_candidates_2916.csv",dtype=str)
A=c[c.last_year.astype(int)<=2025].copy(); print("A",len(A))
rd=pd.DatetimeIndex(sorted(pd.concat([pd.read_parquet(f,columns=["date"]) for f in sorted(glob.glob(f"{PIT}/sector_*.parquet"))]).date.unique()))
rows=[]
for _,r in A.iterrows():
    k=pd.read_parquet(f"{DEL}/{r.ticker}.parquet").sort_values("date").reset_index(drop=True); k["date"]=pd.to_datetime(k.date)
    sl=pd.Timestamp(r.last_date); nx=rd[rd>sl]; nx=nx[0] if len(nx) else pd.NaT
    L=k.tail(30); ret=k.close.pct_change(); z=(k.volume==0).values
    run=0
    for v in z[::-1]:
        if v: run+=1
        else: break
    pv=np.where(k.volume.values>0)[0]; lp=pv[-1] if len(pv) else -1
    tail=k.iloc[lp+1:] if lp>=0 else k
    pc=k.close.iloc[lp] if lp>=0 else np.nan
    rows.append(dict(ticker=r.ticker,sec_last=sl,kis_last=k.date.iloc[-1],next_roster=nx,diff=(k.date.iloc[-1]-sl).days,
      kis_after_next=bool(pd.notna(nx) and k.date.iloc[-1]>=nx),last_vol0=int(k.volume.iloc[-1]==0),trail_run=run,
      vol0_l30=int((L.volume==0).sum()),open0_l30=int((L.open==0).sum()),
      tail_rows=len(tail) if lp>=0 else np.nan,
      tail_flat=bool(((tail.open==pc)&(tail.high==pc)&(tail.low==pc)&(tail.close==pc)).all()) if lp>=0 and len(tail) else None,
      cum7=(k.close.iloc[lp]/k.close.iloc[lp-7]-1) if lp>=7 else np.nan,
      big7=int((k.close.iloc[max(lp-7,0):lp+1].pct_change().abs()>0.30).sum()) if lp>=1 else 0,
      last_close=k.close.iloc[-1],ohlc_bad=int(not(L.iloc[-1].low<=min(L.iloc[-1].open,L.iloc[-1].close) and L.iloc[-1].high>=max(L.iloc[-1].open,L.iloc[-1].close)))))
d=pd.DataFrame(rows)
print("① diff<0:",(d["diff"]<0).sum()," ==0:",(d["diff"]==0).sum()," >0:",(d["diff"]>0).sum(),"| KIS>=다음스냅샷:",d.kis_after_next.sum())
print(d["diff"].quantile([.5,.75,.95]).to_dict()," >31일:",(d["diff"]>31).sum())
print("② 마지막행 vol=0:",d.last_vol0.sum(),"/",len(d)," 꼬리있음:",(d.tail_rows>0).sum()," 꼬리 flat:",d.tail_flat.value_counts(dropna=False).to_dict())
print("꼬리 길이:",d[d.tail_rows>0].tail_rows.describe().round(1).to_dict(),"| >30행:",(d.tail_rows>30).sum())
print("꼬리없음 중 cum7<-50%:",((d.tail_rows==0)&(d.cum7<-0.5)).sum(),"/",(d.tail_rows==0).sum()," 꼬리있음 중:",((d.tail_rows>0)&(d.cum7<-0.5)).sum(),"/",(d.tail_rows>0).sum())
print("마지막30행 open=0 종목:",(d.open0_l30>0).sum()," vol0 평균비율:",round(d.vol0_l30.mean()/30,3)," 종가<=1000:",(d.last_close<=1000).sum()," OHLC위반:",d.ohlc_bad.sum())
```

```python
# 셀 3 — 점검 ③ (DART)
D=pd.concat([pd.read_csv(f,dtype=str,low_memory=False) for f in sorted(glob.glob("/content/dart/dart_data/20*.csv"))],ignore_index=True)
D["stock_code"]=D.stock_code.str.replace(r"\.0$","",regex=True)
m=D.stock_code.str.fullmatch(r"\d+",na=False); D.loc[m,"stock_code"]=D.loc[m,"stock_code"].str.zfill(6)
D["rcept_dt"]=pd.to_datetime(D.rcept_dt,format="%Y%m%d")
rn=D.report_nm.fillna("").str.replace(r"\s+","",regex=True)
print("rows",len(D),D.corp_cls.value_counts().to_dict())
for k in ["상장폐지","정리매매","거래정지","관리종목","상장적격성"]:
    mm=rn.str.contains(k); print(f"{k}: {mm.sum()}건 / {D[mm].stock_code.nunique()}종목")
for k in ["정리매매","상장폐지"]:
    print("##",k); print(rn[rn.str.contains(k)].value_counts().head(8))
u=set(pd.read_parquet(f"{MAIN}/data/universe.parquet").ticker.astype(str).str.zfill(6))
cs=c.set_index("ticker"); grp=lambda t:"439" if t in u else ("KIS" if t in cs.index else "none")
codes=set(D.stock_code)
c["inD"]=c.ticker.isin(codes); c["grp"]=np.where(c.last_year.astype(int)<=2025,"A","B")
print(c.groupby("grp").inD.agg(["sum","count"]))
print("A 마지막관측>=2018:",((c.grp=="A")&(c.last_year.astype(int)>=2018)).sum())
j=D[rn.str.contains("정리매매개시")][["stock_code","corp_name","rcept_dt"]]
print("정리매매개시 종목 소속:",j.stock_code.map(grp).value_counts().to_dict())
print("상장폐지키워드 종목 소속:",D[rn.str.contains("상장폐지")].stock_code.drop_duplicates().map(grp).value_counts().to_dict())
```
