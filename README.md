# 영암9 백테스터 — Phase 1 코어

> **계획 문서 (`BACKTEST_WEEKEND_PLAN.md`) 기반, 토요일 작업분 완성**

## 📦 구성

```
backtest/
├── data/                  # SQLite DB 저장소 (자동 생성)
├── results/               # 백테스트 결과 JSON
├── plots/                 # 차트 (Phase 2)
├── fetch_history.py       # 데이터 수집 (pykrx)
├── feature_builder.py     # OHLCV → 전략 입력 dict 변환
├── backtest_engine.py     # 시뮬레이션 엔진
├── metrics.py             # 성과 지표 계산
├── run_backtest.py        # 진입점
└── test_engine.py         # 통합 테스트 (가짜 데이터)
```

## 🚀 사용 순서

### 1️⃣ 사전 준비 (한 번만)
```bash
# pykrx 설치 (KRX 데이터 수집용)
pip install pykrx pandas numpy --break-system-packages

# 동작 확인 (가짜 데이터로 5초 안에 끝남)
cd /home/free4tak/k-bot/stock_bot/backtest
python3 test_engine.py
```

### 2️⃣ 데이터 수집 (5~10분)
```bash
# KOSPI 200 종목, 2년치
python3 fetch_history.py --start 2023-01-01 --top 200 --market KOSPI

# 또는 특정 종목만 (빠른 테스트용)
python3 fetch_history.py --start 2024-01-01 --codes 005930,000660,035720
```

### 3️⃣ 백테스트 실행
```bash
# 단일 시나리오 (현재 설정 그대로)
python3 run_backtest.py --start 2024-01-01 --end 2025-12-31

# 시나리오 비교 (★ 권장)
python3 run_backtest.py --compare --start 2024-01-01 --end 2025-12-31

# 종목 지정 + 임계치 변경
python3 run_backtest.py \
    --codes 005930,000660,035720 \
    --buy-score-min 65 \
    --max-positions 3
```

## 📊 출력 예시

```
시나리오                수익률      CAGR     승률      MDD   샤프     PF   거래
─────────────────────────────────────────────────────────────────────────
기본(임계치60)         +14.32%   +14.32%   58.4%   -8.7%   1.42   1.87    87
보수적(임계치70)       +18.65%   +18.65%   65.1%   -6.2%   1.71   2.31    52
공격적(임계치55)        +9.83%    +9.83%   52.3%  -12.4%   1.08   1.42   124
포지션多(max=8)        +21.45%   +21.45%   56.2%  -14.1%   1.55   1.94   142
```

## ✅ 검증된 항목

- **수수료/슬리피지 정확성**: 수동 계산과 ±1원 오차 (96,656원)
- **Look-ahead bias 방지**: 매수 결정엔 T-1까지 데이터, T+1 시가에 체결
- **전략 코드 재사용**: `strategy.py` / `risk_manager.py` 수정 없이 그대로 호출
- **거래비용**: KRX 표준 (수수료 0.015% + 매도세 0.18% + 슬리피지 0.05%)

## ⚠️ 알려진 한계 (Phase 2에서 보완)

1. **AI 점수 가정**: 현재 `rule_proxy` 모드 — 룰 점수와 동일하게 가정
   - 실전에선 Claude AI가 별도 점수 부여 → 백테스트 결과는 **보수적 추정치**
2. **실시간 외국인 비율 / 매수 압력**: 일봉 데이터로 추정 불가 → 중립값(50)
3. **활성 업종 / 테마**: 과거 시점의 강세 업종 데이터 없음 → 가산점 0
4. **2차 분할매수**: 백테스트에선 비활성화 (켈리 일관성 위해)
5. **Survivorship bias**: 현재 시총 기준 종목 선정 → 상폐 종목 누락

## 🎯 활용 방법

**계획 문서 질문들에 답하기:**

```bash
# "본절보호 -2% vs -3% 어느 게 더 나아?"
# → strategy.py의 STOP_LOSS_AFTER_1ST 값 바꿔가며 비교

# "AI 임계치 55 → 60 → 70 어떤 게 좋을까?"
python3 run_backtest.py --compare  # 위 시나리오에 이미 포함

# "약세장 매수 차단 효과?"
# → BacktestConfig.market_status='weak'로 백테스트
```

## 🛡️ 안전 사용 가이드

> **백테스트 결과 ≠ 실전 수익**
>
> - 백테스트 +30% → 실전 보통 +15~21% (50~70%)
> - 과적합 방지: **3개월 이상 out-of-sample** 검증 필수
> - 단일 시나리오 결과로 전략 변경 ❌ → 최소 4~5개 시나리오 비교
> - 거래 50건 미만이면 통계적 유의성 부족 → 결과 신뢰도 낮음
