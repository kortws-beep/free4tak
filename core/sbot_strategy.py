"""
sbot_strategy.py — 스윙봇 매수/매도 전략 (개선판)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

스윙봇은 단타봇과 달리 며칠~1주일 보유하는 전략입니다.
- 매수 금액: 단타(20만원) 보다 큰 200만원/종목
- 매도 기준: 1차 +8%, 2차 +15%, 손절 -7% (단타보다 넓음)
- 보유 종목: 최대 3개 (단타 5개보다 적음, 큰 자금 집중)

[주요 개선 사항]
1. ★ 1차 익절 후 본절 보호 (-3% 이내로 떨어지면 청산)
2. ★ 분할 익절 후 effective_entry 보정
3. ★ MA20 이탈 시 매도 (스윙은 일봉 기준이 중요)
4. ★ 트레일링 스탑 stage>=1부터 작동
5. ★ ATR 기반 동적 손절선 옵션
================================================================
"""
from typing import Optional, Callable


# ==========================================================
# 매도 단계별 기준
# ==========================================================
SELL_1ST_RATE   = 0.08    # 1차 익절: +8%
SELL_1ST_QTY    = 0.30    # 30% 매도
SELL_2ND_RATE   = 0.15    # 2차 익절: +15%
SELL_2ND_QTY    = 0.40    # 40% 매도
SELL_3RD_RATE   = 0.25    # 3차 익절: +25% 전량

# 트레일링
TRAIL_STOP_AFTER_1ST = 0.05    # 1차 후: 5%
TRAIL_STOP_AFTER_2ND = 0.07    # 2차 후: 7%

# 손절
STOP_LOSS_BASIC      = -0.07   # 기본: -7%
STOP_LOSS_AFTER_1ST  = -0.03   # ★ 1차 익절 후 본절 보호
STOP_LOSS_WEAK       = -0.05   # 약세장: -5%

# ==========================================================
# 분할매수
# ==========================================================
BUY_2ND_AMT       = 500_000    # 2차 매수 금액 (실전 50만원)
BUY_2ND_THRESHOLD = -0.03      # -3% 하락 시 물타기

# ==========================================================
# 가산점
# ==========================================================
NEW_BONUS = 7   # new 그룹 종목


