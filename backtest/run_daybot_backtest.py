"""
run_daybot_backtest.py — 단타봇(daybot) 백테스트 실행 진입점
================================================================
[사용법]

  # 시나리오 비교 (손절라인/보유기한 튜닝용)
  python3 run_daybot_backtest.py --compare

  # 단일 시나리오
  python3 run_daybot_backtest.py --start 2024-06-01 --end 2026-10-02

[시나리오]
  기본(손절-3.5%/3영업일)   — 현재 실전 설정 그대로
  손절완화(-5%)             — "동국산업 손절 직후 급등" 케이스 검증용
  손절더완화(-7%)
  보유기한단축(2영업일)
  보유기한연장(5영업일)

[주의 — 이 백테스터의 한계]
daybot_backtest_engine.py 모듈 docstring 참고: 키움 조건검색을 과거
데이터로 재생할 수 없어 거래대금+시가갭으로 근사한 후보풀을 쓴다.
실제 키움이 그날 정확히 어떤 종목을 띄웠을지와 다를 수 있음 — 이
백테스트는 "진입 신호가 비슷한 수준일 때 출구 규칙(손절/보유기한)을
어떻게 설정하는 게 나은가"를 보는 용도지, 실거래 성과를 정확히
예측하는 도구가 아니다.
"""
import os
import sys
import json
import argparse
import datetime

from daybot_backtest_engine import DayBotBacktestEngine, DayBotBacktestConfig, DataLoader
from metrics import calc_metrics, format_report, format_comparison

WEEKDAY_NAMES = ["월", "화", "수", "목", "금"]


# ============================================================
# 시나리오 정의
# ============================================================
def get_daybot_scenarios(base: DayBotBacktestConfig) -> list:
    return [
        {"name": "기본(손절-3.5%/3영업일)",
         "config": {**base.__dict__}},
        {"name": "손절완화(-5%)",
         "config": {**base.__dict__, "stop_loss_pct": -5.0}},
        {"name": "손절더완화(-7%)",
         "config": {**base.__dict__, "stop_loss_pct": -7.0}},
        {"name": "보유기한단축(2영업일)",
         "config": {**base.__dict__, "hold_days_limit": 2}},
        {"name": "보유기한연장(5영업일)",
         "config": {**base.__dict__, "hold_days_limit": 5}},
        {"name": "월요일매수제외",
         "config": {**base.__dict__, "exclude_weekdays": [0]}},
        {"name": "금요일매수제외",
         "config": {**base.__dict__, "exclude_weekdays": [4]}},
        {"name": "트레일링단일2.0%(구버전)",
         "config": {**base.__dict__, "use_tiered_trailing": False}},
        {"name": "트레일링타이트1.5%(신버전,기본)",
         "config": {**base.__dict__, "use_tiered_trailing": True}},
    ]


# ============================================================
# 단일 실행
# ============================================================
def run_one(name: str, config: DayBotBacktestConfig, db_path: str) -> dict:
    print(f"\n{'=' * 60}")
    print(f"▶ [DAYBOT] {name}")
    print(f"{'=' * 60}")

    engine = DayBotBacktestEngine(config, db_path)
    engine.run()

    trades = engine.get_trades()
    metrics = calc_metrics(trades, engine.get_equity_curve(), config.initial_cash)
    return {
        "name": name,
        "bot": "daybot",
        "config": {
            "stop_loss_pct": config.stop_loss_pct,
            "hold_days_limit": config.hold_days_limit,
            "base_max_positions": config.base_max_positions,
            "buy_amt_per_slot": config.buy_amt_per_slot,
            "exclude_weekdays": config.exclude_weekdays,
        },
        "metrics": metrics,
        "trades": trades,
        "equity": engine.get_equity_curve(),
    }


# ============================================================
# 요일별 분포 리포트 (금요일 오후/월요일 오전 세기효과 1차 점검용)
# ============================================================
def print_weekday_breakdown(trades: list):
    if not trades:
        print("  (거래 없음)")
        return
    buckets = {i: [] for i in range(5)}
    for t in trades:
        try:
            d = datetime.datetime.strptime(t["buy_date"], "%Y-%m-%d")
        except Exception:
            continue
        wd = d.weekday()
        if wd in buckets:
            buckets[wd].append(t["profit_rate"])

    print(f"\n  {'요일':<6} {'거래수':>6} {'승률':>8} {'평균손익':>10}")
    for wd in range(5):
        rates = buckets[wd]
        if not rates:
            print(f"  {WEEKDAY_NAMES[wd]:<6} {'0':>6}")
            continue
        win_rate = sum(1 for r in rates if r > 0) / len(rates) * 100
        avg = sum(rates) / len(rates) * 100   # profit_rate는 0~1 비율로 저장됨 — 표시용 %환산
        print(f"  {WEEKDAY_NAMES[wd]:<6} {len(rates):>6} {win_rate:>7.1f}% {avg:>+9.2f}%")


