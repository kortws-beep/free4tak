"""
sbot2_strategy.py — 중기 스윙봇2 전략 (v2 전면 개편)
================================================================
[컨셉] 메가 트렌드 추세 추종 (20영업일)

[핵심 로직]
  1. 200일선 정배열 우상향
     - MA200 위에서 탄탄하게 지지받는 우량 종목
     - MA20 > MA60 > MA200 완전 정배열

  2. 펀더멘탈 결합
     - ROE 10% 이상 (자기자본이익률)
     - 실적 베이스가 받쳐주는 섹터 대장주

  3. 누적 수급 (메이저 매집 확인)
     - 하루이틀 단발성이 아닌 15일 누적 외인+기관 동반 매집
     - 단발성 쌍끌이(5일) + 누적 매집(15일) 동시 확인

[청산]
  익절: +15% / +25% / +40%
  손절: -10% (추세추종이라 여유있게)
  시간청산: 20영업일
  트레일링: 고점 -8% (1차 후) / -10% (2차 후)
  MA200 이탈: 1차 익절 후 MA200 하향 돌파 시 청산

[sbot1 vs sbot2]
  sbot1: VCP 초입 + 텔레그램 핫테마 + 단발 쌍끌이 (10일)
  sbot2: 200일선 정배열 + ROE + 15일 누적매집 (20일)
================================================================
"""
from typing import Optional, Callable
import datetime


# ==========================================================
# 매도 기준
# ==========================================================
SELL_1ST_RATE   = 0.15    # 1차 익절: +15%
SELL_1ST_QTY    = 0.30    # 30% 매도
SELL_2ND_RATE   = 0.25    # 2차 익절: +25%
SELL_2ND_QTY    = 0.40    # 40% 매도
SELL_3RD_RATE   = 0.40    # 3차 익절: +40% 전량

TRAIL_STOP_AFTER_1ST = 0.08    # 1차 후: 고점 -8%
TRAIL_STOP_AFTER_2ND = 0.10    # 2차 후: 고점 -10%

STOP_LOSS_BASIC     = -0.10   # 기본: -10% (추세추종 여유)
STOP_LOSS_AFTER_1ST = -0.05   # 1차 익절 후 본절 보호 -5%
STOP_LOSS_WEAK      = -0.07   # 약세장: -7%

TIME_STOP_DAYS = 20   # 20영업일

# MA200 이탈 매도 (1차 익절 후)
MA200_EXIT_RATE = 0.97   # MA200 × 0.97 이탈 시 청산

# 가산점
ROE_BONUS        = 15   # ROE 15% 이상
ACCUM_BONUS      = 20   # 15일 누적 쌍끌이
MA200_ALIGN_BONUS = 15  # 완전 정배열 (MA20>MA60>MA200)
MEGA_TREND_BONUS  = 10  # 52주 신고가 근처


