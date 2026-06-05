"""
sbot_strategy.py — 스윙봇 전용 매수/매도 전략
"""

# ── 매도 전략 ───────────────────────────────────────────────
SELL_1ST_RATE   = 0.08   # 1차 익절 +8%
SELL_1ST_QTY    = 0.30
SELL_2ND_RATE   = 0.15   # 2차 익절 +15%
SELL_2ND_QTY    = 0.40
TRAIL_STOP      = 0.07   # 트레일링 스탑 -7% (MA20 폴백용)
STOP_LOSS_BASIC = -0.07
STOP_LOSS_WEAK  = -0.05

# ── 분할매수 ────────────────────────────────────────────────
BUY_2ND_AMT       = 1000000
BUY_2ND_THRESHOLD = -0.03   # -3% 물타기

# ── new 그룹 가점 ────────────────────────────────────────────
NEW_BONUS = 7


class SwingStrategy:

    # ============================================================
    # 룰 기반 점수 (스윙 특화)
    # ============================================================
    def get_rule_score(self, data: dict) -> int:
        try:
            score       = 50
            change      = data.get("change_rate",   0)
            value       = data.get("trading_value", 0)
            rsi         = data.get("rsi",           50)
            ma5         = data.get("ma5",            0)
            ma20        = data.get("ma20",           0)
            ma60        = data.get("ma60",           0)
            foreign     = data.get("foreign_5d",     0)
            institution = data.get("institution_5d", 0)

            if   change > 5:  score += 20
            elif change > 3:  score += 12
            elif change > 1:  score += 5
            else:             score -= 5

            if   value > 500: score += 20
            elif value > 200: score += 12
            elif value > 100: score += 5
            elif value < 50:  score -= 15

            if   ma5 > ma20 > ma60 > 0: score += 25
            elif ma5 > ma20 > 0:        score += 12
            else:                       score -= 10

            if   40 < rsi < 70:  score += 10
            elif rsi > 80:       score -= 20
            elif rsi < 30:       score -= 5

            if   foreign > 10000:  score += 15
            elif foreign > 5000:   score += 10
            elif foreign > 1000:   score += 5
            elif foreign < -5000:  score -= 10

            if   institution > 10000:  score += 15
            elif institution > 5000:   score += 10
            elif institution > 1000:   score += 5
            elif institution < -5000:  score -= 10

            return max(0, min(100, score))
        except Exception as e:
            print(f"⚠️ 룰 점수 오류: {e}"); return 0

    # ============================================================
    # new 그룹 가점
    # ============================================================
    def apply_new_bonus(self, code: str, score: int,
                        new_codes_list: list) -> tuple:
        """반환: (보정 점수, 보정 이유)"""
        if code not in new_codes_list:
            return score, ""
        new_score = min(100, score + NEW_BONUS)
        reason    = f"신규추천(+{NEW_BONUS})"
        print(f"   🆕 new 가점 {code}: {score}→{new_score}점")
        return new_score, reason

    # ============================================================
    # 매도 체크
    # ============================================================
    def check_sell(self, code: str, pos: dict,
                   market_data: dict, market_status: str,
                   peak_tracker: dict, is_paused: bool,
                   on_buy,            # callable(code, price, amount)
                   on_sell,           # callable(code, qty, reason, sell_price)
                   on_loss,           # callable()
                   ma20: float = 0):  # ★ 20일선 값
        data = market_data
        if not data: return

        current = float(data.get("stck_prpr", 0))
        entry   = pos["entry_price"]
        qty     = pos["qty"]
        if entry == 0 or current == 0 or qty <= 0: return

        rate = (current - entry) / entry

        if code not in peak_tracker:
            peak_tracker[code] = {
                "peak_rate":  rate, "stage": 0,
                "remain_qty": qty,  "buy2_done": True,
                "buy1_price": entry,
            }

        tracker    = peak_tracker[code]
        stage      = tracker["stage"]
        buy2_done  = tracker.get("buy2_done", True)
        buy1_price = tracker.get("buy1_price", entry)

        if rate > tracker["peak_rate"]:
            tracker["peak_rate"] = rate

        # 2차 분할매수 (물타기)
        _is_weak  = market_status in ("weak", "stop")
        buy2_rate = (current - buy1_price) / buy1_price if buy1_price else 0
        if (not buy2_done and stage == 0
                and buy2_rate <= BUY_2ND_THRESHOLD
                and not is_paused and not _is_weak):
            print(f"➕ 2차 매수(물타기) {code} | {buy2_rate:+.2%}")
            on_buy(code, current, BUY_2ND_AMT)
            tracker["buy2_done"] = True

        # ★ 2차 익절 후 — 20일선 이탈 매도 (MA20 없으면 트레일링 스탑 폴백)
        if stage >= 2:
            if ma20 > 0:
                if current < ma20:
                    print(f"📉 20일선 이탈 {code} | 현재:{current:,} < MA20:{ma20:,.0f}")
                    on_sell(code, qty, f"20일선이탈({rate:+.2%})", current)
                    peak_tracker.pop(code, None); return
                else:
                    print(f"  📊 MA20 유지 {code} | 현재:{current:,} > MA20:{ma20:,.0f}")
            else:
                if rate <= tracker["peak_rate"] - TRAIL_STOP:
                    print(f"📉 트레일링스탑(폴백) {code} | {rate:+.2%}")
                    on_sell(code, qty, f"트레일링스탑({rate:+.2%})", current)
                    peak_tracker.pop(code, None); return

        # 2차 익절 +15%
        if stage < 2 and rate >= SELL_2ND_RATE:
            sell_qty = max(int(tracker["remain_qty"] * SELL_2ND_QTY / (1 - SELL_1ST_QTY)), 1)
            sell_qty = min(sell_qty, qty)
            on_sell(code, sell_qty, f"2차익절({rate:+.2%})", current)
            tracker["stage"] = 2; return

        # 1차 익절 +8%
        if stage < 1 and rate >= SELL_1ST_RATE:
            sell_qty = max(int(qty * SELL_1ST_QTY), 1)
            on_sell(code, sell_qty, f"1차익절({rate:+.2%})", current)
            tracker["stage"]      = 1
            tracker["remain_qty"] = qty - sell_qty; return

        # 손절
        stop_line = STOP_LOSS_WEAK if _is_weak else STOP_LOSS_BASIC
        if rate <= stop_line:
            label = "손절(약세장)" if _is_weak else "손절"
            on_sell(code, qty, f"{label}({rate:+.2%})", current)
            on_loss()
            peak_tracker.pop(code, None)
