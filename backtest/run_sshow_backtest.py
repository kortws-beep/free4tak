"""
run_sshow_backtest.py — 생쇼(전문가4인 추천) 결과 체크 & 적중률 백테스터
================================================================
목적:
  sshow_db.py에 쌓인 생쇼 추천을 ATR 재계산 + 7/14일 역일 체크인
  방식으로 점검하고, 적중률 통계 및 모든 pending 종목의 현재
  손익 스냅샷을 콘솔에 보기 좋게 출력한다.

  2026-06-30: 원래 lina_bot.py의 07:50/14:40 텔레스윙 스케줄러에 묻혀
  자동 알림으로 보내던 기능을, 결과를 차분히 들여다볼 수 있도록 이
  독립 스크립트로 분리. lina_bot.py에서는 해당 호출을 제거함.
  (14:30 mbn 생쇼 데이터 수집 자체는 lina_bot.py에 그대로 남아있음)

  2026-07-01: 체크인을 7/14/21 → 7/14로 단축(pending이 60종목까지
  쌓여 보기 어려웠음). 또한 "정확히 7/14일째 도달한 것만 보고"가
  아니라, 실행할 때마다 모든 pending 종목의 현재가/현재수익률을
  항상 보여주도록 변경 (get_pending_with_current_price 사용).

사용법:
  python3 run_sshow_backtest.py                # 체크인 + 통계 + 현재손익 출력
  python3 run_sshow_backtest.py --migrate       # 기존 데이터 일괄 ATR재계산도 함께 (1회성)
  python3 run_sshow_backtest.py --cutoff 2026-06-16  # 마이그레이션 시 삭제 기준일 변경
"""
import os
import sys
import argparse
import datetime
import json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LINA_DIR = os.path.join(os.path.dirname(BASE_DIR), "lina_bot")
RESULT_DIR = os.path.join(BASE_DIR, "results")

if LINA_DIR not in sys.path:
    sys.path.insert(0, LINA_DIR)

import sshow_db  # noqa: E402


def print_header(title: str):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(description="생쇼 추천 결과 체크 & 통계")
    parser.add_argument("--migrate", action="store_true",
                         help="기존 데이터를 ATR 재계산값으로 일괄 갱신 (1회성)")
    parser.add_argument("--cutoff", default="2026-06-16",
                         help="--migrate 시 이 날짜 이전 데이터는 삭제 (기본 2026-06-16)")
    parser.add_argument("--stats-days", type=int, default=30,
                         help="적중률 통계 집계 기간 (기본 30일)")
    args = parser.parse_args()

    if args.migrate:
        print_header("🔧 기존 데이터 ATR 재계산 마이그레이션")
        result = sshow_db.migrate_recalc_existing(cutoff_date=args.cutoff)
        print(f"\n삭제: {result['deleted']}건 | "
              f"재계산: {result['recalced']}건 | "
              f"재계산실패(원문유지): {result['kept_failed']}건")

    print_header("📊 7/14일 체크인 — 신규 판정/알림")
    notis = sshow_db.check_and_update_results()
    if notis:
        for n in notis:
            print(f"  [{n['stage']:>2}일][{n['kind']:>8}] {n['text']}")
    else:
        print("  이번 체크인 대상 없음 (모두 이미 확정됐거나 아직 판정 시점 전)")

    print_header("🗑️ 오래된 데이터 정리")
    deleted = sshow_db.cleanup_old_picks()
    if not deleted:
        print(f"  정리 대상 없음 ({sshow_db.KEEP_DAYS}일 기준)")

    print_header(f"📈 적중률 통계 (최근 {args.stats_days}일)")
    stats = sshow_db.get_sshow_stats(days=args.stats_days)
    print(f"  전체 판정 건수: {stats['total']}건 "
          f"(표본 {'충분' if stats['sample_size_ok'] else '부족 — 20건 미만, 가산점은 기본값 +8 유지'})")
    print(f"  적중(hit): {stats['hit']}건 | 손절(stop): {stats['stop']}건 | "
          f"보합(hold): {stats['hold']}건")
    print(f"  적중률(hit/(hit+stop)): {stats['hit_rate']:.1%}")

    print_header("📋 pending(미확정) 추천 — 현재 손익 스냅샷")
    pending_list = sshow_db.get_pending_with_current_price()
    if pending_list:
        for p in pending_list:
            tag = "✅" if p["price_valid"] else "❌무효"
            cur = f"{p['current_price']:,.0f}원" if p["current_price"] else "데이터없음"
            pct = f"{p['current_pct']:+.1f}%" if p["current_pct"] is not None else "  -  "
            print(f"  {p['date']} {p['name']:>10} | 매수:{p['buy_price']:>10,.0f} "
                  f"손절:{p['stop_price']:>10,.0f} 목표:{p['tgt_price']:>10,.0f} | "
                  f"현재:{cur:>12}({pct:>7}) | {p['checkin_label']:<14} | "
                  f"출처:{p['price_source']} {tag}")
    else:
        print("  없음")

    # 결과 JSON 저장 (다른 run_*_backtest.py와 동일한 패턴)
    os.makedirs(RESULT_DIR, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = os.path.join(RESULT_DIR, f"sshow_backtest_{ts}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({
            "generated_at": datetime.datetime.now().isoformat(),
            "checkin_notifications": notis,
            "deleted_old": deleted,
            "stats": stats,
            "pending": pending_list,
        }, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n💾 결과 저장: {out}")


if __name__ == "__main__":
    main()
