"""
swing_analyzer.py
─────────────────────────────────────────────────────────────
대장 전용 스윙 추천 엔진 v2 (ATR 손절/목표 + 섹터 군집 버전)

필터 파이프라인:
 ① 200일 이동평균선 위  (장기 추세 생존)
 ② 20일선 근처 밀집    (±7% 이내 횡보)
 ③ VCP — 진폭 수렴     (최근 15일 진폭 < 이전 15일 진폭의 60%)
 ④ 거래량 마름         (최근 5일 평균 < 전체 평균의 50%)
 ⑤ 스마트머니 잠입     (외인/기관 최근 10일 중 3일 이상 순매수)

추가 정보:
 - ATR 14일 기반 손절가 / 목표가 / 리스크:리워드
 - 섹터 군집 (같은 테마 내 200일선 위 종목 수 + 테마 대장주)
"""

import sqlite3
import os
import re

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE_DIR, "kr_theme_finance.db")

# ── 튜닝 파라미터 ────────────────────────────────────────────
MA20_BAND        = 0.07   # 20일선 ±7% 이내 밀집
VCP_RATIO        = 0.60   # 최근 진폭 < 이전 진폭 × 60%
VOL_DRY_RATIO    = 0.50   # 최근 5일 평균 거래량 < 전체 평균 × 50%
SMART_DAYS       = 10     # 스마트머니 확인 기간 (일)
SMART_MIN_DAYS   = 2      # 최소 순매수 일수
ATR_PERIOD       = 14     # ATR 계산 기간
ATR_STOP_MULT    = 1.5    # 손절 = 현재가 - ATR × 1.5
ATR_TARGET_MULT  = 3.0    # 목표 = 현재가 + ATR × 3.0
CLUSTER_MIN      = 3      # 섹터 군집 최소 종목 수
TOP_N_DEFAULT    = 5


# ══════════════════════════════════════════════════════════════
# 헬퍼
# ══════════════════════════════════════════════════════════════

def _ma(prices: list, n: int, min_ratio: float = 0.95) -> float:
    """
    단순 이동평균.
    데이터가 n개 미만이어도 min_ratio(95%) 이상이면 있는 데이터로 계산.
    예: n=200인데 198개면 계산, 100개면 0 반환
    """
    if len(prices) >= n:
        return sum(prices[:n]) / n
    if len(prices) >= int(n * min_ratio):
        return sum(prices) / len(prices)
    return 0.0


def _atr(closes: list, n: int = ATR_PERIOD) -> float:
    """
    종가만 있으므로 True Range = abs(close[i] - close[i+1]) 로 근사
    실제 고/저 없이도 변동성 추정 가능
    """
    if len(closes) < n + 1:
        return 0.0
    trs = [abs(closes[i] - closes[i + 1]) for i in range(n)]
    return sum(trs) / n


# ══════════════════════════════════════════════════════════════
# 메인
# ══════════════════════════════════════════════════════════════