# ============================================================
# 결과 요약 출력
# ============================================================
def print_daybot_summary(results: list):
    print(f"\n\n{'=' * 70}")
    print("📊 [DAYBOT] 시나리오 비교")
    print('=' * 70)
    print(format_comparison(results))

    print(f"\n\n{'=' * 70}")
    print("📋 [DAYBOT] 개별 상세")
    print('=' * 70)
    for r in results:
        print()
        print(format_report(r["metrics"], r["name"]))

    base_r = next((r for r in results if "기본" in r["name"]), results[0])
    print(f"\n\n{'=' * 70}")
    print(f"📅 [DAYBOT] 요일별 분포 — {base_r['name']} 기준")
    print('=' * 70)
    print_weekday_breakdown(base_r.get("trades", []))

    m = base_r["metrics"]
    pf = m.get("profit_factor", 0)
    win_rate = m.get("win_rate", 0)
    mdd = m.get("mdd", 0)
    ret = m.get("total_return", 0)
    print(f"\n\n{'=' * 70}")
    print("🎯 [DAYBOT] 판단 기준")
    print('=' * 70)
    print(f"\n  기본 시나리오 성과: 수익률 {ret:+.2f}% | 승률 {win_rate:.1f}% | "
          f"MDD {mdd:.2f}% | PF {pf:.2f}")

    best = max(results, key=lambda r: r["metrics"].get("profit_factor", 0))
    if best["name"] != base_r["name"]:
        print(f"\n  💡 최고 PF 시나리오: {best['name']} "
              f"(PF {best['metrics'].get('profit_factor', 0):.2f}) → 변경 검토")
    else:
        print(f"\n  ✅ 기본 설정이 비교 시나리오 중 최고 PF — 현행 유지 근거")

    print("\n  ⚠️ 이 백테스터는 키움 조건검색을 거래대금+시가갭으로 근사한 "
          "결과입니다. 절대수치보다 시나리오 간 '상대비교'로 해석할 것.")


# ============================================================
# 메인
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="단타봇(daybot) 백테스트")
    parser.add_argument("--start", default="2024-06-01")
    parser.add_argument("--end", default="")
    parser.add_argument("--codes", default="")
    parser.add_argument("--max-codes", type=int, default=200)
    parser.add_argument("--initial-cash", type=int, default=10_000_000)
    parser.add_argument("--buy-amt-per-slot", type=int, default=1_000_000)
    parser.add_argument("--base-max-positions", type=int, default=3)
    parser.add_argument("--stop-loss-pct", type=float, default=-3.5)
    parser.add_argument("--hold-days-limit", type=int, default=3)
    parser.add_argument("--compare", action="store_true")
    parser.add_argument("--db",
                        default=os.path.join(os.path.dirname(__file__), "data", "backtest_data.db"))
    parser.add_argument("--results-dir",
                        default=os.path.join(os.path.dirname(__file__), "results"))
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    os.makedirs(args.results_dir, exist_ok=True)
    end_date = args.end or datetime.date.today().strftime("%Y-%m-%d")

    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    else:
        loader = DataLoader(args.db)
        codes = loader.all_codes()[:args.max_codes]
    print(f"📋 [DAYBOT] 대상 종목 {len(codes)}개")
    if not codes:
        print("❌ 종목 없음 — fetch_history_fdr.py 먼저 실행하세요")
        sys.exit(1)

    base_config = DayBotBacktestConfig(
        initial_cash=args.initial_cash,
        buy_amt_per_slot=args.buy_amt_per_slot,
        base_max_positions=args.base_max_positions,
        stop_loss_pct=args.stop_loss_pct,
        hold_days_limit=args.hold_days_limit,
        start_date=args.start,
        end_date=end_date,
        codes=codes,
        verbose=args.verbose,
    )

    if args.compare:
        scenarios = get_daybot_scenarios(base_config)
        results = []
        for sc in scenarios:
            cfg = DayBotBacktestConfig(**sc["config"])
            results.append(run_one(sc["name"], cfg, args.db))
        print_daybot_summary(results)

        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(args.results_dir, f"daybot_result_{ts}.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2, default=str)
        print(f"\n💾 결과 저장: {out_path}")
    else:
        result = run_one("단일실행", base_config, args.db)
        print(format_report(result["metrics"], "단일실행"))
        print_weekday_breakdown(result["trades"])

        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join(args.results_dir, f"daybot_result_{ts}.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump([result], f, ensure_ascii=False, indent=2, default=str)
        print(f"\n💾 결과 저장: {out_path}")


if __name__ == "__main__":
    main()
