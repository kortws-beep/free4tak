# 영암9 백테스터 — 작업 완료 가이드
**작성일: 2026-05-09 (토)**

---
실행
chmod +x /home/free4tak/k-bot/stock_bot/backtest/weekly_review.sh
./weekly_review.sh 

결과
cd /home/free4tak/k-bot/stock_bot/backtest

python3 generate_combined_report.py \
    --nbot results/result_20260522_080435.json \
    --sbot results/sbot_result_20260522_080525.json \
    --date $(date +%Y-%m-%d)

xdg-open results/weekly_report_$(date +%Y-%m-%d).html

## 1. 오늘 한 일 요약

| 작업 | 결과 |
|---|---|
| 백테스터 코어 구축 | ✅ 완료 |
| 실데이터 수집 (KOSPI 50종목 2년치) | ✅ 24,300건 |
| 수급 데이터 수집 (30일치) | ✅ 1,650건 |
| 수급 자동 누적 cron 등록 | ✅ 매일 17:30 |
| 시나리오 비교 백테스트 실행 | ✅ 완료 |
| 임계치 70으로 봇 조정 | ✅ nbot/sbot 적용 |
| 주간 리뷰 자동화 + HTML 리포트 | ✅ 완료 |

---

## 2. 백테스트 핵심 결과 (2025-08-01 ~ 2026-05-01, 수급없음)

| 시나리오 | 수익률 | 승률 | MDD | PF |
|---|---|---|---|---|
| 기본(임계치60) | -18.68% | 42.1% | -19.5% | 0.67 |
| **보수적(임계치70) ⭐** | **+4.21%** | **49.1%** | **-7.15%** | **1.37** |
| 공격적(임계치55) | -24.83% | 42.6% | -25.0% | 0.62 |
| 포지션多(max=8) | -21.79% | 40.9% | -22.4% | 0.64 |

**결론: 임계치 70이 유일하게 흑자. nbot/sbot에 즉시 적용.**

---

## 3. 파일 구조

```
stock_bot/
├── nbot.py              ← BUY_SCORE_ENTER = 70 (변경됨)
├── sbot.py              ← BUY_SCORE_ENTER = 70 (변경됨)
└── backtest/
    ├── weekly_review.sh      ← 토요일 실행 스크립트 (이것만 실행)
    ├── generate_report.py    ← HTML 리포트 생성기
    ├── fetch_history_fdr.py  ← OHLCV 수집 (FDR, 안정적)
    ├── fetch_investor.py     ← 수급 수집 (KIS, 매일 cron)
    ├── feature_builder.py    ← OHLCV → 지표 변환
    ├── backtest_engine.py    ← 시뮬레이션 엔진
    ├── metrics.py            ← 성과 지표 계산
    ├── run_backtest.py       ← 백테스트 실행 진입점
    ├── data/
    │   └── backtest_data.db  ← 데이터 DB (OHLCV + 수급)
    └── results/
        └── weekly_report_날짜.html  ← 주간 리포트
```

---

## 4. 토요일 루틴 (30분)

### 명령 하나로 끝
```bash
cd /home/free4tak/k-bot/stock_bot/backtest
bash weekly_review.sh
```

### 스크립트가 자동으로 하는 것
1. OHLCV 최신화 (FDR)
2. 수급 최신화 (KIS)
3. 4개 시나리오 백테스트 실행
4. HTML 리포트 생성

### 리포트 열기
```bash
# 생성된 HTML 파일을 브라우저로 열기
xdg-open results/weekly_report_$(date +%Y-%m-%d).html
```

### 리포트 보고 판단 기준

| 결과 | 조치 |
|---|---|
| 임계치70 PF > 1.3, 승률 > 48% | 현행 유지 |
| 임계치70 PF 1.0~1.3 | 관찰 유지 |
| 임계치70 PF < 1.0 | 임계치 75로 상향 검토 |
| 모든 시나리오 적자 | 시장 약세 → 봇 일시 중단 검토 |

---

## 5. 수동 명령어 모음

### 데이터 수집
```bash
cd /home/free4tak/k-bot/stock_bot/backtest

# OHLCV 수집 (2년치, 50종목)
python3 fetch_history_fdr.py \
    --start 2024-05-08 \
    --end 2026-05-09 \
    --top 50

# 수급 수집 (최근 30일치, 매일 갱신)
python3 fetch_investor.py --verbose

# 특정 종목만 수집
python3 fetch_history_fdr.py --codes 005930,000660,035720
```

### 백테스트 실행
```bash
# 4개 시나리오 비교 (추천)
python3 run_backtest.py \
    --compare \
    --start 2025-08-01 \
    --end 2026-05-09 \
    --max-codes 50

# 단일 시나리오
python3 run_backtest.py \
    --start 2025-08-01 \
    --end 2026-05-09 \
    --buy-score-min 70 \
    --max-codes 50

# 특정 종목만
python3 run_backtest.py \
    --codes 005930,000660 \
    --start 2025-08-01 \
    --end 2026-05-09
```

### HTML 리포트 수동 생성
```bash
# 최신 결과로 리포트 생성
python3 generate_report.py \
    results/result_최신파일.json \
    $(date +%Y-%m-%d)
```

