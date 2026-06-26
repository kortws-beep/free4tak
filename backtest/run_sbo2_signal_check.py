"""
verify_sbo2_signals.py — sbo2 VCP/추세/텔레 선정 신호 사후검증
================================================================
목적:
  sbo2_candidates에 쌓인 추천 종목들(매수 여부 무관)을 가져와,
  실제 sbo2.py와 동일한 ATR 손절/목표1 로직으로 "만약 그 시점에
  진입했다면" 25일 안에 손절/목표1도달/미결 중 무엇이었는지
  시뮬레이션하고, 슬롯별·점수구간별로 집계한다.

★ 제약: kr_stock_daily_data에 고가/저가가 없어 ATR은 종가 기반
  근사치(전일 대비 절대변화 평균)를 사용한다. 실거래보다 변동성이
  작게 잡힐 수 있어 손절/목표 폭이 더 좁게 나올 수 있다는 한계가
  있음을 결과 해석 시 감안해야 한다.

사용법:
  python3 verify_sbo2_signals.py
  python3 verify_sbo2_signals.py --grade swing
  python3 verify_sbo2_signals.py --hold-days 25
"""
import os
import sqlite3
import json
import argparse
import datetime
from collections import defaultdict

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
# ★ 이 스크립트는 stock_bot/backtest/ 에 위치하지만,
#   실제 sbo2 데이터는 stock_bot/lina_bot/ 안에 있다.
LINA_DIR   = os.path.join(os.path.dirname(BASE_DIR), "lina_bot")
CAND_DB    = os.path.join(LINA_DIR, "sbo2_trades.db")
PRICE_DB   = os.path.join(LINA_DIR, "kr_theme_finance.db")
RESULT_DIR = os.path.join(BASE_DIR, "results")

# sbo2.py와 동일한 상수
ATR_STOP_MULT   = 2.0
ATR_TARGET_MULT = 3.0
TARGET_CAP_PCT  = 0.20      # 목표가 상한 +20%
MIN_ATR_RATE    = 0.01      # 최소 ATR 비율 강제
ATR_LOOKBACK    = 14        # ATR 기간


# ============================================================
# 종목별 가격 히스토리 로드 (캐시)
# ============================================================
_price_cache = {}

def load_price_history(conn, stock_name: str) -> list:
    """[(date, close), ...] 날짜 오름차순"""
    if stock_name in _price_cache:
        return _price_cache[stock_name]
    rows = conn.execute("""
        SELECT date, close_price FROM kr_stock_daily_data
        WHERE stock_name = ? AND close_price > 0
        ORDER BY date ASC
    """, (stock_name,)).fetchall()
    result = [(r[0], r[1]) for r in rows]
    _price_cache[stock_name] = result
    return result


def _atr_approx(closes_desc: list, n: int = ATR_LOOKBACK) -> float:
    """종가 기반 근사 ATR (lina_backtest.py와 동일 방식) — closes_desc는 최신→과거"""
    if len(closes_desc) < n + 1:
        return 0.0
    return sum(abs(closes_desc[i] - closes_desc[i+1]) for i in range(n)) / n


def simulate_one(history: list, scan_date: str, hold_days: int) -> dict:
    """
    특정 종목의 scan_date 시점에 진입했다고 가정하고 ATR 손절/목표1
    시뮬레이션. history: [(date, close), ...] 오름차순 전체.
    반환: {"result": "손절"|"목표1도달"|"미결", "days": N, "ret_pct": float} 또는 None(데이터부족)
    """
    dates = [h[0] for h in history]
    if scan_date not in dates:
        # scan_date 당일 종가가 없으면(휴장 등) 가장 가까운 이전 거래일로 보정
        earlier = [d for d in dates if d <= scan_date]
        if not earlier:
            return None
        scan_date = earlier[-1]

    idx = dates.index(scan_date)
    if idx < ATR_LOOKBACK:
        return None  # ATR 계산에 필요한 과거 데이터 부족

    entry_price = history[idx][1]
    if entry_price <= 0:
        return None

    # ATR 계산용 — scan_date까지의 종가를 최신→과거로
    closes_desc = [history[i][1] for i in range(idx, max(-1, idx - ATR_LOOKBACK - 1), -1)]
    atr = _atr_approx(closes_desc)
    atr_rate = atr / entry_price if entry_price > 0 else 0
    if 0 < atr_rate < MIN_ATR_RATE:
        atr_rate = MIN_ATR_RATE
    if atr_rate <= 0:
        return None

    atr_val   = entry_price * atr_rate
    stop      = entry_price - atr_val * ATR_STOP_MULT
    tgt_raw   = entry_price + atr_val * ATR_TARGET_MULT
    tgt_cap   = entry_price * (1 + TARGET_CAP_PCT)
    tgt       = min(tgt_raw, tgt_cap)

    # 진입 다음날부터 hold_days 거래일 추적
    future = history[idx+1: idx+1+hold_days]
    if not future:
        return None

    for day_i, (d, close) in enumerate(future, start=1):
        if close <= stop:
            ret_pct = (close - entry_price) / entry_price * 100
            return {"result": "손절", "days": day_i, "ret_pct": round(ret_pct, 2)}
        if close >= tgt:
            ret_pct = (close - entry_price) / entry_price * 100
            return {"result": "목표1도달", "days": day_i, "ret_pct": round(ret_pct, 2)}

    # 기한 내 미결 — 마지막 종가 기준 평가
    last_close = future[-1][1]
    ret_pct = (last_close - entry_price) / entry_price * 100
    return {"result": "미결", "days": len(future), "ret_pct": round(ret_pct, 2)}


