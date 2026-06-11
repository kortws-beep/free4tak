"""
run_sbot2_backtest.py — sbot2 중단기 백테스트 실행
================================================================
[사용법]
  cd ~/k-bot/stock_bot
  python3 backtest/run_sbot2_backtest.py

  # 시나리오 비교
  python3 backtest/run_sbot2_backtest.py --scenario all
================================================================
"""
import os
import sys
import json
import argparse
import datetime

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_BASE, "backtest"))

from sbot2_backtest_engine import SBot2BacktestEngine, SBot2BacktestConfig

DB_PATH       = os.path.join(_BASE, "backtest_data.db")
RESULTS_DIR   = os.path.join(_BASE, "backtest", "results")
os.makedirs(RESULTS_DIR, exist_ok=True)

# ── 종목 풀 로드 ──────────────────────────────────────────────
def load_codes(db_path: str, min_trading_value: int = 200,
               limit: int = 200) -> list:
    import sqlite3
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        rows = conn.execute(f"""
            SELECT DISTINCT code
            FROM daily_ohlcv
            WHERE value >= {min_trading_value}
            GROUP BY code
            HAVING COUNT(*) >= 120
            ORDER BY AVG(value) DESC
            LIMIT {limit}
        """).fetchall()
        conn.close()
        codes = [r[0] for r in rows]
        print(f"✅ 종목 풀: {len(codes)}개 (거래대금 {min_trading_value}억+ 기준)")
        return codes
    except Exception as e:
        print(f"❌ 종목 로드 오류: {e}")
        return []


# ── 시나리오 정의 ─────────────────────────────────────────────
SCENARIOS = {
    "base": {
        "name": "기본 (MA20+눌림목+VCP)",
        "config": SBot2BacktestConfig(
            buy_score_min=60,
            use_ma20_filter=True,
            use_pullback=True,
            use_vcp=True,
        ),
    },
    "strict": {
        "name": "엄격 (점수 70+)",
        "config": SBot2BacktestConfig(
            buy_score_min=70,
            use_ma20_filter=True,
            use_pullback=True,
            use_vcp=True,
        ),
    },
    "loose": {
        "name": "완화 (MA20만)",
        "config": SBot2BacktestConfig(
            buy_score_min=55,
            use_ma20_filter=True,
            use_pullback=False,
            use_vcp=False,
        ),
    },
    "no_ma20": {
        "name": "MA20 필터 없음",
        "config": SBot2BacktestConfig(
            buy_score_min=60,
            use_ma20_filter=False,
            use_pullback=True,
            use_vcp=True,
        ),
    },
}


def run_scenario(name: str, scenario: dict, codes: list,
                 start: str, end: str) -> dict:
    cfg        = scenario["config"]
    cfg.codes  = codes
    cfg.start_date = start
    cfg.end_date   = end

    print(f"\n{'='*60}")
    print(f"🧪 시나리오: {scenario['name']}")
    print(f"{'='*60}")

    engine = SBot2BacktestEngine(cfg, DB_PATH)
    engine.run()
    report = engine.get_report()
    report["name"]   = scenario["name"]
    report["config"] = {
        "buy_score_min": cfg.buy_score_min,
        "use_ma20_filter": cfg.use_ma20_filter,
        "use_pullback":  cfg.use_pullback,
        "use_vcp":       cfg.use_vcp,
        "max_positions": cfg.max_positions,
        "base_buy_amt":  cfg.base_buy_amt,
    }
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", default="base",
                        help="base/strict/loose/no_ma20/all")
    parser.add_argument("--start",    default="2024-01-01")
    parser.add_argument("--end",      default="2025-12-31")
    parser.add_argument("--min-tvol", type=int, default=200)
    parser.add_argument("--limit",    type=int, default=200)
    args = parser.parse_args()

    codes = load_codes(DB_PATH, args.min_tvol, args.limit)
    if not codes:
        print("❌ 종목 없음 — DB 확인 필요")
        sys.exit(1)

    results = []
    if args.scenario == "all":
        for name, scenario in SCENARIOS.items():
            r = run_scenario(name, scenario, codes, args.start, args.end)
            results.append(r)
    else:
        scenario = SCENARIOS.get(args.scenario, SCENARIOS["base"])
        r = run_scenario(args.scenario, scenario, codes, args.start, args.end)
        results.append(r)

    # 결과 저장
    ts       = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(RESULTS_DIR, f"sbot2_result_{ts}.json")

    # equity_curve 제외한 가벼운 버전 저장
    save_data = []
    for r in results:
        light = {k: v for k, v in r.items()
                 if k not in ("equity_curve", "trades")}
        light["equity"] = r.get("equity_curve", [])[-1:] # 마지막값만
        save_data.append(light)

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(save_data, f, ensure_ascii=False, indent=2)
    print(f"\n💾 결과 저장: {out_path}")

    # 시나리오 비교표
    if len(results) > 1:
        print(f"\n{'='*70}")
        print(f"{'시나리오':<20} {'수익률':>8} {'승률':>7} {'MDD':>8} {'PF':>6} {'평균보유':>8}")
        print(f"{'-'*70}")
        for r in results:
            print(f"{r['name']:<20} "
                  f"{r['total_return_pct']:>+7.2f}% "
                  f"{r['win_rate']:>6.1f}% "
                  f"{r['max_drawdown_pct']:>7.2f}% "
                  f"{r['profit_factor']:>5.2f} "
                  f"{r['avg_hold_days']:>7.1f}일")
        print(f"{'='*70}")


if __name__ == "__main__":
    main()
