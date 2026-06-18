"""
run_cbot_backtest.py — 코인봇 백테스트 실행 진입점
================================================================
[사용법]
  python3 run_cbot_backtest.py --compare
  python3 run_cbot_backtest.py --start 2026-04-06 --end 2026-05-09

[주의]
  coin_backtest.db 데이터가 약 33일치(4시간봉 200개)뿐이라
  25일 보유기한 로직 검증은 샘플이 매우 적을 수 있음.
  결과는 참고용으로만 활용.
"""
import os
import sys
import json
import argparse
import datetime

from cbot_backtest_engine import CBotBacktestEngine, CBotBacktestConfig
from metrics import calc_metrics, format_report, format_comparison


def get_cbot_scenarios(base: CBotBacktestConfig) -> list:
    return [
        {"name": "기본(임계치55)",   "config": {**base.__dict__, "buy_score_min": 55}},
        {"name": "보수적(임계치65)", "config": {**base.__dict__, "buy_score_min": 65}},
        {"name": "엄격(임계치75)",   "config": {**base.__dict__, "buy_score_min": 75}},
        {"name": "포지션확대(max=5)", "config": {**base.__dict__, "max_positions": 5}},
    ]


def run_one(name: str, config: CBotBacktestConfig, db_path: str) -> dict:
    print(f"\n{'=' * 60}")
    print(f"▶ [CBOT] {name}")
    print(f"{'=' * 60}")

    engine = CBotBacktestEngine(config, db_path)
    engine.run()

    metrics = calc_metrics(
        engine.get_trades(), engine.get_equity_curve(), config.initial_cash,
    )
    return {
        "name": name, "bot": "cbot",
        "config": {
            "buy_score_min": config.buy_score_min,
            "max_positions": config.max_positions,
            "base_buy_amt":  config.base_buy_amt,
        },
        "metrics": metrics,
        "trades":  engine.get_trades(),
        "equity":  engine.get_equity_curve(),
    }


def print_cbot_summary(results: list):
    print(f"\n\n{'=' * 70}")
    print("📊 [CBOT] 시나리오 비교")
    print('=' * 70)
    print(format_comparison(results))

    print(f"\n\n{'=' * 70}")
    print("📋 [CBOT] 개별 상세")
    print('=' * 70)
    for r in results:
        print()
        print(format_report(r["metrics"], r["name"]))

    base_r = next((r for r in results if "기본" in r["name"]), results[0])
    m = base_r["metrics"]
    print(f"\n\n{'=' * 70}")
    print("🎯 [CBOT] 판단 기준")
    print('=' * 70)
    print(f"\n  현재(임계치55) 성과:")
    print(f"    수익률: {m.get('total_return_pct',0):+.2f}% | "
          f"승률: {m.get('win_rate',0):.1f}% | "
          f"MDD: {m.get('mdd',0):.2f}% | PF: {m.get('profit_factor',0) or 0:.2f}")

    best = max(results, key=lambda r: r["metrics"].get("profit_factor", 0) or 0)
    bm = best["metrics"]
    print(f"\n  최고 PF 시나리오: {best['name']}")
    print(f"    수익률: {bm.get('total_return_pct',0):+.2f}% | "
          f"승률: {bm.get('win_rate',0):.1f}% | "
          f"MDD: {bm.get('mdd',0):.2f}% | PF: {bm.get('profit_factor',0) or 0:.2f}")

    print("\n  ⚠️ 데이터 기간이 짧아(약 33일) 참고용 결과입니다.")


def main():
    parser = argparse.ArgumentParser(description="코인봇 백테스트")
    parser.add_argument("--start", default="2026-04-06")
    parser.add_argument("--end",   default="")
    parser.add_argument("--codes", default="")
    parser.add_argument("--max-codes",     type=int, default=30)
    parser.add_argument("--initial-cash",  type=int, default=10_000_000)
    parser.add_argument("--base-buy-amt",  type=int, default=400_000)
    parser.add_argument("--max-positions", type=int, default=3)
    parser.add_argument("--buy-score-min", type=int, default=55)
    parser.add_argument("--compare", action="store_true")
    parser.add_argument("--db", default=os.path.join(
        os.path.dirname(__file__), "..", "backtestc", "coin_backtest.db"))
    parser.add_argument("--results-dir", default=os.path.join(
        os.path.dirname(__file__), "results"))
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    os.makedirs(args.results_dir, exist_ok=True)
    end_date = args.end or datetime.date.today().strftime("%Y-%m-%d")

    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    else:
        import sqlite3
        conn = sqlite3.connect(args.db)
        rows = conn.execute(
            "SELECT DISTINCT code FROM daily_ohlcv LIMIT ?", (args.max_codes,)
        ).fetchall()
        conn.close()
        codes = [r[0] for r in rows]

    print(f"📋 [CBOT] 대상 종목 {len(codes)}개")
    if not codes:
        print("❌ 종목 없음")
        sys.exit(1)

    base_config = CBotBacktestConfig(
        initial_cash=args.initial_cash, base_buy_amt=args.base_buy_amt,
        max_positions=args.max_positions, buy_score_min=args.buy_score_min,
        start_date=args.start, end_date=end_date, codes=codes,
        verbose=args.verbose,
    )

    if args.compare:
        scenarios = get_cbot_scenarios(base_config)
        results = []
        for sc in scenarios:
            cfg = CBotBacktestConfig(**sc["config"])
            results.append(run_one(sc["name"], cfg, args.db))
        print_cbot_summary(results)
    else:
        result = run_one("단일(CBOT)", base_config, args.db)
        print()
        print(format_report(result["metrics"], "단일(CBOT)"))
        results = [result]

    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(args.results_dir, f"cbot_result_{ts}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(
            [{**r, "trades": r["trades"][:50]} for r in results],
            f, ensure_ascii=False, indent=2, default=str,
        )
    print(f"\n💾 결과 저장: {out_path}")


if __name__ == "__main__":
    main()