# ============================================================
# 메인
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="sbo2 신호 사후검증")
    parser.add_argument("--grade", default="", help="swing/trend/tele 중 하나만 (비우면 전체)")
    parser.add_argument("--hold-days", type=int, default=25, help="추적 기간 (sbo2 기한초과 기준 기본 25일)")
    parser.add_argument("--min-score", type=int, default=0, help="이 점수 이상만 분석")
    args = parser.parse_args()

    if not os.path.exists(CAND_DB):
        print(f"❌ {CAND_DB} 없음"); return
    if not os.path.exists(PRICE_DB):
        print(f"❌ {PRICE_DB} 없음"); return

    cand_conn  = sqlite3.connect(CAND_DB)
    price_conn = sqlite3.connect(PRICE_DB)

    grade_filter = "AND grade = ?" if args.grade else ""
    params = [args.grade] if args.grade else []

    rows = cand_conn.execute(f"""
        SELECT DISTINCT scan_date, stock_name, grade, score, curr_price
        FROM sbo2_candidates
        WHERE grade IN ('swing','trend','tele') {grade_filter}
        AND score >= ?
        ORDER BY scan_date, stock_name
    """, params + [args.min_score]).fetchall()

    print(f"📋 중복 제거 후 분석 대상: {len(rows)}건 "
          f"(슬롯: {args.grade or 'swing/trend/tele 전체'}, "
          f"점수 {args.min_score}점 이상, 추적 {args.hold_days}일)")

    results = []
    skipped = 0
    for scan_date, stock_name, grade, score, curr_price in rows:
        history = load_price_history(price_conn, stock_name)
        if not history:
            skipped += 1
            continue
        sim = simulate_one(history, scan_date, args.hold_days)
        if sim is None:
            skipped += 1
            continue
        sim.update({"grade": grade, "score": score, "stock_name": stock_name,
                     "scan_date": scan_date})
        results.append(sim)

    print(f"✅ 시뮬레이션 완료: {len(results)}건 (데이터 부족으로 제외: {skipped}건)")
    if not results:
        print("⚠️ 분석 가능한 데이터가 없습니다.")
        return

    # ── 슬롯별 집계 ──────────────────────────────────────────
    print("\n" + "=" * 70)
    print("📊 슬롯별 결과")
    print("=" * 70)
    by_grade = defaultdict(list)
    for r in results:
        by_grade[r["grade"]].append(r)

    for grade, items in by_grade.items():
        total = len(items)
        wins  = sum(1 for i in items if i["result"] == "목표1도달")
        losses = sum(1 for i in items if i["result"] == "손절")
        undecided = sum(1 for i in items if i["result"] == "미결")
        avg_ret = sum(i["ret_pct"] for i in items) / total
        avg_days = sum(i["days"] for i in items) / total
        print(f"\n  [{grade}] 총 {total}건")
        print(f"    목표1도달: {wins}건 ({wins/total*100:.1f}%) | "
              f"손절: {losses}건 ({losses/total*100:.1f}%) | "
              f"미결: {undecided}건 ({undecided/total*100:.1f}%)")
        print(f"    평균 수익률: {avg_ret:+.2f}% | 평균 보유일: {avg_days:.1f}일")

    # ── 점수구간별 집계 (전체 슬롯 통합) ───────────────────────
    print("\n" + "=" * 70)
    print("📊 점수구간별 결과 (전체 슬롯 통합)")
    print("=" * 70)
    bins = [(0, 60), (60, 70), (70, 80), (80, 90), (90, 101)]
    for lo, hi in bins:
        items = [r for r in results if lo <= r["score"] < hi]
        if not items:
            continue
        total = len(items)
        wins  = sum(1 for i in items if i["result"] == "목표1도달")
        losses = sum(1 for i in items if i["result"] == "손절")
        avg_ret = sum(i["ret_pct"] for i in items) / total
        print(f"\n  [{lo}~{hi-1}점] 총 {total}건")
        print(f"    목표1도달: {wins}건 ({wins/total*100:.1f}%) | "
              f"손절: {losses}건 ({losses/total*100:.1f}%)")
        print(f"    평균 수익률: {avg_ret:+.2f}%")

    cand_conn.close()
    price_conn.close()

    # ── 결과 JSON 저장 (다른 run_*_backtest.py와 동일한 패턴) ──
    os.makedirs(RESULT_DIR, exist_ok=True)
    summary_by_grade = {}
    for grade, items in by_grade.items():
        total = len(items)
        summary_by_grade[grade] = {
            "total": total,
            "목표1도달": sum(1 for i in items if i["result"] == "목표1도달"),
            "손절":     sum(1 for i in items if i["result"] == "손절"),
            "미결":     sum(1 for i in items if i["result"] == "미결"),
            "avg_return_pct": round(sum(i["ret_pct"] for i in items) / total, 2),
            "avg_hold_days":  round(sum(i["days"] for i in items) / total, 1),
        }

    ts  = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = os.path.join(RESULT_DIR, f"sbo2_signal_check_{ts}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({
            "generated_at": datetime.datetime.now().isoformat(),
            "params": {"grade": args.grade or "전체", "hold_days": args.hold_days,
                       "min_score": args.min_score},
            "total_analyzed": len(results), "skipped": skipped,
            "by_grade": summary_by_grade,
            "trades": results[:500],  # 너무 커지지 않게 상한
        }, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n💾 결과 저장: {out}")


if __name__ == "__main__":
    main()
