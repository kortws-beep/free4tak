"""
run_sbot_backtest.py — 스윙봇 백테스트 실행 진입점
================================================================
[사용법]

  # 시나리오 비교 (토요일 루틴 — 이것만 쓰면 됨)
  python3 run_sbot_backtest.py --compare

  # 단일 시나리오
  python3 run_sbot_backtest.py --start 2024-06-01 --end 2026-05-22

  # 특정 종목만
  python3 run_sbot_backtest.py --compare --codes 005930,000660,035720

[시나리오 4개]
  기본(임계치75)         — 현재 실전 임계치 (점수분포 반영)
  보수적(임계치80)       — 높은 확신만 진입
  엄격(임계치85)        — 강한 신호만
  최엄격(임계치90)      — 최고점만
  포지션확대(max=3)      — 분산 투자 효과 검증

[판단 기준]
  PF > 1.3, 승률 > 50%, MDD < -10%  → 현행 유지 or 상향 검토
  PF 1.0~1.3                          → 관찰 유지
  PF < 1.0                            → 임계치 상향 검토
  모든 시나리오 PF < 1.0              → sbot 운영 재검토
"""
import os
import sys
import json
import argparse
import datetime

from feature_builder      import DataLoader
from sbot_backtest_engine import SBotBacktestEngine, SBotBacktestConfig
from metrics              import calc_metrics, format_report, format_comparison


# ============================================================
# sbot 시나리오 정의
# ============================================================
def get_sbot_scenarios(base: SBotBacktestConfig) -> list:
    """
    스윙봇 비교 시나리오.
    ★ SwingStrategy.get_rule_score()는 대형주 위주로 80~100점 집중
      → 60~70 구간은 의미 없음, 75~90 구간으로 재설계
    핵심 변수: 임계치(buy_score_min) + 최대 포지션 수
    """
    return [
        {
            "name": "기본(임계치75)",
            "config": {**base.__dict__, "buy_score_min": 75},
        },
        {
            "name": "보수적(임계치80)",
            "config": {**base.__dict__, "buy_score_min": 80},
        },
        {
            "name": "엄격(임계치85)",
            "config": {**base.__dict__, "buy_score_min": 85},
        },
        {
            "name": "최엄격(임계치90)",
            "config": {**base.__dict__, "buy_score_min": 90},
        },
        {
            "name": "포지션확대(max=3,임계치80)",
            "config": {**base.__dict__,
                       "buy_score_min": 80,
                       "max_positions": 3},
        },
    ]


# ============================================================
# 단일 실행
# ============================================================
def run_one(name: str, config: SBotBacktestConfig, db_path: str) -> dict:
    print(f"\n{'=' * 60}")
    print(f"▶ [SBOT] {name}")
    print(f"{'=' * 60}")

    engine = SBotBacktestEngine(config, db_path)
    engine.run()

    metrics = calc_metrics(
        engine.get_trades(),
        engine.get_equity_curve(),
        config.initial_cash,
    )
    return {
        "name":    name,
        "bot":     "sbot",
        "config": {
            "buy_score_min": config.buy_score_min,
            "max_positions": config.max_positions,
            "base_buy_amt":  config.base_buy_amt,
            "ai_score_mode": config.ai_score_mode,
        },
        "metrics": metrics,
        "trades":  engine.get_trades(),
        "equity":  engine.get_equity_curve(),
    }


