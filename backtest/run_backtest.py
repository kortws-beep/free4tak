"""
run_backtest.py — 백테스트 실행 진입점
================================================================
[사용]
  # 단일 시나리오
  python3 run_backtest.py --start 2024-01-01 --end 2025-12-31

  # 시나리오 비교 (현재 vs 임계치 변경)
  python3 run_backtest.py --compare

  # 종목 지정
  python3 run_backtest.py --codes 005930,000660,035720
"""
import os
import sys
import json
import argparse
import datetime

# 자체 모듈
from feature_builder import DataLoader
from backtest_engine import BacktestEngine, BacktestConfig
from metrics import calc_metrics, format_report, format_comparison


# ============================================================
# 시나리오 정의
# ============================================================
def get_scenarios(base: BacktestConfig) -> list:
    """핵심 시나리오 + 익절 구조 최적화"""
    return [
        # 베이스라인
        {
            "name": "기본(임계치60,max=5)",
            "config": {**base.__dict__, "buy_score_min": 60, "max_positions": 5},
        },
        {
            "name": "시간청산 3일 적용",
            "config": {**base.__dict__, "buy_score_min": 60, "max_positions": 5, "time_stop_days": 3},
        },
        {
            "name": "시간청산 5일 적용",
            "config": {**base.__dict__, "buy_score_min": 60, "max_positions": 5, "time_stop_days": 5},
        },
        {
            "name": "보수적(임계치70,max=5)",
            "config": {**base.__dict__, "buy_score_min": 70, "max_positions": 5},
        },
        {
            "name": "공격적(max=8,임계치60)",
            "config": {**base.__dict__, "buy_score_min": 60, "max_positions": 8},
        },

        # 손절선 비교
        {
            "name": "손절-5%+max5",
            "config": {**base.__dict__, "buy_score_min": 60, "max_positions": 5,
                       "stop_loss_rate": -0.05},
        },
        {
            "name": "손절-7%+max5(현재)",
            "config": {**base.__dict__, "buy_score_min": 60, "max_positions": 5,
                       "stop_loss_rate": -0.07},
        },
        {
            "name": "손절-10%+max5",
            "config": {**base.__dict__, "buy_score_min": 60, "max_positions": 5,
                       "stop_loss_rate": -0.10},
        },

        # 익절 구조 최적화 (손절-7% 기준)
        # 현재: 1차+5%/30% + 2차+10%/40% + 3차+15%/30%
        {
            "name": "익절축소(1차3%,2차6%,3차10%)+손절7%",
            "config": {**base.__dict__, "buy_score_min": 60, "max_positions": 5,
                       "stop_loss_rate": -0.07,
                       "sell_1st_rate": 0.03, "sell_2nd_rate": 0.06, "sell_3rd_rate": 0.10},
        },
        {
            "name": "2단계익절(1차5%50%,2차10%50%)+손절7%",
            "config": {**base.__dict__, "buy_score_min": 60, "max_positions": 5,
                       "stop_loss_rate": -0.07,
                       "sell_1st_rate": 0.05, "sell_2nd_rate": 0.10, "sell_3rd_rate": 0.10,
                       "sell_1st_qty_pct": 0.5, "sell_2nd_qty_pct": 0.5},
        },
        {
            "name": "트레일링전량(5%진입,3%트레일)+손절7%",
            "config": {**base.__dict__, "buy_score_min": 60, "max_positions": 5,
                       "stop_loss_rate": -0.07,
                       "sell_1st_rate": 0.05, "trail_1st": 0.03, "trail_mode": "full"},
        },

        # 익절 구조 최적화 (손절-10% 기준)
        {
            "name": "2단계익절(1차5%50%,2차10%50%)+손절10%",
            "config": {**base.__dict__, "buy_score_min": 60, "max_positions": 5,
                       "stop_loss_rate": -0.10,
                       "sell_1st_rate": 0.05, "sell_2nd_rate": 0.10, "sell_3rd_rate": 0.10,
                       "sell_1st_qty_pct": 0.5, "sell_2nd_qty_pct": 0.5},
        },
        {
            "name": "익절축소(1차3%,2차6%,3차10%)+손절10%",
            "config": {**base.__dict__, "buy_score_min": 60, "max_positions": 5,
                       "stop_loss_rate": -0.10,
                       "sell_1st_rate": 0.03, "sell_2nd_rate": 0.06, "sell_3rd_rate": 0.10},
        },

        # ★ 무작위 진입 검증
        {
            "name": "랜덤진입(현재손절-10%)",
            "config": {**base.__dict__, "buy_score_min": 40, "max_positions": 5,
                       "ai_score_mode": "random", "stop_loss_rate": -0.10},
        },
        {
            "name": "랜덤진입+시간청산5일",
            "config": {**base.__dict__, "buy_score_min": 40, "max_positions": 5,
                       "ai_score_mode": "random", "stop_loss_rate": -0.10,
                       "time_stop_days": 5},
        },
        {
            "name": "랜덤진입+타이트손절(-5%)",
            "config": {**base.__dict__, "buy_score_min": 40, "max_positions": 5,
                       "ai_score_mode": "random", "stop_loss_rate": -0.05},
        },

        # ★ 무작위 진입 검증 — 리스크 관리 방어력 순수 테스트
        {
            "name": "랜덤진입(현재손절)",
            "config": {**base.__dict__,
                       "buy_score_min": 40,   # 임계치 낮춰 최대한 많이 진입
                       "max_positions": 5,
                       "ai_score_mode": "random",
                       "stop_loss_rate": -0.10},
        },
        {
            "name": "랜덤진입+시간청산5일",
            "config": {**base.__dict__,
                       "buy_score_min": 40,
                       "max_positions": 5,
                       "ai_score_mode": "random",
                       "stop_loss_rate": -0.10,
                       "time_stop_days": 5},
        },
        {
            "name": "랜덤진입+타이트손절(-5%)",
            "config": {**base.__dict__,
                       "buy_score_min": 40,
                       "max_positions": 5,
                       "ai_score_mode": "random",
                       "stop_loss_rate": -0.05},
        },

        # ★ 무작위 진입 검증 — 리스크 관리 방어력 순수 테스트
        {
            "name": "랜덤진입(손절-10%)",
            "config": {**base.__dict__, "buy_score_min": 40, "max_positions": 5,
                       "ai_score_mode": "random", "stop_loss_rate": -0.10},
        },
        {
            "name": "랜덤진입+시간청산5일",
            "config": {**base.__dict__, "buy_score_min": 40, "max_positions": 5,
                       "ai_score_mode": "random", "stop_loss_rate": -0.10,
                       "time_stop_days": 5},
        },
        {
            "name": "랜덤진입+타이트손절(-5%)",
            "config": {**base.__dict__, "buy_score_min": 40, "max_positions": 5,
                       "ai_score_mode": "random", "stop_loss_rate": -0.05},
        },

        # ★ 하락장 시나리오
        {
            "name": "하락장만(코스피-3%이하)+현재설정",
            "config": {**base.__dict__, "buy_score_min": 60, "max_positions": 5,
                       "stop_loss_rate": -0.07,
                       "crash_only": True},
        },
        {
            "name": "하락장만+반등2회매수+익절축소",
            "config": {**base.__dict__, "buy_score_min": 60, "max_positions": 5,
                       "stop_loss_rate": -0.07,
                       "crash_only": True,
                       "rebound_buy": True,
                       "rebound_thresh": 1.0,
                       "sell_1st_rate": 0.03, "sell_2nd_rate": 0.06, "sell_3rd_rate": 0.10},
        },
        {
            "name": "반등2회매수+익절축소+손절7%(전체장)",
            "config": {**base.__dict__, "buy_score_min": 60, "max_positions": 5,
                       "stop_loss_rate": -0.07,
                       "rebound_buy": True,
                       "rebound_thresh": 1.0,
                       "sell_1st_rate": 0.03, "sell_2nd_rate": 0.06, "sell_3rd_rate": 0.10},
        },
    ]




