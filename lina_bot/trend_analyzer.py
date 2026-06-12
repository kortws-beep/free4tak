"""
trend_analyzer.py
─────────────────────────────────────────────────────────────
대장 전용 상승추세 눌림목 엔진

필터 파이프라인:
 ① 60일선 위 + 60일선 자체 우상향    (추세 살아있음)
 ② 고점이 전 고점보다 높음 (HH)      (상승 파동 확인)
 ③ 저점이 전 저점보다 높음 (HL)      (눌림목 확인)
 ④ 현재가가 최근 저점 근처 (±8%)     (눌림목 진입 타점)
 ⑤ RSI 40~60 구간                   (건전한 눌림, 과매도 아님)
 ⑥ 거래량 눌림 구간에서 감소         (매도 압력 약화)
 ⑦ 스마트머니 수급                   (외인/기관 잠입 확인)

추가 정보:
 - ATR 14일 기반 손절가 / 목표가 / R:R
 - 섹터 군집 (같은 테마 내 60일선 위 종목 수)
"""

import sqlite3
import os
import re

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE_DIR, "kr_theme_finance.db")

# ── 튜닝 파라미터 ────────────────────────────────────────────
MA60_SLOPE_DAYS  = 10     # 60일선 우상향 확인 기간
PULLBACK_BAND    = 0.08   # 현재가가 최근 저점 ±8% 이내
RSI_LOW          = 40     # RSI 하한
RSI_HIGH         = 60     # RSI 상한
VOL_PULL_RATIO   = 0.70   # 눌림 구간 거래량 < 전체 평균 × 70%
SMART_DAYS       = 10
SMART_MIN_DAYS   = 3
ATR_PERIOD       = 14
ATR_STOP_MULT    = 1.5
ATR_TARGET_MULT  = 3.0
CLUSTER_MIN      = 3
TOP_N_DEFAULT    = 5

# 파동 구간 설정
WAVE_RECENT      = 20     # 최근 파동 구간 (일)
WAVE_PREV        = 20     # 이전 파동 구간 (일)


# ══════════════════════════════════════════════════════════════
# 헬퍼
# ══════════════════════════════════════════════════════════════

def _ma(prices: list, n: int, min_ratio: float = 0.95) -> float:
    if len(prices) >= n:
        return sum(prices[:n]) / n
    if len(prices) >= int(n * min_ratio):
        return sum(prices) / len(prices)
    return 0.0