class MidSwingStrategy:
    """sbot2 중기 추세추종 전략."""

    # ============================================================
    # 1. 룰 점수 (중기 추세추종 특화)
    # ============================================================
    def get_rule_score(self, data: dict) -> int:
        """
        중기 관점 점수 — 추세/펀더멘탈/누적수급 중심

        [가중치]
          기본:              30
          MA200 정배열:      +15  ★★★ 핵심
          200일선 위:        +10
          누적수급 15일:     +20  ★★★ 핵심
          ROE:               +15
          52주 신고가 근처:  +10
          5일 단발 쌍끌이:   +10
          거래대금:          +8
          RSI (40~60):       +8
          텔레그램 이벤트:   별도 가산 (event_bonus)
          ─────────────────
          최대:              126 → cap 100
        """
        score = 30

        ma5    = data.get("ma5",    0)
        ma20   = data.get("ma20",   0)
        ma60   = data.get("ma60",   0)
        ma200  = data.get("ma200",  0)
        cur    = data.get("current_price", 0)
        rsi    = data.get("rsi",    50)
        tvol   = data.get("trading_value", 0)
        high_52w = data.get("high_52w", 0)

        # 수급
        foreign_5d   = data.get("foreign_5d",      0)
        orgn_5d      = data.get("institution_5d",  0)
        foreign_15d  = data.get("foreign_15d",     0)
        orgn_15d     = data.get("institution_15d", 0)

        # 펀더멘탈
        roe    = data.get("roe",    0)
        op_yoy = data.get("op_yoy", 0)

        # ── ★ MA200 위 (필수 조건 — 점수로 반영) ────────────
        if ma200 > 0 and cur > 0:
            if cur > ma200:
                score += 10   # 200일선 위

                # ★ 완전 정배열 MA20 > MA60 > MA200
                if ma20 > 0 and ma60 > 0:
                    if ma20 > ma60 > ma200:
                        score += MA200_ALIGN_BONUS
                        print(f"   📈 MA200 완전정배열 +{MA200_ALIGN_BONUS}점")
                    elif ma60 > ma200:
                        score += 8    # 부분 정배열

        # ── ★ 누적 수급 15일 (핵심) ──────────────────────────
        if foreign_15d > 0 and orgn_15d > 0:
            # 외인+기관 15일 동반 누적 매집
            score += ACCUM_BONUS
            print(f"   💰 15일 누적 쌍끌이 +{ACCUM_BONUS}점 (외:{foreign_15d:,} 기:{orgn_15d:,})")
        elif foreign_15d > 0 or orgn_15d > 0:
            score += 10   # 한쪽만 누적

        # ── 단발 5일 쌍끌이 (추가 확인) ─────────────────────
        if foreign_5d > 0 and orgn_5d > 0:
            score += 10
        elif foreign_5d > 0 or orgn_5d > 0:
            score += 5

        # ── ★ ROE (펀더멘탈) ─────────────────────────────────
        if roe >= 15:
            score += ROE_BONUS
            print(f"   💎 ROE {roe:.1f}% +{ROE_BONUS}점")
        elif roe >= 10:
            score += 8
        elif roe >= 5:
            score += 4

        # ── 52주 신고가 근처 (메가트렌드 확인) ───────────────
        if high_52w > 0 and cur > 0:
            ratio = cur / high_52w
            if ratio >= 0.95:
                score += MEGA_TREND_BONUS
                print(f"   🚀 52주 고가 근처 +{MEGA_TREND_BONUS}점 ({ratio:.1%})")
            elif ratio >= 0.85:
                score += 5

        # ── 거래대금 ─────────────────────────────────────────
        if   tvol >= 500: score += 8
        elif tvol >= 200: score += 5
        elif tvol >= 100: score += 3

        # ── RSI (40~60 추세 진행 중) ─────────────────────────
        if   40 <= rsi <= 60: score += 8   # 추세 진행 중 이상적
        elif 60 < rsi <= 70:  score += 4   # 약간 과열
        elif rsi > 75:        score -= 5   # 단기 과열
        elif rsi < 35:        score -= 3   # 과매도 (추세 이탈 위험)

        # ── 실적 개선 ────────────────────────────────────────
        if op_yoy >= 30:
            score += 5

        return min(100, max(0, score))

    # ============================================================
    # 2. 매수 필터 (중기 추세추종 — 엄격)
    # ============================================================
    def passes_buy_filter(self, data: dict) -> tuple:
        """반환: (통과 여부, 사유)"""
        ma20  = data.get("ma20",  0)
        ma60  = data.get("ma60",  0)
        ma200 = data.get("ma200", 0)
        cur   = data.get("current_price", 0)
        rsi   = data.get("rsi",   50)
        tvol  = data.get("trading_value", 0)

        # ★ VI 발동 제외
        if data.get("iscd_stat_cls_code", "55") == "51":
            return False, "VI 발동 중"

        # 상한가 제외
        if data.get("change_rate", 0) >= 29.5:
            return False, "상한가 제외"

        # ★★★ 핵심: MA200 위 필수 (중기 추세추종 최우선 조건)
        if ma200 > 0 and cur > 0:
            if cur < ma200:
                return False, f"MA200 하방 ({cur:,.0f} < MA200:{ma200:,.0f}) — 하락추세 제외"

        # MA60 완전 하락추세 제외
        if ma60 > 0 and cur > 0 and cur < ma60 * 0.90:
            return False, "MA60 대폭 하방 — 강한 하락추세"

        # RSI 과매수 (80 이상) 제외
        if rsi >= 80:
            return False, f"RSI 과매수 ({rsi:.0f})"

        # 급락 종목 제외
        if data.get("change_rate", 0) <= -7:
            return False, f"급락 제외 ({data.get('change_rate', 0):.1f}%)"

        return True, ""

    # ============================================================
    # 3. 가산점
    # ============================================================
    def apply_bonus(self, code: str, score: int,
                    new_codes_list: list = None) -> tuple:
        """new 그룹 가산점"""
        NEW_BONUS = 7
        if new_codes_list and code in new_codes_list:
            new_score = min(100, score + NEW_BONUS)
            return new_score, f"신규추천(+{NEW_BONUS})"
        return score, ""

    # ============================================================
    # 4. 매도 체크 (중기 — 넓은 밴드 + MA200 이탈)
    # ============================================================
    def check_sell(self, code: str, pos: dict,
                   market_data: dict, market_status: str,
                   peak_tracker: dict, is_paused: bool,
                   on_buy: Callable, on_sell: Callable, on_loss: Callable,
                   ma20: float = 0, ma200: float = 0,
                   atr_rate: float = 0,
                   vol_ratio: float = 0.0,
                   now_t: str = '1200') -> Optional[str]:
        """중기 매도 의사결정."""
        if not market_data:
            return None

        current = float(market_data.get("stck_prpr", 0))
        entry   = pos["entry_price"]
        qty     = pos["qty"]
        if entry == 0 or current == 0 or qty <= 0:
            return None

        rate = (current - entry) / entry

        if code not in peak_tracker:
            peak_tracker[code] = {
                "peak_rate":       rate,
                "stage":           0,
                "remain_qty":      qty,
                "buy2_done":       True,
                "buy1_price":      entry,
                "effective_entry": entry,
                "holding_days":    0,
                "buy_date":        pos.get("buy_date", ""),
            }

        tracker         = peak_tracker[code]
        stage           = tracker["stage"]
        peak_rate       = tracker["peak_rate"]
        effective_entry = tracker.get("effective_entry", entry)

        if rate > peak_rate:
            tracker["peak_rate"] = rate
            peak_rate            = rate

        # ── ★ 시간청산 (20영업일) ──────────────────────────────
        _today   = datetime.date.today()
        buy_date = pos.get("buy_date", tracker.get("buy_date", ""))
        if buy_date:
            try:
                _bd      = datetime.date.fromisoformat(str(buy_date)[:10])
                _holding = (_today - _bd).days
            except Exception:
                _holding = tracker.get("holding_days", 0)
        else:
            pos["buy_date"] = _today.isoformat()
            _holding = 0
            tracker["holding_days"] = 0

        print(f"  ⏱️ [MID] {code} 보유{_holding}일 (기준:{TIME_STOP_DAYS}일)")

        if _holding >= TIME_STOP_DAYS and stage < 1:
            on_sell(code, qty, f"시간청산({_holding}일)", current)
            return "시간청산"

        # ── ★ 약세장 손절 (손실 종목만) ─────────────────────
        if market_status == "stop" and stage < 1:
            eff_rate = (current - effective_entry) / effective_entry
            if eff_rate <= STOP_LOSS_WEAK:
                on_sell(code, qty, f"약세장손절({eff_rate:+.1%})", current)
                on_loss()
                return "약세장손절"

        # ── ★ 기본 손절 / 본절 보호 ─────────────────────────
        stop     = STOP_LOSS_BASIC if stage == 0 else STOP_LOSS_AFTER_1ST
        eff_rate = (current - effective_entry) / effective_entry
        if eff_rate <= stop:
            reason = f"손절({eff_rate:+.1%})" if stage == 0 else f"본절보호({eff_rate:+.1%})"
            on_sell(code, tracker["remain_qty"], reason, current)
            if stage == 0:
                on_loss()
            return reason

        # ── ★ MA200 이탈 매도 (1차 익절 후 — 추세 이탈 신호) ─
        if ma200 > 0 and current < ma200 * MA200_EXIT_RATE and stage >= 1:
            on_sell(code, tracker["remain_qty"],
                    f"MA200이탈({current:,.0f}<{ma200:,.0f})", current)
            return "MA200이탈"

        # ── ★ 트레일링 스탑 ──────────────────────────────────
        if stage >= 1:
            trail = TRAIL_STOP_AFTER_1ST if stage == 1 else TRAIL_STOP_AFTER_2ND
            trail_stop = peak_rate - trail
            if rate <= trail_stop:
                on_sell(code, tracker["remain_qty"],
                        f"트레일링({rate:+.1%}↓{peak_rate:+.1%})", current)
                return "트레일링"

        # ── ★ 익절 ───────────────────────────────────────────
        remain = tracker["remain_qty"]

        # 3차 익절: +40%
        if rate >= SELL_3RD_RATE and stage >= 2:
            on_sell(code, remain, f"3차익절(+{rate:.1%})", current)
            tracker["stage"] = 3
            return "3차익절"

        # 2차 익절: +25%
        if rate >= SELL_2ND_RATE and stage == 1:
            qty2 = max(1, round(remain * SELL_2ND_QTY))
            on_sell(code, qty2, f"2차익절(+{rate:.1%})", current)
            tracker["remain_qty"] = remain - qty2
            tracker["stage"]      = 2
            tracker["effective_entry"] = effective_entry * 0.5 + current * 0.5
            return "2차익절"

        # 1차 익절: +15%
        if rate >= SELL_1ST_RATE and stage == 0:
            qty1 = max(1, round(qty * SELL_1ST_QTY))
            on_sell(code, qty1, f"1차익절(+{rate:.1%})", current)
            tracker["remain_qty"] = remain - qty1
            tracker["stage"]      = 1
            tracker["effective_entry"] = entry
            return "1차익절"

        return None
