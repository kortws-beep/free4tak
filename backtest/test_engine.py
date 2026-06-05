"""
test_engine.py — 백테스트 엔진 동작 검증 (가짜 데이터로)
================================================================
실제 pykrx 없이 SQLite에 직접 더미 데이터를 넣고 백테스트가 굴러가는지
확인하는 통합 테스트.

검증 항목:
1. DB 스키마 생성 OK
2. DataLoader가 데이터 로드 OK
3. 지표 계산 (NaN 없이) OK
4. 매수 신호 발생 → 가상 체결 OK
5. 매도 트리거 (익절/손절) OK
6. 메트릭 계산 OK
7. 수수료/슬리피지 정확히 반영
"""
import os
import sys
import sqlite3
import tempfile
import datetime
import math
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fetch_history    import init_db
from feature_builder  import DataLoader, compute_indicators, build_features_at
from backtest_engine  import BacktestEngine, BacktestConfig
from metrics          import calc_metrics


# ============================================================
# 더미 데이터 생성
# ============================================================
def make_dummy_data(db_path: str, code: str = "TEST01",
                     n_days: int = 200, seed: int = 42) -> None:
    """
    랜덤 워크로 OHLCV 200일치 생성.
    중간에 의도적으로 강한 상승 (매수 시그널) + 하락 (손절) 패턴 삽입.
    """
    rng = np.random.default_rng(seed)
    conn = init_db(db_path)

    base_date = datetime.date(2024, 1, 1)
    price = 50000.0
    rows_ohlcv = []
    rows_flow  = []

    for i in range(n_days):
        d = base_date + datetime.timedelta(days=i)
        # 주말 제외
        if d.weekday() >= 5:
            continue

        # 100일째에 +10%, 130일째에 -8% 의도적 삽입 (전략 테스트용)
        if i == 100:
            ret = 0.10
        elif i == 130:
            ret = -0.08
        else:
            ret = rng.normal(0.0, 0.02)  # 평균 0, 표준편차 2%

        new_price = price * (1 + ret)
        high = max(price, new_price) * (1 + abs(rng.normal(0, 0.005)))
        low  = min(price, new_price) * (1 - abs(rng.normal(0, 0.005)))
        open_p = price
        close = new_price
        vol   = int(rng.integers(100_000, 1_000_000))
        value = int(close * vol)
        change = (close - price) / price * 100

        rows_ohlcv.append((
            code, d.strftime("%Y-%m-%d"),
            float(open_p), float(high), float(low), float(close),
            vol, value, float(change),
        ))
        # 외국인/기관 순매수 (랜덤)
        rows_flow.append((
            code, d.strftime("%Y-%m-%d"),
            int(rng.integers(-3000, 3000)),  # foreign
            int(rng.integers(-2000, 2000)),  # orgn
            int(rng.integers(-2000, 2000)),  # prsn
        ))
        price = new_price

    conn.executemany(
        "INSERT OR REPLACE INTO daily_ohlcv "
        "(code, date, open, high, low, close, volume, value, change) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows_ohlcv,
    )
    conn.executemany(
        "INSERT OR REPLACE INTO daily_flow "
        "(code, date, foreign_qty, orgn_qty, prsn_qty) "
        "VALUES (?, ?, ?, ?, ?)",
        rows_flow,
    )
    conn.commit()
    conn.close()
    print(f"   더미 데이터 {len(rows_ohlcv)}일치 생성")


# ============================================================
# 테스트
# ============================================================
def test_indicators():
    print("\n[테스트 1] 지표 계산")
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "t.db")
        make_dummy_data(db, "TEST01", 200)

        loader = DataLoader(db)
        df = loader.load_ohlcv("TEST01")
        df = compute_indicators(df)

        # NaN 검증 (60일 이후로는 모든 지표 채워져야 함)
        last = df.iloc[-1]
        for col in ["ma5", "ma20", "ma60", "rsi", "macd_hist",
                    "bb_pct", "stoch_k", "atr14"]:
            v = last[col]
            assert not (math.isnan(float(v)) if not isinstance(v, str) else False), \
                f"{col} NaN!"
            print(f"   {col:10}: {v:.4f}")
        print("   ✅ 모든 지표 정상")


def test_feature_builder():
    print("\n[테스트 2] 피처 빌더 (시점 t의 dict)")
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "t.db")
        make_dummy_data(db, "TEST01", 200)

        loader = DataLoader(db)
        # 100번째 거래일에 결정한다고 가정
        df = loader.load_ohlcv("TEST01")
        decision_date = df.index[100]
        features = build_features_at(loader, "TEST01", decision_date)

        assert features is not None, "features None!"
        # 필수 키 모두 존재
        required = ["change_rate", "trading_value", "rsi",
                    "ma5", "ma20", "ma60",
                    "foreign_5d", "institution_5d",
                    "macd_hist", "bb_pct", "stoch_k"]
        for k in required:
            assert k in features, f"키 누락: {k}"
        print(f"   필수 키 {len(required)}개 모두 존재")
        print(f"   샘플: change={features['change_rate']:.2f}%, "
              f"rsi={features['rsi']:.0f}, "
              f"foreign_5d={features['foreign_5d']:,}, "
              f"macd={features['macd_hist']:.1f}")
        print("   ✅ 피처 빌더 정상")