### DB 상태 확인
```bash
sqlite3 data/backtest_data.db "
SELECT
    (SELECT COUNT(DISTINCT code) FROM daily_ohlcv) AS ohlcv_종목,
    (SELECT COUNT(*) FROM daily_ohlcv) AS ohlcv_건수,
    (SELECT MIN(date) FROM daily_ohlcv) AS ohlcv_시작,
    (SELECT MAX(date) FROM daily_ohlcv) AS ohlcv_끝,
    (SELECT COUNT(*) FROM daily_flow WHERE foreign_qty != 0) AS 수급_건수,
    (SELECT MAX(date) FROM daily_flow) AS 수급_끝;
"
```

---

## 6. cron 설정 (자동 수급 누적)

```bash
# 현재 등록된 cron 확인
crontab -l | grep fetch

# 결과:
# 30 17 * * 1-5 cd .../backtest && python3 fetch_investor.py >> /tmp/fetch_investor.log 2>&1
```

매일 평일 17:30에 수급 데이터 자동 수집 중.

### 수급 누적 일정
| 시점 | 수급 누적 | 활용도 |
|---|---|---|
| 현재 (오늘) | ~30일치 | 최근 1개월 수급 반영 |
| 3개월 후 (8월) | ~90일치 | ⭐ 본격 수급 포함 백테스트 |
| 6개월 후 (11월) | ~180일치 | 신뢰도 높은 전략 검증 |

---

## 7. 봇 파라미터 변경 이력

### nbot.py
```python
# 변경 전
BUY_SCORE_ENTER = 55

# 변경 후 (2026-05-09, 백테스트 근거)
BUY_SCORE_ENTER = 70
```

### sbot.py
```python
# 변경 전
BUY_SCORE_ENTER = 60

# 변경 후 (2026-05-09, 백테스트 근거)
BUY_SCORE_ENTER = 70
```

**예상 효과:**
- 거래 횟수 -69% (373 → 116건)
- MDD 절반 (-19.5% → -7.1%)
- 수익률 흑자 전환 기대

---

## 8. 백테스터 한계 및 주의사항

### 현재 한계
- **수급 데이터 30일치** — 3개월 후 본격 활용 가능
- **수급 없는 결과** — 실전보다 보수적 (수급 필터 없음)
- **AI 점수 미반영** — rule_proxy 모드 (룰 점수와 동일하게 가정)
- **업종/테마 가산점 없음** — 실전보다 매수 신호 적음

### 결과 해석 원칙
- 백테스트 +X% → 실전 보통 +X×0.5~0.7% (50~70%)
- 거래 50건 미만 → 통계적 신뢰도 낮음
- 단일 시나리오로 전략 변경 금지 → 최소 4~5개 비교
- 9개월 이하 백테스트 → 시장 사이클 부족

### 8월 재검증 계획
수급 90일치 누적 시점에 재실행:
```bash
python3 run_backtest.py \
    --compare \
    --start 2025-11-01 \
    --end 2026-08-01 \
    --max-codes 50
```
이 결과로 임계치/포지션 수/손절비율 종합 재조정.

---

---

## 9. 스윙봇(sbot) 백테스터

### 9-1. nbot과의 차이

| 항목 | nbot | sbot |
|---|---|---|
| 익절선 | +5% / +10% / +15% | +8% / +15% / +25% |
| 손절선 | -5% | -7% (1차 후 본절 -3%) |
| 트레일링 | 고점 -2.5% / -2% | 고점 -4% / -3% |
| 보유기간 | 당일 종가매도 | 최대 11영업일 |
| 대상 시총 | 500억~5만억 | 1조~20조 |
| 매수금액 | 20~30만원 | 50만원~ |
| market_status | 동적 반영 | "normal" 고정 |

### 9-2. 토요일 루틴 (nbot과 동시 실행)

```bash
cd /home/free4tak/k-bot/stock_bot/backtest

# nbot 백테스트 (기존)
python3 run_backtest.py \
    --compare \
    --start 2025-08-01 \
    --end $(date +%Y-%m-%d) \
    --max-codes 50

# sbot 백테스트 (신규)
python3 run_sbot_backtest.py \
    --compare \
    --start 2024-06-01 \
    --end $(date +%Y-%m-%d) \
    --max-codes 50
```

### 9-3. sbot 판단 기준

| 결과 | 조치 |
|---|---|
| PF > 1.3, 승률 > 50%, MDD > -10% | 현행 유지 |
| PF 1.0~1.3 | 관찰 유지 (데이터 누적) |
| PF < 1.0, 최고PF 시나리오 임계치 다름 | 임계치 변경 검토 |
| 모든 시나리오 PF < 1.0 | sbot 운영 재검토 |
| 평균 보유기간 10일↑ 건수 많음 | 손절선 또는 임계치 재검토 |

### 9-4. 8월 재검증 계획

수급 90일치 + sbot 실전 3개월 누적 시점:

```bash
python3 run_sbot_backtest.py \
    --compare \
    --start 2025-11-01 \
    --end 2026-08-01 \
    --max-codes 50
```

이 결과 + 실전 master_trades sbot 성과를 함께 보고
임계치 / 손절선 / 최대 포지션 수 종합 재조정.

---

*영암9 백테스터 v1.1 — 2026-05-22 (sbot 백테스터 추가)*