# ============================================================
# 결과 요약 출력 — 토요일 판단 기준 포함
# ============================================================
def print_sbot_summary(results: list):
    """시나리오 비교 + 판단 기준 출력"""
    print(f"\n\n{'=' * 70}")
    print("📊 [SBOT] 시나리오 비교")
    print('=' * 70)
    print(format_comparison(results))

    print(f"\n\n{'=' * 70}")
    print("📋 [SBOT] 개별 상세")
    print('=' * 70)
    for r in results:
        print()
        print(format_report(r["metrics"], r["name"]))

    # ── 토요일 판단 기준 자동 출력 ──────────────────────────
    print(f"\n\n{'=' * 70}")
    print("🎯 [SBOT] 토요일 판단 기준")
    print('=' * 70)

    # 기본(임계치75) 기준 시나리오 찾기
    base_r = next((r for r in results if "기본" in r["name"]), results[0])
    m = base_r["metrics"]
    pf        = m.get("profit_factor", 0)
    win_rate  = m.get("win_rate", 0)
    mdd       = m.get("mdd", 0)
    ret       = m.get("total_return_pct", 0)

    print(f"\n  현재(임계치75) 성과:")
    print(f"    수익률: {ret:+.2f}% | 승률: {win_rate:.1f}% | "
          f"MDD: {mdd:.2f}% | PF: {pf:.2f}")

    # 최적 시나리오 찾기
    best = max(results, key=lambda r: r["metrics"].get("profit_factor", 0))
    best_m = best["metrics"]
    print(f"\n  최고 PF 시나리오: {best['name']}")
    print(f"    수익률: {best_m.get('total_return_pct',0):+.2f}% | "
          f"승률: {best_m.get('win_rate',0):.1f}% | "
          f"MDD: {best_m.get('mdd',0):.2f}% | "
          f"PF: {best_m.get('profit_factor',0):.2f}")

    print(f"\n  판단:")
    if pf >= 1.3 and win_rate >= 50 and mdd >= -10:
        print("  ✅ 현행 유지 — PF/승률/MDD 모두 기준 충족")
    elif pf >= 1.0:
        print("  ⚠️ 관찰 유지 — 데이터 더 누적 후 재검토")
        if best["name"] != base_r["name"]:
            best_score = best["config"]["buy_score_min"]
            print(f"  💡 임계치 {best_score}점 시나리오가 더 좋음 → 변경 검토")
    else:
        print("  🔴 임계치 상향 또는 운영 재검토 필요")

    # 보유기간 분석
    all_trades = []
    for r in results[:1]:  # 기본 시나리오 기준
        all_trades = r.get("trades", [])
    if all_trades:
        hold_days_list = [t.get("hold_days", 0) for t in all_trades if t.get("hold_days")]
        if hold_days_list:
            avg_hold = sum(hold_days_list) / len(hold_days_list)
            print(f"\n  평균 보유기간: {avg_hold:.1f}영업일 "
                  f"(최소:{min(hold_days_list)} / 최대:{max(hold_days_list)})")
            long_hold = sum(1 for d in hold_days_list if d >= 10)
            if long_hold > 0:
                print(f"  ⚠️ 10일 이상 장기보유: {long_hold}건 "
                      f"({long_hold/len(hold_days_list)*100:.0f}%) "
                      f"→ 손절선 또는 임계치 재검토 신호")


# ============================================================
# 메인
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="스윙봇 백테스트")
    parser.add_argument("--start",         default="2024-06-01",
                        help="시작일 (최소 6개월 권장)")
    parser.add_argument("--end",           default="",
                        help="종료일 (비우면 오늘)")
    parser.add_argument("--codes",         default="",
                        help="쉼표구분 종목코드 (비우면 DB 전체)")
    parser.add_argument("--max-codes",     type=int, default=50)
    parser.add_argument("--initial-cash",  type=int, default=10_000_000,
                        help="초기 자본 (sbot 기본 1000만원)")
    parser.add_argument("--base-buy-amt",  type=int, default=500_000,
                        help="기본 매수금액 (sbot 기본 50만원)")
    parser.add_argument("--max-positions", type=int, default=2)
    parser.add_argument("--buy-score-min", type=int, default=75,
                        help="매수 최소 점수 (sbot 점수분포상 75~90 권장)")
    parser.add_argument("--ai-mode",       default="rule_proxy",
                        choices=["fixed", "rule_proxy", "cache"])
    parser.add_argument("--compare",       action="store_true",
                        help="시나리오 비교 모드 (토요일 루틴용)")
    parser.add_argument("--db",
                        default=os.path.join(
                            os.path.dirname(__file__), "data", "backtest_data.db"))
    parser.add_argument("--results-dir",
                        default=os.path.join(
                            os.path.dirname(__file__), "results"))
    parser.add_argument("--verbose",       action="store_true")
    args = parser.parse_args()

    os.makedirs(args.results_dir, exist_ok=True)

    # 종료일 기본값 = 오늘
    end_date = args.end or datetime.date.today().strftime("%Y-%m-%d")

    # 종목 결정
    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    else:
        loader = DataLoader(args.db)
        codes  = loader.all_codes()[:args.max_codes]
    print(f"📋 [SBOT] 대상 종목 {len(codes)}개")
    if not codes:
        print("❌ 종목 없음 — fetch_history_fdr.py 먼저 실행하세요")
        sys.exit(1)

    # 기본 설정
    base_config = SBotBacktestConfig(
        initial_cash    = args.initial_cash,
        base_buy_amt    = args.base_buy_amt,
        max_positions   = args.max_positions,
        buy_score_min   = args.buy_score_min,
        start_date      = args.start,
        end_date        = end_date,
        codes           = codes,
        ai_score_mode   = args.ai_mode,
        verbose         = args.verbose,
    )

    # 실행
    if args.compare:
        scenarios = get_sbot_scenarios(base_config)
        results   = []
        for sc in scenarios:
            cfg = SBotBacktestConfig(**sc["config"])
            results.append(run_one(sc["name"], cfg, args.db))
        print_sbot_summary(results)
    else:
        result  = run_one("단일(SBOT)", base_config, args.db)
        print()
        print(format_report(result["metrics"], "단일(SBOT)"))
        results = [result]

    # 결과 저장
    ts       = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(args.results_dir, f"sbot_result_{ts}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(
            [{**r, "trades": r["trades"][:50]} for r in results],
            f, ensure_ascii=False, indent=2, default=str,
        )
    print(f"\n💾 결과 저장: {out_path}")


if __name__ == "__main__":
    main()