class SwingStrategy:
    """스윙봇 매수/매도 전략."""

    # ============================================================
    # 1. 룰 점수 (스윙 특화)
    # ============================================================
    def get_rule_score(self, data: dict) -> int:
        """
        스윙 관점에서의 점수 (v2 — 점수 분포 개선판)

        [변경 이유]
        기존: 대형주(삼성전자급)는 거래대금+MA+수급 만으로 130~155점 → 100점 cap
              → 임계치 60~90이 모두 동일 결과 (백테스트 의미 없음)
        개선1: 각 항목 최대 가중치 축소 (최대합산 155→113)
        개선2: 기본점수 50→30 (대형주 베이스 75→55점으로 하향)
               → 대형주 정상조건: 55+12+15+8+10+8 = 108 → cap 100
               → 대형주 보통조건: 55+5+10+8+3+2   = 83점
               → 임계치 70/75/80/85가 실제 다른 종목 선별

        [가중치 변경]
          항목          기존최대   개선최대
          기본점수        50        30   ★ 핵심 변경
          등락률          +20       +12
          거래대금        +20       +10
          MA배열          +25       +15  ← 핵심 유지
          RSI             +10       +8
          외국인5d        +15       +10
          기관5d          +15       +8
          ─────────────────────────────
          최대합산    50+105=155  30+63=93 → 실질 분포 30~93점
        """
        try:
            score       = 30   # ★ v2: 50→30 (대형주 몰림 방지, 분포 개선)
            change      = data.get("change_rate",   0)
            value       = data.get("trading_value", 0)
            rsi         = data.get("rsi",           50)
            ma5         = data.get("ma5",            0)
            ma20        = data.get("ma20",           0)
            ma60        = data.get("ma60",           0)
            foreign     = data.get("foreign_5d",     0)
            institution = data.get("institution_5d", 0)

            # 등락률 (최대 +12)
            if   change > 5:  score += 12
            elif change > 3:  score += 8
            elif change > 1:  score += 5
            else:             score -= 5

            # 거래대금 (최대 +10)
            if   value > 500: score += 10
            elif value > 200: score += 7
            elif value > 100: score += 3
            elif value < 50:  score -= 10

            # MA 배열 (최대 +15) ← 추세 핵심 지표 유지
            if   ma5 > ma20 > ma60 > 0: score += 15
            elif ma5 > ma20 > 0:        score += 8
            else:                       score -= 8

            # RSI (최대 +8)
            if   40 < rsi < 70:  score += 8
            elif rsi > 80:       score -= 15
            elif rsi < 30:       score -= 3

            # 외국인 5일 수급 (최대 +10)
            if   foreign > 10000: score += 10
            elif foreign > 5000:  score += 7
            elif foreign > 1000:  score += 3
            elif foreign < -5000: score -= 8

            # 기관 5일 수급 (최대 +8)
            if   institution > 10000: score += 8
            elif institution > 5000:  score += 5
            elif institution > 1000:  score += 2
            elif institution < -5000: score -= 5

            return max(0, min(100, score))
        except Exception as e:
            print(f"⚠️ 스윙 룰 점수 오류: {e}")
            return 0

    # ============================================================
    # 2. 매수 필터 (양봉 조건 면제 로직)
    # ============================================================
    def passes_buy_filter(self, data: dict, is_new: bool = False) -> tuple:
        """반환: (통과 여부, 사유)"""
        change   = data.get("change_rate", 0)
        ma5      = data.get("ma5", 0)
        ma20     = data.get("ma20", 0)
        foreign  = data.get("foreign_5d", 0)

        if change >= 29.5:
            return False, "상한가 제외"

        # ★ 추세 강한 종목은 음봉/약양봉도 허용
        is_strong = (ma5 > ma20 > 0 and foreign > 5000) or is_new
        if is_strong:
            if change < -2:
                return False, "약세종목(-2% 미만)"
        else:
            if change < 1.0:
                return False, "양봉 미달(+1% 미만)"

        return True, ""

    # ============================================================
    # 3. new 그룹 가산점
    # ============================================================
    def apply_new_bonus(self, code: str, score: int,
                        new_codes_list: list) -> tuple:
        """반환: (보정 점수, 보정 이유)"""
        if not new_codes_list or code not in new_codes_list:
            return score, ""
        new_score = min(100, score + NEW_BONUS)
        reason    = f"신규추천(+{NEW_BONUS})"
        print(f"   🆕 new 가점 {code}: {score}→{new_score}점")
        return new_score, reason

    # ============================================================
    # 4. 매도 체크 (★ 본절 보호 + effective_entry)
    # ============================================================
    def check_sell(self, code: str, pos: dict,
                   market_data: dict, market_status: str,
                   peak_tracker: dict, is_paused: bool,
                   on_buy: Callable, on_sell: Callable, on_loss: Callable,
                   ma20: float = 0,
                   atr_rate: float = 0,
                   vol_ratio: float = 0.0,
                   now_t: str = '1200') -> Optional[str]:  # ★ 백테스트 호환
        """
        스윙 매도 의사결정.
        매개변수는 단타와 비슷하되, ma10 → ma20 (스윙은 20일선 기준).
        """
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
            }

        tracker         = peak_tracker[code]
        stage           = tracker["stage"]
        peak_rate       = tracker["peak_rate"]
        buy2_done       = tracker.get("buy2_done", True)
        buy1_price      = tracker.get("buy1_price", entry)

        if rate > peak_rate:
            tracker["peak_rate"] = rate
            peak_rate            = rate

        # ----------------------------------------------------------
        # ① 2차 분할매수 (물타기) — 강화 조건
        # ----------------------------------------------------------
        is_weak   = market_status in ("weak", "stop")
        buy2_rate = (current - buy1_price) / buy1_price if buy1_price else 0

        # ★ 강화 조건: MA20 위 + 시장 normal + 거래량 1.5배↑
        ma20_ok   = (ma20 > 0 and current >= ma20)
        mkt_ok    = (market_status == "normal")
        # ★ vol_ratio 실제 연동 (기존 True 고정 → 실제 조건)
        # vol_ratio=0 이면 데이터 없음 → 조건 통과 (보수적 허용)
        VOL_RATIO_MIN = 150.0   # 전일 대비 1.5배 이상 (150%)
        vol_ok = (vol_ratio <= 0) or (vol_ratio >= VOL_RATIO_MIN)

        if (not buy2_done and stage == 0
                and buy2_rate <= BUY_2ND_THRESHOLD
                and not is_paused and not is_weak
                and ma20_ok and mkt_ok and vol_ok):
            print(f"➕ 2차 매수(물타기) {code} | {buy2_rate:+.2%} | "
                  f"MA20:{ma20:,.0f} | 거래량:{vol_ratio:.0f}%")
            on_buy(code, current, BUY_2ND_AMT)
            tracker["buy2_done"] = True
        elif (not buy2_done and stage == 0
                and buy2_rate <= BUY_2ND_THRESHOLD
                and not is_paused and not is_weak):
            reasons = []
            if not ma20_ok: reasons.append(f"MA20이탈({current:,.0f}<{ma20:,.0f})")
            if not mkt_ok:  reasons.append(f"시장{market_status}")
            if not vol_ok:  reasons.append(f"거래량부족({vol_ratio:.0f}%<{VOL_RATIO_MIN:.0f}%)")
            print(f"⛔ 2차매수 조건미달 {code}: {', '.join(reasons)}")

        # ----------------------------------------------------------
        # ② 3차 익절 (+25% 전량)
        # ----------------------------------------------------------
        if stage >= 2 and rate >= SELL_3RD_RATE:
            on_sell(code, qty, f"3차익절전량({rate:+.2%})", current)
            peak_tracker.pop(code, None)
            return "3차익절"

        # ----------------------------------------------------------
        # ③ MA20 이탈 (2차 익절 후)
        # ----------------------------------------------------------
        if stage >= 2 and ma20 > 0:
            if current < ma20:
                print(f"📉 MA20 이탈 {code} | 현재:{current:,.0f} < MA20:{ma20:,.0f}")
                on_sell(code, qty, f"MA20이탈({rate:+.2%})", current)
                peak_tracker.pop(code, None)
                return "MA20이탈"

        # ----------------------------------------------------------
        # ④ 트레일링 스탑
        # ----------------------------------------------------------
        if stage >= 2:
            if rate <= peak_rate - TRAIL_STOP_AFTER_2ND:
                on_sell(code, qty, f"트레일링2({rate:+.2%})", current)
                peak_tracker.pop(code, None)
                return "트레일링2"
        elif stage == 1:
            # ★ 1차 익절 후 트레일링
            if rate <= peak_rate - TRAIL_STOP_AFTER_1ST:
                on_sell(code, qty, f"트레일링1({rate:+.2%})", current)
                peak_tracker.pop(code, None)
                return "트레일링1"

        # ----------------------------------------------------------
        # ⑤ 2차 익절 (+15%)
        # ----------------------------------------------------------
        if stage < 2 and rate >= SELL_2ND_RATE:
            sell_qty = max(int(tracker["remain_qty"] * SELL_2ND_QTY / (1 - SELL_1ST_QTY)), 1)
            sell_qty = min(sell_qty, qty)
            on_sell(code, sell_qty, f"2차익절({rate:+.2%})", current)
            tracker["stage"] = 2
            realized_gain = (current - entry) * sell_qty
            tracker["effective_entry"] = max(
                entry - realized_gain / max(qty - sell_qty, 1),
                entry * 0.93,
            )
            return "2차익절"

        # ----------------------------------------------------------
        # ⑥ 1차 익절 (+8%)
        # ----------------------------------------------------------
        if stage < 1 and rate >= SELL_1ST_RATE:
            sell_qty = max(int(qty * SELL_1ST_QTY), 1)
            on_sell(code, sell_qty, f"1차익절({rate:+.2%})", current)
            tracker["stage"]      = 1
            tracker["remain_qty"] = qty - sell_qty
            realized_gain = (current - entry) * sell_qty
            tracker["effective_entry"] = max(
                entry - realized_gain / max(qty - sell_qty, 1),
                entry * 0.96,
            )
            return "1차익절"

        # ----------------------------------------------------------
        # ⑦ 손절 (★ 단계별 + ATR)
        # ----------------------------------------------------------
        if stage >= 1:
            stop_line = STOP_LOSS_AFTER_1ST  # 본절 보호
            label     = "본절보호"
        elif is_weak:
            stop_line = STOP_LOSS_WEAK
            label     = "손절(약세장)"
        else:
            stop_line = STOP_LOSS_BASIC
            label     = "손절"

        # ★ 스윙봇 ATR 보정 제거 — 고정 손절선 -7% 사용 (ATR로 인한 손절 무력화 방지)
        # if atr_rate > 0:
        #     atr_floor = max(-0.10, -atr_rate * 1.5)
        #     stop_line = min(stop_line, atr_floor)

        if rate <= stop_line:
            on_sell(code, qty, f"{label}({rate:+.2%})", current)
            on_loss()
            peak_tracker.pop(code, None)
            return label

        return None