def _rsi(closes: list, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains  = [max(closes[i] - closes[i+1], 0) for i in range(period)]
    losses = [max(closes[i+1] - closes[i], 0) for i in range(period)]
    avg_g  = sum(gains)  / period
    avg_l  = sum(losses) / period
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return round(100 - (100 / (1 + rs)), 1)


def _atr(closes: list, n: int = ATR_PERIOD) -> float:
    if len(closes) < n + 1:
        return 0.0
    trs = [abs(closes[i] - closes[i+1]) for i in range(n)]
    return sum(trs) / n


def _is_upslope(prices: list, n: int) -> bool:
    """최근 n일간 이동평균이 우상향인지 확인"""
    if len(prices) < n + 5:
        return False
    ma_now  = _ma(prices[:n], n)
    ma_prev = _ma(prices[5:n+5], n)
    return ma_now > ma_prev


# ══════════════════════════════════════════════════════════════
# 메인
# ══════════════════════════════════════════════════════════════

def get_trend_picks(top_n: int = TOP_N_DEFAULT) -> str:

    if not os.path.exists(DB_PATH):
        return "⚠️ [추세 엔진] kr_theme_finance.db 파일을 찾을 수 없어."

    conn   = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("SELECT DISTINCT stock_name FROM kr_stock_daily_data")
    all_stocks = [r[0] for r in cursor.fetchall()]
    if not all_stocks:
        conn.close()
        return "⚠️ [추세 엔진] kr_stock_daily_data 테이블이 비어있어."

    # ── 테마 매핑 ─────────────────────────────────────────────
    cursor.execute("SELECT stock_name, theme_name FROM kr_theme_stocks")
    theme_map: dict[str, list] = {}
    for sname, tname in cursor.fetchall():
        pure = re.sub(r'\s*(KOSPI|KOSDAQ)\s*\d{6}$', '', sname).strip()
        theme_map.setdefault(pure, [])
        if tname not in theme_map[pure]:
            theme_map[pure].append(tname)

    # ── 60일선 위 종목 사전 (군집용) ─────────────────────────
    ma60_pass: dict[str, float] = {}

    filtered = {"total": 0, "f1_ma60": 0, "f2_hh": 0,
                "f3_hl": 0, "f4_pullback": 0, "f5_rsi": 0,
                "f6_vol": 0, "f7_smart": 0}
    passed   = []

    for stock_name in all_stocks:
        filtered["total"] += 1

        cursor.execute("""
            SELECT date, close_price, volume,
                   foreign_net_buy, institution_net_buy
            FROM kr_stock_daily_data
            WHERE stock_name = ?
            ORDER BY date DESC
            LIMIT 220
        """, (stock_name,))
        rows = cursor.fetchall()

        if len(rows) < 60:
            continue

        closes  = [r[1] if r[1] else 0 for r in rows]
        volumes = [r[2] if r[2] else 0 for r in rows]

        valid_closes = [c for c in closes if c > 0]
        if len(valid_closes) < 60:
            continue

        curr_price = valid_closes[0]
        pure_name  = re.sub(r'\s*(KOSPI|KOSDAQ)\s*\d{6}$', '', stock_name).strip()

        # ════════════════════════════════════════════════
        # ETF / 인버스 / 레버리지 제외
        etf_keywords = ['KODEX','TIGER','KBSTAR','ARIRANG','HANARO',
                        'KOSEF','TREX','SOL','ACE','PLUS','RISE',
                        '인버스','레버리지','ETN']
        if any(kw in pure_name for kw in etf_keywords):
            continue

        # 우선주 제외
        if pure_name.endswith('우') or pure_name.endswith('우B'):
            continue

        # ① 60일선 위 + 우상향
        # ════════════════════════════════════════════════
        ma60 = _ma(valid_closes, 60)
        if ma60 == 0 or curr_price < ma60:
            filtered["f1_ma60"] += 1
            continue
        if not _is_upslope(valid_closes, 60):
            filtered["f1_ma60"] += 1
            continue

        # 60일선 통과 종목 등록 (군집용)
        ma60_pass[pure_name] = curr_price

        # ════════════════════════════════════════════════
        # ② HH — 최근 고점 > 이전 고점
        # ════════════════════════════════════════════════
        if len(valid_closes) < WAVE_RECENT + WAVE_PREV:
            continue

        recent_hi = max(valid_closes[0:WAVE_RECENT])
        prev_hi   = max(valid_closes[WAVE_RECENT:WAVE_RECENT + WAVE_PREV])

        if recent_hi <= prev_hi:
            filtered["f2_hh"] += 1
            continue

        # ════════════════════════════════════════════════
        # ③ HL — 최근 저점 > 이전 저점
        # ════════════════════════════════════════════════
        recent_lo = min(valid_closes[0:WAVE_RECENT])
        prev_lo   = min(valid_closes[WAVE_RECENT:WAVE_RECENT + WAVE_PREV])

        if recent_lo <= prev_lo:
            filtered["f3_hl"] += 1
            continue

        # ════════════════════════════════════════════════
        # ④ 현재가가 최근 저점 근처 (눌림목 타점)
        #    현재가 < 최근 고점의 50% 되돌림 지점
        #    AND 현재가가 최근 저점 ±8% 이내
        # ════════════════════════════════════════════════
        dist_from_lo = abs(curr_price - recent_lo) / recent_lo if recent_lo > 0 else 1
        if dist_from_lo > PULLBACK_BAND:
            filtered["f4_pullback"] += 1
            continue

        # ════════════════════════════════════════════════
        # ⑤ RSI 40~60 (건전한 눌림)
        # ════════════════════════════════════════════════
        rsi = _rsi(valid_closes, 14)
        if not (RSI_LOW <= rsi <= RSI_HIGH):
            filtered["f5_rsi"] += 1
            continue

        # ════════════════════════════════════════════════
        # ⑥ 거래량 감소 (눌림 구간 에너지 응축)
        # ════════════════════════════════════════════════
        valid_volumes = [v for v in volumes if v > 0]
        if len(valid_volumes) < 10:
            continue

        vol_avg_all    = sum(valid_volumes) / len(valid_volumes)
        vol_avg_recent = sum(valid_volumes[:5]) / 5

        if vol_avg_all == 0 or vol_avg_recent >= vol_avg_all * VOL_PULL_RATIO:
            filtered["f6_vol"] += 1
            continue

        # ════════════════════════════════════════════════
        # ⑦ 스마트머니
        # ════════════════════════════════════════════════
        f_net_raw  = [r[3] for r in rows]
        i_net_raw  = [r[4] for r in rows]
        supply_len = max(
            sum(1 for v in f_net_raw if v is not None),
            sum(1 for v in i_net_raw if v is not None)
        )
        f_net = [v if v is not None else 0 for v in f_net_raw]
        i_net = [v if v is not None else 0 for v in i_net_raw]

        if supply_len == 0:
            f_pos_days = i_pos_days = f_cum = i_cum = 0
            smart_ok = True
        else:
            sw         = min(SMART_DAYS, supply_len)
            f_pos_days = sum(1 for v in f_net[:sw] if v > 0)
            i_pos_days = sum(1 for v in i_net[:sw] if v > 0)
            f_cum      = sum(f_net[:sw])
            i_cum      = sum(i_net[:sw])
            adj_min    = max(2, int(SMART_MIN_DAYS * supply_len / SMART_DAYS))
            smart_ok   = (
                f_pos_days >= adj_min or
                i_pos_days >= adj_min or
                (f_cum > 0 and i_cum > 0) or
                (f_cum > 0 or i_cum > 0)
            )

        if not smart_ok:
            filtered["f7_smart"] += 1
            continue

        # ════════════════════════════════════════════════
        # ✅ 통과 → ATR + 스코어
        # ════════════════════════════════════════════════
        atr        = _atr(valid_closes, ATR_PERIOD)
        stop_price = round(curr_price - atr * ATR_STOP_MULT, 0)
        tgt_price  = round(curr_price + atr * ATR_TARGET_MULT, 0)
        stop_pct   = round((curr_price - stop_price) / curr_price * 100, 1)
        tgt_pct    = round((tgt_price  - curr_price) / curr_price * 100, 1)
        rr_ratio   = round(tgt_pct / stop_pct, 1) if stop_pct > 0 else 0

        score = 0

        # 60일선 이격 좁을수록 +점수 (최대 20점)
        gap60 = (curr_price - ma60) / ma60
        score += max(0, 20 - int(gap60 * 100))

        # HH 상승폭 클수록 +점수 (최대 20점)
        hh_strength = (recent_hi - prev_hi) / prev_hi if prev_hi > 0 else 0
        score += min(20, int(hh_strength * 200))

        # HL 저점 상승폭 클수록 +점수 (최대 20점)
        hl_strength = (recent_lo - prev_lo) / prev_lo if prev_lo > 0 else 0
        score += min(20, int(hl_strength * 200))

        # 눌림목 깊이 (저점에 가까울수록 +점수, 최대 20점)
        score += max(0, 20 - int(dist_from_lo * 200))

        # RSI 50 근처일수록 +점수 (최대 10점)
        score += max(0, 10 - int(abs(rsi - 50)))

        # 거래량 마름 (최대 10점)
        dry_ratio = vol_avg_recent / vol_avg_all if vol_avg_all > 0 else 1
        score += int((1 - dry_ratio) * 10)

        # 스마트머니 강도 (최대 15점)
        score += min(15, (f_pos_days + i_pos_days) * 2)

        themes = theme_map.get(pure_name, ["테마 미분류"])

        # R:R 불량 제외
        if rr_ratio < 1.5:
            continue

        passed.append({
            "pure_name":   pure_name,
            "score":       score,
            "curr_price":  curr_price,
            "ma60":        round(ma60, 0),
            "gap60":       round(gap60 * 100, 1),
            "recent_hi":   recent_hi,
            "recent_lo":   recent_lo,
            "prev_hi":     prev_hi,
            "prev_lo":     prev_lo,
            "hh_pct":      round(hh_strength * 100, 1),
            "hl_pct":      round(hl_strength * 100, 1),
            "dist_lo_pct": round(dist_from_lo * 100, 1),
            "rsi":         rsi,
            "vol_dry_pct": round(dry_ratio * 100, 1),
            "f_pos_days":  f_pos_days,
            "i_pos_days":  i_pos_days,
            "f_cum":       f_cum,
            "i_cum":       i_cum,
            "themes":      themes[:2],
            "atr":         round(atr, 0),
            "stop_price":  stop_price,
            "tgt_price":   tgt_price,
            "stop_pct":    stop_pct,
            "tgt_pct":     tgt_pct,
            "rr_ratio":    rr_ratio,
        })

    conn.close()

    # ── 섹터 군집 계산 ────────────────────────────────────────
    for item in passed:
        cluster_stocks = []
        for theme in item["themes"]:
            for sname, themes_list in theme_map.items():
                if theme in themes_list and sname in ma60_pass and sname != item["pure_name"]:
                    cluster_stocks.append(sname)
        cluster_stocks     = list(set(cluster_stocks))
        item["cluster_cnt"]    = len(cluster_stocks)
        item["cluster_stocks"] = cluster_stocks[:3]
        if item["cluster_cnt"] >= CLUSTER_MIN:
            item["score"] += min(15, item["cluster_cnt"] * 3)

    # ── 정렬 & 선출 ──────────────────────────────────────────
    passed.sort(key=lambda x: x["score"], reverse=True)
    top = passed[:top_n]

    if not top:
        return (
            f"💡 [추세 엔진] 총 {filtered['total']}종목 분석 완료.\n"
            f"   ① 60일선 미달/하락: {filtered['f1_ma60']}개\n"
            f"   ② HH 미형성      : {filtered['f2_hh']}개\n"
            f"   ③ HL 미형성      : {filtered['f3_hl']}개\n"
            f"   ④ 눌림목 이탈    : {filtered['f4_pullback']}개\n"
            f"   ⑤ RSI 범위 이탈  : {filtered['f5_rsi']}개\n"
            f"   ⑥ 거래량 과다    : {filtered['f6_vol']}개\n"
            f"   ⑦ 스마트머니 미감지: {filtered['f7_smart']}개\n"
            f"   → 조건 통과 종목 없어. 파라미터 조정을 고려해봐."
        )

    # ── 리포트 ───────────────────────────────────────────────
    report  = "📈 **[상승추세 눌림목 엔진 — HH/HL + RSI 건전한 눌림]** 📈\n"
    report += f"   분석 {filtered['total']}종목 → 60일선 통과 {len(ma60_pass)}종목 → 최종 {len(passed)}종목 → 상위 {len(top)}개\n"
    report += "=" * 60 + "\n"

    for idx, item in enumerate(top, 1):
        # 스마트머니 태그
        if item["f_cum"] > 0 and item["i_cum"] > 0:
            smart_tag = "🔥 외인+기관 쌍끌이"
        elif item["f_pos_days"] >= SMART_MIN_DAYS:
            smart_tag = "🟦 외국인 연속 매수"
        elif item["i_pos_days"] >= SMART_MIN_DAYS:
            smart_tag = "🟥 기관 연속 매수"
        else:
            smart_tag = "🔸 수급 미약"

        # 군집 태그
        if item["cluster_cnt"] >= CLUSTER_MIN:
            cluster_tag  = f"🔥 강한 군집 ({item['cluster_cnt']}종목 동반)"
            cluster_line = f"    🏆 테마 동반주  : {' / '.join(item['cluster_stocks'])} 등\n"
        elif item["cluster_cnt"] > 0:
            cluster_tag  = f"🔸 약한 군집 ({item['cluster_cnt']}종목)"
            cluster_line = f"    🏆 테마 동반주  : {' / '.join(item['cluster_stocks'])}\n"
        else:
            cluster_tag  = "❌ 군집 없음 (단독)"
            cluster_line = ""

        # R:R 태그
        if item["rr_ratio"] >= 2.5:
            rr_tag = "✅ 우량"
        elif item["rr_ratio"] >= 1.5:
            rr_tag = "🔸 보통"
        else:
            rr_tag = "❌ 불량"

        report += (
            f"\n 📌 **{idx}위: {item['pure_name']}**  (스코어: {item['score']}점)\n"
            f"    📂 테마        : {' / '.join(item['themes'])}\n"
            f"    👥 섹터 군집   : {cluster_tag}\n"
            f"{cluster_line}"
            f"    💰 현재가      : {item['curr_price']:,}원\n"
            f"    🎯 목표가      : {item['tgt_price']:,}원  (+{item['tgt_pct']}%)\n"
            f"    🛑 손절가      : {item['stop_price']:,}원  (-{item['stop_pct']}%)\n"
            f"    ⚖️  R:R         : 1 : {item['rr_ratio']}  {rr_tag}\n"
            f"    📊 60일선      : {item['ma60']:,.0f}원  (상단 +{item['gap60']}%)\n"
            f"    🌊 파동 분석   : 고점 +{item['hh_pct']}% 상승 / 저점 +{item['hl_pct']}% 상승\n"
            f"    🎯 눌림 위치   : 최근 저점에서 +{item['dist_lo_pct']}% (타점 근접)\n"
            f"    📉 RSI         : {item['rsi']} (건전한 눌림 구간)\n"
            f"    💤 거래량 마름 : 평소 대비 {item['vol_dry_pct']}% 수준\n"
            f"    🕵️  스마트머니  : {smart_tag} "
            f"(외인 {item['f_pos_days']}일 / 기관 {item['i_pos_days']}일)\n"
            f"------------------------------------------------------------"
        )

    report += f"\n\n   ⚙️ ATR{ATR_PERIOD}일 / 손절×{ATR_STOP_MULT} / 목표×{ATR_TARGET_MULT} / RSI범위 {RSI_LOW}~{RSI_HIGH} / 군집기준 {CLUSTER_MIN}종목\n"
    return report


if __name__ == "__main__":
    print(get_trend_picks(top_n=5))