# ============================================================
# 단일 백테스트 실행
# ============================================================
def run_one(name: str, config: BacktestConfig, db_path: str) -> dict:
    """한 시나리오 실행 → 메트릭 반환"""
    print(f"\n{'=' * 60}")
    print(f"▶ 실행: {name}")
    print(f"{'=' * 60}")

    engine = BacktestEngine(config, db_path)
    engine.run()

    metrics = calc_metrics(
        engine.get_trades(),
        engine.get_equity_curve(),
        config.initial_cash,
    )
    return {
        "name": name,
        "config": {
            "buy_score_min":  config.buy_score_min,
            "max_positions":  config.max_positions,
            "base_buy_amt":   config.base_buy_amt,
            "ai_score_mode":  config.ai_score_mode,
        },
        "metrics": metrics,
        "trades":  engine.get_trades(),
        "equity":  engine.get_equity_curve(),
    }


# ============================================================
# 메인
# ============================================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end",   default="2025-12-31")
    parser.add_argument("--codes", default="",
                        help="쉼표구분 종목코드 (비우면 DB의 모든 종목)")
    parser.add_argument("--max-codes", type=int, default=50,
                        help="자동 선택 시 종목 개수 상한")
    parser.add_argument("--initial-cash", type=int, default=5_000_000)
    parser.add_argument("--base-buy-amt", type=int, default=200_000)
    parser.add_argument("--max-positions", type=int, default=5)
    parser.add_argument("--buy-score-min", type=int, default=60)
    parser.add_argument("--ai-mode", default="rule_proxy",
                        choices=["fixed", "rule_proxy"])
    parser.add_argument("--compare", action="store_true",
                        help="시나리오 비교 모드")
    parser.add_argument("--db",
                        default=os.path.join(
                            os.path.dirname(__file__), "data", "backtest_data.db"))
    parser.add_argument("--results-dir",
                        default=os.path.join(
                            os.path.dirname(__file__), "results"))
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    # 결과 디렉토리 보장
    os.makedirs(args.results_dir, exist_ok=True)

    # 종목 리스트 결정
    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    else:
        loader = DataLoader(args.db)
        codes = loader.all_codes()[:args.max_codes]
    print(f"📋 대상 종목 {len(codes)}개")
    if not codes:
        print("❌ 종목 없음 — fetch_history.py 먼저 실행하세요")
        sys.exit(1)

    # 기본 설정
    base_config = BacktestConfig(
        initial_cash    = args.initial_cash,
        base_buy_amt    = args.base_buy_amt,
        max_positions   = args.max_positions,
        buy_score_min   = args.buy_score_min,
        start_date      = args.start,
        end_date        = args.end,
        codes           = codes,
        ai_score_mode   = args.ai_mode,
        verbose         = args.verbose,
    )

    # 실행
    if args.compare:
        scenarios = get_scenarios(base_config)
        results = []
        for sc in scenarios:
            cfg = BacktestConfig(**sc["config"])
            results.append(run_one(sc["name"], cfg, args.db))

        # 비교 출력
        print(f"\n\n{'=' * 70}")
        print("📊 시나리오 비교")
        print('=' * 70)
        print(format_comparison(results))

        # 개별 상세
        print(f"\n\n{'=' * 70}")
        print("📋 개별 상세")
        print('=' * 70)
        for r in results:
            print()
            print(format_report(r["metrics"], r["name"]))

    else:
        result = run_one("단일 백테스트", base_config, args.db)
        print()
        print(format_report(result["metrics"], "단일 백테스트"))
        results = [result]

    # 결과 저장
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(args.results_dir, f"result_{ts}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(
            [{**r, "trades": r["trades"][:50]} for r in results],  # trades 일부만
            f, ensure_ascii=False, indent=2, default=str,
        )
    print(f"\n💾 결과 저장: {out_path}")


if __name__ == "__main__":
    main()
