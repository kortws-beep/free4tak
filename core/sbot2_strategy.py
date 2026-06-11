"""
sbot2_strategy.py — 중단기 스윙봇2 전략
================================================================
[컨셉]
  sbot1 (단기 10일) 과 차별화된 중단기 (20일) 전략.
  기술적 타점 + 재료 + 수급이 모두 맞아떨어질 때만 진입.

[진입 전략]
  [필수 — 기술적 타점]
  ① 추세 추종: 20일선 위 + 정배열 (5>20>60)
  ② 눌림목: 전고점 대비 -10~20% 조정 후 반등 초입
  ③ VCP: 거래량 수렴 후 확산 (변동성 수축 패턴)

  [가산 — 재료/수급]
  ④ 실적 턴어라운드: 적자→흑자, 영업이익 +50% 이상
  ⑤ 수급 전환: 외국인/기관 5일 순매수 전환
  ⑥ 공시 이벤트: KIND 텔레그램 연동 (이미 구축)
  ⑦ 텔레그램 테마 모멘텀 (이미 구축)
  ⑧ consensus 리포트 상향

[청산]
  익절: +15% / +25% / +40%
  손절: -10%
  시간청산: 20영업일
  트레일링: 고점 -8%
  본절 보호: 1차 익절 후 -5%

[sbot1 vs sbot2 차별화]
  - 같은 종목 동시 보유 금지 (master_db 교차 체크)
  - sbot2는 대형주/주도섹터 대장주 위주
  - 진입 기준 더 높음 (점수 임계치 +5)
================================================================
"""
from typing import Optional, Callable


# ==========================================================
# 매도 단계별 기준 (sbot1보다 넓은 밴드)
# ==========================================================
SELL_1ST_RATE   = 0.15    # 1차 익절: +15%
SELL_1ST_QTY    = 0.30    # 30% 매도
SELL_2ND_RATE   = 0.25    # 2차 익절: +25%
SELL_2ND_QTY    = 0.40    # 40% 매도
SELL_3RD_RATE   = 0.40    # 3차 익절: +40% 전량

# 트레일링
TRAIL_STOP_AFTER_1ST = 0.08    # 1차 후: 고점 -8%
TRAIL_STOP_AFTER_2ND = 0.10    # 2차 후: 고점 -10%

# 손절
STOP_LOSS_BASIC     = -0.10   # 기본: -10%
STOP_LOSS_AFTER_1ST = -0.05   # 1차 익절 후 본절 보호 -5%
STOP_LOSS_WEAK      = -0.07   # 약세장: -7%

# 시간청산
TIME_STOP_DAYS = 20   # 20영업일

# 가산점
NEW_BONUS         = 7
TURNROUND_BONUS   = 15   # 실적 턴어라운드
SUPPLY_BONUS      = 10   # 수급 전환
VCP_BONUS         = 12   # VCP 패턴
PULLBACK_BONUS    = 10   # 눌림목 타점