def get_swing_picks(top_n: int = TOP_N_DEFAULT) -> str:

    if not os.path.exists(DB_PATH):
        return "⚠️ [스윙 엔진] kr_theme_finance.db 파일을 찾을 수 없어."

    conn   = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # ── 전 종목 ───────────────────────────────────────────────
    cursor.execute("SELECT DISTINCT stock_name FROM kr_stock_daily_data")
    all_stocks = [r[0] for r in cursor.fetchall()]
    if not all_stocks:
        conn.close()
        return "⚠️ [스윙 엔진] kr_stock_daily_data 테이블이 비어있어."

    # ── 테마 매핑 ─────────────────────────────────────────────
    cursor.execute("SELECT stock_name, theme_name FROM kr_theme_stocks")
    theme_map: dict[str, list] = {}
    for sname, tname in cursor.fetchall():
        pure = re.sub(r'\s*(KOSPI|KOSDAQ)\s*\d{6}$', '', sname).strip()
        theme_map.setdefault(pure, [])
        if tname not in theme_map[pure]:
            theme_map[pure].append(tname)

    # ── 200일선 위 종목 전체 사전 구축 (섹터 군집용) ──────────
    # { 종목명: 현재가 } — 200일선 통과 종목만
    ma200_pass: dict[str, float] = {}

    filtered = {"total": 0, "f1_ma200": 0, "f2_ma20": 0,
                "f3_vcp": 0, "f4_vol": 0, "f5_smart": 0}
    passed   = []

    # ── 전 종목 순회 ──────────────────────────────────────────
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

        if len(rows) < 30:
            continue

        closes  = [r[1] if r[1] else 0 for r in rows]
        volumes = [r[2] if r[2] else 0 for r in rows]

        valid_closes = [c for c in closes if c > 0]
        if len(valid_closes) < 30:
            continue

        curr_price = valid_closes[0]
        pure_name  = re.sub(r'\s*(KOSPI|KOSDAQ)\s*\d{6}$', '', stock_name).strip()

        # ETF / 인버스 / 레버리지 제외
        etf_keywords = ['KODEX','TIGER','KBSTAR','ARIRANG','HANARO',
                        'KOSEF','TREX','SOL','ACE','PLUS','RISE',
                        '인버스','레버리지','ETN']
        if any(kw in pure_name for kw in etf_keywords):
            continue

        # 우선주 제외
        if pure_name.endswith('우') or pure_name.endswith('우B'):
            continue

        # ① 200일선 위
        ma200 = _ma(valid_closes, 200)
        if ma200 == 0 or curr_price < ma200:
            filtered["f1_ma200"] += 1
            continue

        # 200일선 통과 종목 사전에 등록 (군집 계산용)
        ma200_pass[pure_name] = curr_price

        # ② 20일선 밀집
        ma20 = _ma(valid_closes, 20)
        if ma20 == 0:
            continue
        dist_ma20 = abs(curr_price - ma20) / ma20
        if dist_ma20 > MA20_BAND:
            filtered["f2_ma20"] += 1
            continue

        # ③ VCP 수렴
        if len(valid_closes) < 30:
            continue
        recent_amp = (max(valid_closes[0:15]) - min(valid_closes[0:15])) / min(valid_closes[0:15]) if min(valid_closes[0:15]) > 0 else 0
        prev_amp   = (max(valid_closes[15:30]) - min(valid_closes[15:30])) / min(valid_closes[15:30]) if min(valid_closes[15:30]) > 0 else 0
        if prev_amp == 0 or recent_amp >= prev_amp * VCP_RATIO:
            filtered["f3_vcp"] += 1
            continue

        # ④ 거래량 마름
        valid_volumes = [v for v in volumes if v > 0]
        if len(valid_volumes) < 10:
            continue
        vol_avg_all    = sum(valid_volumes) / len(valid_volumes)
        vol_avg_recent = sum(valid_volumes[:5]) / 5
        if vol_avg_all == 0 or vol_avg_recent >= vol_avg_all * VOL_DRY_RATIO:
            filtered["f4_vol"] += 1
            continue

        # ⑤ 스마트머니
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
            sw        = min(SMART_DAYS, supply_len)
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
            filtered["f5_smart"] += 1
            continue

        # ════════════════════════════════════════════════
        # ✅ 통과 → ATR 계산
        # ════════════════════════════════════════════════
        atr        = _atr(valid_closes, ATR_PERIOD)
        stop_price = round(curr_price - atr * ATR_STOP_MULT, 0)
        tgt_price  = round(curr_price + atr * ATR_TARGET_MULT, 0)
        stop_pct   = round((curr_price - stop_price) / curr_price * 100, 1)
        tgt_pct    = round((tgt_price - curr_price)  / curr_price * 100, 1)
        rr_ratio   = round(tgt_pct / stop_pct, 1) if stop_pct > 0 else 0

        # 스코어
        gap200 = (curr_price - ma200) / ma200
        score  = 0
        score += max(0, 20 - int(gap200 * 100))
        score += max(0, 20 - int(dist_ma20 * 200))
        score += int((1 - recent_amp / prev_amp) * 25) if prev_amp > 0 else 0
        dry_ratio = vol_avg_recent / vol_avg_all if vol_avg_all > 0 else 1
        score += int((1 - dry_ratio) * 20)
        score += min(15, (f_pos_days + i_pos_days) * 2)

        themes = theme_map.get(pure_name, ["테마 미분류"])

        # R:R 불량 제외
        if rr_ratio < 1.5:
            continue

        passed.append({
            "pure_name":   pure_name,
            "score":       score,
            "curr_price":  curr_price,
            "ma20":        round(ma20, 0),
            "ma200":       round(ma200, 0),
            "dist_ma20":   round(dist_ma20 * 100, 1),
            "gap200":      round(gap200 * 100, 1),
            "recent_amp":  round(recent_amp * 100, 2),
            "prev_amp":    round(prev_amp * 100, 2),
            "vol_dry_pct": round(dry_ratio * 100, 1),
            "f_pos_days":  f_pos_days,
            "i_pos_days":  i_pos_days,
            "f_cum":       f_cum,
            "i_cum":       i_cum,
            "themes":      themes[:2],
            # ATR
            "atr":         round(atr, 0),
            "stop_price":  stop_price,
            "tgt_price":   tgt_price,
            "stop_pct":    stop_pct,
            "tgt_pct":     tgt_pct,
            "rr_ratio":    rr_ratio,
        })

    conn.close()

    # ── 섹터 군집 계산 (200일선 통과 사전 활용) ──────────────
    for item in passed:
        cluster_stocks = []
        for theme in item["themes"]:
            # 같은 테마에 속한 종목 중 200일선 위인 것들
            for sname, themes_list in theme_map.items():
                if theme in themes_list and sname in ma200_pass and sname != item["pure_name"]:
                    cluster_stocks.append(sname)

        cluster_stocks = list(set(cluster_stocks))
        item["cluster_cnt"]    = len(cluster_stocks)
        item["cluster_stocks"] = cluster_stocks[:3]  # 대장 후보 최대 3개

        # 군집 보너스 점수 (최대 15점)
        if item["cluster_cnt"] >= CLUSTER_MIN:
            item["score"] += min(15, item["cluster_cnt"] * 3)

    # ── 정렬 & 선출 ──────────────────────────────────────────
    passed.sort(key=lambda x: x["score"], reverse=True)
    top = passed[:top_n]

    if not top:
        return (
            f"💡 [스윙 엔진] 총 {filtered['total']}종목 분석 완료.\n"
            f"   ① 200일선 미달   : {filtered['f1_ma200']}개\n"
            f"   ② 20일선 이탈    : {filtered['f2_ma20']}개\n"
            f"   ③ VCP 미수렴     : {filtered['f3_vcp']}개\n"
            f"   ④ 거래량 살아있음: {filtered['f4_vol']}개\n"
            f"   ⑤ 스마트머니 미감지: {filtered['f5_smart']}개\n"
            f"   → 조건 통과 종목 없어. 파라미터 조정을 고려해봐."
        )

    # ── 리포트 ───────────────────────────────────────────────
    report  = "🎯 **[스윙 추천 엔진 v2 — ATR 손절/목표 + 섹터 군집]** 🎯\n"
    report += f"   분석 {filtered['total']}종목 → 200일선 통과 {len(ma200_pass)}종목 → 최종 {len(passed)}종목 → 상위 {len(top)}개\n"
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
            cluster_tag = f"🔥 강한 군집 ({item['cluster_cnt']}종목 동반)"
            cluster_names = " / ".join(item["cluster_stocks"]) if item["cluster_stocks"] else ""
            cluster_line  = f"    🏆 테마 동반주  : {cluster_names} 등\n"
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
            f"    📊 20일선      : {item['ma20']:,.0f}원  (이격 {item['dist_ma20']}%)\n"
            f"    📈 200일선     : {item['ma200']:,.0f}원  (상단 +{item['gap200']}%)\n"
            f"    🔻 VCP 수렴    : 이전 {item['prev_amp']}% → 최근 {item['recent_amp']}% "
            f"({round(item['recent_amp']/item['prev_amp']*100) if item['prev_amp'] else 0}% 압축)\n"
            f"    💤 거래량 마름 : 평소 대비 {item['vol_dry_pct']}% 수준\n"
            f"    🕵️  스마트머니  : {smart_tag} "
            f"(외인 {item['f_pos_days']}일 / 기관 {item['i_pos_days']}일)\n"
            f"------------------------------------------------------------"
        )

    report += f"\n\n   ⚙️ ATR{ATR_PERIOD}일 / 손절×{ATR_STOP_MULT} / 목표×{ATR_TARGET_MULT} / 군집기준 {CLUSTER_MIN}종목\n"
    return report


if __name__ == "__main__":
    print(get_swing_picks(top_n=5))