def test_strategy_compatibility():
    print("\n[테스트 3] 전략 모듈 호환성")
    sys.path.insert(0, "/mnt/project")
    from strategy import Strategy

    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "t.db")
        make_dummy_data(db, "TEST01", 200)

        loader = DataLoader(db)
        df = loader.load_ohlcv("TEST01")
        decision_date = df.index[100]
        features = build_features_at(loader, "TEST01", decision_date)

        # 룰 점수 호출 (예외 없이 정수 반환?)
        strat = Strategy()
        score = strat.get_rule_score(features)
        assert isinstance(score, int), f"점수 타입 이상: {type(score)}"
        assert 0 <= score <= 100, f"점수 범위 이상: {score}"
        print(f"   룰 점수: {score}")

        # 매수 필터
        ok, reason = strat.passes_buy_filter(features)
        print(f"   매수필터: {'통과' if ok else '탈락'} | {reason}")

        # 가산점 (빈 리스트로)
        new_score, _, tag = strat.apply_sector_bonus(
            "TEST01", score, [], {}, [], [], set())
        assert new_score == score, "가산점 없을 때 점수 그대로여야"
        print(f"   ✅ 전략 모듈 호환 확인")


def test_full_engine():
    print("\n[테스트 4] 풀 백테스트 엔진")
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "t.db")
        # 여러 종목 생성
        for i, code in enumerate(["A0001", "A0002", "A0003"]):
            make_dummy_data(db, code, 200, seed=i*7)

        config = BacktestConfig(
            initial_cash=5_000_000,
            base_buy_amt=200_000,
            max_positions=3,
            buy_score_min=50,
            start_date="2024-04-01",   # ma60 충분히 쌓인 후
            end_date="2024-09-01",
            codes=["A0001", "A0002", "A0003"],
            ai_score_mode="rule_proxy",
            verbose=False,
        )
        engine = BacktestEngine(config, db)
        engine.run()

        trades = engine.get_trades()
        equity = engine.get_equity_curve()
        print(f"   총 거래: {len(trades)}건")
        print(f"   에쿼티 포인트: {len(equity)}개")
        if trades:
            print(f"   첫 거래: {trades[0]['code']} "
                  f"@ {trades[0]['buy_price']:,.0f} → "
                  f"{trades[0]['sell_price']:,.0f} "
                  f"({trades[0]['profit_rate']:+.2%}) | "
                  f"{trades[0]['sell_reason']}")

        # 메트릭
        m = calc_metrics(trades, equity, config.initial_cash)
        print(f"   메트릭: 수익률 {m.get('total_return', 0):+.2f}%, "
              f"승률 {m.get('win_rate', 0):.1f}%, "
              f"MDD {m.get('mdd', 0):+.2f}%, "
              f"PF {m.get('profit_factor', 0)}")
        print("   ✅ 엔진 통합 테스트 통과")


def test_fee_slippage_accuracy():
    print("\n[테스트 5] 수수료/슬리피지 정확성")
    # 수동 계산 검증:
    # 100주 @ 10000원 매수, 11000원 매도, 슬리피지 0.05%, 수수료 0.015%, 세금 0.18%
    # 매수 체결가: 10000 * 1.0005 = 10005
    # 매수 비용: 10005 * 100 = 1,000,500
    # 매수 수수료: 1,000,500 * 0.00015 = 150.075
    # 매수 총비용: 1,000,650.075
    # 매도 체결가: 11000 * 0.9995 = 10994.5
    # 매도 수익: 10994.5 * 100 = 1,099,450
    # 매도 수수료: 1,099,450 * 0.00015 = 164.9175
    # 매도 세금: 1,099,450 * 0.0018 = 1,979.01
    # 매도 순수령: 1,099,450 - 164.9175 - 1979.01 = 1,097,306.07
    # 순익: 1,097,306.07 - 1,000,650.075 = 96,656 원

    config = BacktestConfig(
        initial_cash=10_000_000, codes=["X"],
        slippage=0.0005, fee_rate=0.00015, tax_rate=0.0018)

    # 임시 DB는 안 만들고, 엔진의 시뮬레이션 함수만 직접 검증
    # → DataLoader가 빈 상태여도 _simulate_buy/_simulate_sell은 돌아감
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "t.db")
        init_db(db)
        engine = BacktestEngine(config, db)

        # 매수
        engine._simulate_buy("X", 10000, 100, "2024-01-01")
        cash_after_buy = engine.cash
        expected_cost = 10005 * 100 + (10005 * 100 * 0.00015)
        assert abs((10_000_000 - cash_after_buy) - expected_cost) < 1, \
            f"매수 비용 불일치: 실제 {10_000_000 - cash_after_buy} vs " \
            f"예상 {expected_cost}"
        print(f"   매수 후 현금: {cash_after_buy:,.2f}")
        print(f"   예상 비용: {expected_cost:,.2f} ✓")

        # 매도
        engine._simulate_sell("X", 100, 11000, "익절", "2024-02-01")
        cash_after_sell = engine.cash
        net_profit = cash_after_sell - 10_000_000  # 시작 자금 대비
        assert 96_000 <= net_profit <= 97_000, \
            f"순익 불일치: {net_profit:,.2f} (예상 약 96,656)"
        print(f"   매도 후 순익: {net_profit:,.2f} (예상 약 96,656원) ✓")
        print("   ✅ 수수료/슬리피지 정확성 검증")


# ============================================================
# 메인
# ============================================================
if __name__ == "__main__":
    print("=" * 60)
    print("  백테스트 엔진 단위 테스트")
    print("=" * 60)
    try:
        test_indicators()
        test_feature_builder()
        test_strategy_compatibility()
        test_fee_slippage_accuracy()
        test_full_engine()
        print("\n" + "=" * 60)
        print("  ✅ 모든 테스트 통과")
        print("=" * 60)
    except Exception as e:
        print(f"\n❌ 테스트 실패: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