class MidSwingStrategy:
    """sbot2 중단기 매수/매도 전략."""

    # ============================================================
    # 1. 룰 점수 (중단기 특화)
    # ============================================================
    def get_rule_score(self, data: dict) -> int:
        """
        중단기 관점 점수.

        [가중치]
          기본점수:        30
          MA 배열/추세:    +20  ← 핵심 (중단기는 추세가 생명)
          눌림목 타점:     +10
          VCP 패턴:        +12
          실적 턴어라운드: +15
          수급 전환:       +10
          거래대금:        +8
          RSI:             +8
          외국인 5d:       +10
          기관 5d:         +8
          ─────────────────
          최대 합산:       131 → cap 100
        """
        score = 30

        change   = data.get("change_rate",    0)
        tvol     = data.get("trading_value",  0)    # 억원
        ma5      = data.get("ma5",            0)
        ma20     = data.get("ma20",           0)
        ma60     = data.get("ma60",           0)
        ma120    = data.get("ma120",          0)
        rsi      = data.get("rsi",            50)
        foreign5 = data.get("foreign_5d",     0)
        orgn5    = data.get("institution_5d", 0)
        high_52w = data.get("high_52w",       0)    # 52주 신고가
        low_52w  = data.get("low_52w",        0)    # 52주 저가
        cur      = data.get("current_price",  0)
        vol_ratio = data.get("volume_ratio",  0)    # 최근 거래량/평균

        # ── ★ MA 배열/추세 (+0~20) ──────────────────────────
        if ma5 > 0 and ma20 > 0 and ma60 > 0:
            if ma5 > ma20 > ma60:
                score += 15   # 완전 정배열
                if ma120 > 0 and ma60 > ma120:
                    score += 5   # 장기 정배열까지
            elif ma5 > ma20:
                score += 8    # 단기 정배열
            elif cur > ma20:
                score += 4    # 20일선 위

        # ── ★ 눌림목 타점 (+0~10) ────────────────────────────
        # 전고점(52주 고가) 대비 -10~25% 구간 = 눌림목
        if high_52w > 0 and cur > 0:
            pullback = (cur - high_52w) / high_52w
            if -0.25 <= pullback <= -0.10:
                score += PULLBACK_BONUS
                print(f"   📍 눌림목 타점 +{PULLBACK_BONUS}점 ({pullback:+.1%})")
            # 52주 신고가 돌파 후 눌림
            elif pullback >= -0.05:
                score += 6    # 신고가 근처

        # ── ★ VCP 패턴 (+0~12) ──────────────────────────────
        # 거래량 수렴 후 확산: vol_ratio < 0.7 구간 후 반등
        bb_width = data.get("bb_width", 0)
        if vol_ratio > 0:
            if 0.5 <= vol_ratio <= 0.8 and bb_width < 0.04:
                # 거래량 수렴 + 볼린저밴드 수렴 = VCP 진행 중
                score += VCP_BONUS
                print(f"   🔀 VCP 패턴 +{VCP_BONUS}점 (vol:{vol_ratio:.1f}x bb:{bb_width:.3f})")
            elif vol_ratio >= 1.5 and bb_width > 0.05:
                # 수렴 후 확산 (돌파 시점)
                score += 8

        # ── ★ 실적 턴어라운드 (+0~15) ────────────────────────
        # roe > 0 (흑자) + 전년 대비 영업이익 급증
        roe      = data.get("roe",          0)
        eps_yoy  = data.get("eps_yoy",      0)    # EPS 전년대비 증가율
        op_yoy   = data.get("op_yoy",       0)    # 영업이익 전년대비
        if roe > 0 and op_yoy >= 50:
            score += TURNROUND_BONUS
            print(f"   📈 실적턴어라운드 +{TURNROUND_BONUS}점 (ROE:{roe:.1f}% OP:{op_yoy:.0f}%)")
        elif roe > 0 and op_yoy >= 20:
            score += 8

        # ── ★ 수급 전환 (+0~10) ──────────────────────────────
        foreign_today = data.get("foreign_today", 0)
        orgn_today    = data.get("orgn_today",    0)
        if foreign5 > 0 and orgn5 > 0:
            score += SUPPLY_BONUS
            print(f"   💰 수급전환 +{SUPPLY_BONUS}점 (외:{foreign5:,} 기:{orgn5:,})")
        elif foreign5 > 0:
            score += 6
        elif orgn5 > 0:
            score += 4

        # ── 거래대금 (+0~8) ──────────────────────────────────
        if   tvol >= 500: score += 8
        elif tvol >= 200: score += 5
        elif tvol >= 100: score += 3
        elif tvol >=  50: score += 1

        # ── RSI (+0~8) ────────────────────────────────────────
        # 중단기는 RSI 40~65 구간이 최적 (과열 아닌 상승 초입)
        if   40 <= rsi <= 55: score += 8    # 눌림목 RSI
        elif 55 < rsi <= 65:  score += 5
        elif 35 <= rsi < 40:  score += 3
        elif rsi > 75:        score -= 5    # 단기 과열

        return min(100, max(0, score))

    # ============================================================
    # 2. 매수 필터 (중단기 특화)
    # ============================================================
    def passes_buy_filter(self, data: dict) -> tuple:
        """반환: (통과 여부, 사유)"""
        change  = data.get("change_rate",   0)
        ma5     = data.get("ma5",           0)
        ma20    = data.get("ma20",          0)
        ma60    = data.get("ma60",          0)
        cur     = data.get("current_price", 0)
        rsi     = data.get("rsi",           50)

        # ★ VI 발동 제외
        if data.get("iscd_stat_cls_code", "55") == "51":
            return False, "VI 발동 중"

        # 상한가 제외
        if change >= 29.5:
            return False, "상한가 제외"

        # ★ 핵심: 20일선 위에 있어야 함 (중단기 필수 조건)
        if ma20 > 0 and cur > 0 and cur < ma20 * 0.97:
            return False, f"20일선 하방 ({cur:,.0f} < MA20:{ma20:,.0f})"

        # ★ 60일선 완전 하방이면 제외 (하락추세)
        if ma60 > 0 and cur > 0 and cur < ma60 * 0.92:
            return False, "60일선 하방 — 하락추세"

        # RSI 과매수 제외 (80 이상)
        if rsi >= 80:
            return False, f"RSI 과매수 ({rsi:.0f})"

        # 급락 종목 제외 (-5% 이하)
        if change <= -5:
            return False, f"급락 제외 ({change:.1f}%)"

        return True, ""

    # ============================================================
    # 3. 가산점
    # ============================================================
    def apply_bonus(self, code: str, score: int,
                    new_codes_list: list = None) -> tuple:
        """new 그룹 가산점"""
        if new_codes_list and code in new_codes_list:
            new_score = min(100, score + NEW_BONUS)
            return new_score, f"신규추천(+{NEW_BONUS})"
        return score, ""

    # ============================================================
    # 4. 매도 체크 (중단기 — 넓은 밴드)
    # ============================================================
    def check_sell(self, code: str, pos: dict,
                   market_data: dict, market_status: str,
                   peak_tracker: dict, is_paused: bool,
                   on_buy: Callable, on_sell: Callable, on_loss: Callable,
                   ma20: float = 0,
                   atr_rate: float = 0,
                   vol_ratio: float = 0.0,
                   now_t: str = '1200') -> Optional[str]:
        """중단기 매도 의사결정."""
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
        import datetime as _dt
        _today   = _dt.date.today()
        buy_date = pos.get("buy_date", tracker.get("buy_date", ""))
        if buy_date:
            try:
                _bd      = _dt.date.fromisoformat(str(buy_date)[:10])
                _holding = (_today - _bd).days
            except Exception:
                _holding = tracker.get("holding_days", 0)
        else:
            _holding = tracker.get("holding_days", 0)

        print(f"  ⏱️ [MID] {code} 보유{_holding}일 (기준:{TIME_STOP_DAYS}일)")

        if _holding >= TIME_STOP_DAYS and stage < 1:
            on_sell(code, qty, f"시간청산({_holding}일)", current)
            return "시간청산"

        # ── ★ 손절 ───────────────────────────────────────────
        # 약세장 손절
        if market_status == "stop" and stage < 1:
            stop_rate = STOP_LOSS_WEAK
            eff_rate  = (current - effective_entry) / effective_entry
            if eff_rate <= stop_rate:
                on_sell(code, qty, f"약세장손절({eff_rate:+.1%})", current)
                on_loss()
                return "약세장손절"

        # 기본 손절 / 본절 보호
        if stage == 0:
            stop = STOP_LOSS_BASIC
        else:
            stop = STOP_LOSS_AFTER_1ST   # 1차 익절 후 -5% 본절 보호

        eff_rate = (current - effective_entry) / effective_entry
        if eff_rate <= stop:
            reason = f"손절({eff_rate:+.1%})" if stage == 0 else f"본절보호({eff_rate:+.1%})"
            on_sell(code, tracker["remain_qty"], reason, current)
            if stage == 0:
                on_loss()
            return reason

        # ── ★ 트레일링 스탑 ──────────────────────────────────
        trail = TRAIL_STOP_AFTER_1ST if stage >= 1 else None
        if trail and stage >= 1:
            trail_stop = peak_rate - trail
            if rate <= trail_stop:
                on_sell(code, tracker["remain_qty"],
                        f"트레일링({rate:+.1%}↓{peak_rate:+.1%})", current)
                return "트레일링"

        # ── ★ MA20 이탈 매도 (중단기 핵심) ──────────────────
        if ma20 > 0 and current < ma20 * 0.97 and stage >= 1:
            on_sell(code, tracker["remain_qty"],
                    f"MA20이탈({current:,.0f}<{ma20:,.0f})", current)
            return "MA20이탈"

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
            # effective_entry 보정
            tracker["effective_entry"] = effective_entry * 0.5 + current * 0.5
            return "2차익절"

        # 1차 익절: +15%
        if rate >= SELL_1ST_RATE and stage == 0:
            qty1 = max(1, round(qty * SELL_1ST_QTY))
            on_sell(code, qty1, f"1차익절(+{rate:.1%})", current)
            tracker["remain_qty"] = remain - qty1
            tracker["stage"]      = 1
            tracker["effective_entry"] = entry  # 본절 보호 기준
            return "1차익절"

        return None
