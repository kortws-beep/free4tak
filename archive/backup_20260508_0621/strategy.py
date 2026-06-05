"""
strategy.py — 매수/매도 전략 (룰 점수, 업종 보정, 매도 체크)
"""
import os
import time
import datetime
import requests

# ── 매도 전략 ───────────────────────────────────────────────
SELL_1ST_RATE   = 0.05
SELL_1ST_QTY    = 0.30
SELL_2ND_RATE   = 0.10
SELL_2ND_QTY    = 0.40
SELL_3RD_RATE   = 0.15
TRAIL_STOP      = 0.02
STOP_LOSS_BASIC = -0.05
STOP_LOSS_AFTER = -0.05
STOP_LOSS_WEAK  = -0.03
EOD_SELL_TIME   = "1515"

# ── 분할매수 ────────────────────────────────────────────────
BUY_2ND_AMT       = 100000
BUY_2ND_THRESHOLD = -0.02
BUY_2ND_WEAK_ONLY = True

# ── 업종/테마 가점 ───────────────────────────────────────────
SECTOR_BONUS = 10
THEME_BONUS  = 5
NEW_BONUS    = 7


class Strategy:

    # ============================================================
    # 룰 기반 점수
    # ============================================================
    def get_rule_score(self, data: dict) -> int:
        try:
            score       = 50
            change      = data.get("change_rate",   0)
            value       = data.get("trading_value", 0)
            vol_ratio   = data.get("volume_ratio",  0)
            vol_tnrt    = data.get("vol_tnrt",      0)
            rsi         = data.get("rsi",           50)
            ma5         = data.get("ma5",            0)
            ma20        = data.get("ma20",           0)
            ma60        = data.get("ma60",           0)
            foreign     = data.get("foreign_5d",     0)
            institution = data.get("institution_5d", 0)

            if   change > 5:  score += 25
            elif change > 3:  score += 15
            elif change > 1:  score += 5
            else:             score -= 10

            if   value > 300: score += 20
            elif value > 100: score += 10
            elif value < 30:  score -= 10

            if   vol_ratio > 300: score += 15
            elif vol_ratio > 200: score += 10
            elif vol_ratio > 120: score += 5
            elif vol_ratio < 50:  score -= 10

            if   vol_tnrt > 50: score += 10
            elif vol_tnrt > 20: score += 5

            if   45 < rsi < 65:  score += 10
            elif rsi > 75:       score -= 15
            elif rsi < 30:       score -= 5

            if   ma5 > ma20 > ma60 > 0: score += 15
            elif ma5 > ma20 > 0:        score += 7
            else:                       score -= 5

            if   foreign > 5000:  score += 10
            elif foreign > 1000:  score += 5
            elif foreign < -5000: score -= 5

            if   institution > 5000:  score += 10
            elif institution > 1000:  score += 5
            elif institution < -5000: score -= 5

            return max(0, min(100, score))
        except Exception as e:
            print(f"⚠️ 룰 점수 오류: {e}"); return 0

    # ============================================================
    # 업종/테마 점수 보정
    # ============================================================
    def apply_sector_bonus(self, code: str, score: int,
                           active_sectors: list,
                           sector_group_map: dict,
                           theme_codes: list,
                           new_codes_list: list) -> tuple:
        """반환: (보정 점수, 보정 이유, buy_tag)"""
        bonus   = 0
        reason  = ""
        buy_tag = ""

        for kw in active_sectors:
            if code in sector_group_map.get(kw, []):
                bonus   += SECTOR_BONUS
                reason  += f"|업종활성(+{SECTOR_BONUS})[{kw}]"
                buy_tag  = "theme_buy"
                break

        if code in theme_codes:
            bonus  += THEME_BONUS
            reason += f"|테마(+{THEME_BONUS})"
            if not buy_tag:
                buy_tag = "theme_buy"

        if code in new_codes_list:
            bonus  += NEW_BONUS
            reason += f"|신규추천(+{NEW_BONUS})"
            if not buy_tag:
                buy_tag = "theme_buy"

        if bonus == 0:
            return score, "", ""

        new_score = max(0, min(100, score + bonus))
        reason    = reason.lstrip("|")
        print(f"   🎯 업종/테마 보정 {code}: {score}→{new_score}점 | {reason}")
        return new_score, reason, buy_tag

    # ============================================================
    # 매도 체크
    # ============================================================
    def check_sell(self, code: str, pos: dict, now_t: str,
                   market_data, market_status: str,
                   peak_tracker: dict, buy_tags: dict,
                   is_paused: bool,
                   on_buy,            # callable(code, price, amount)
                   on_sell,           # callable(code, qty, reason, sell_price)
                   on_loss,           # callable() — 손절 카운트
                   ma10: float = 0):  # ★ 10일선 값
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
        peak_rate  = tracker["peak_rate"]
        buy2_done  = tracker.get("buy2_done", True)
        buy1_price = tracker.get("buy1_price", entry)

        if rate > peak_rate:
            tracker["peak_rate"] = rate

        # 2차 분할매수
        buy2_rate = (current - buy1_price) / buy1_price if buy1_price else 0
        _is_weak  = market_status in ("weak", "stop")
        _buy2_allow = (
            buy2_rate > 0
            or (buy2_rate < 0 and not (_is_weak and BUY_2ND_WEAK_ONLY))
        )
        if (not buy2_done and stage == 0
                and abs(buy2_rate) >= abs(BUY_2ND_THRESHOLD)
                and _buy2_allow and not is_paused):
            tag = "눌림목" if buy2_rate < 0 else "추격"
            print(f"➕ 2차 매수 시도 {code} | {buy2_rate:+.2%} {tag}")
            on_buy(code, current, BUY_2ND_AMT)
            tracker["buy2_done"] = True
        elif not buy2_done and buy2_rate < 0 and _is_weak and BUY_2ND_WEAK_ONLY:
            print(f"⚠️ 약세장 물타기 금지 {code} ({buy2_rate:+.2%})")

        # 종가 매도 (15:15)
        if now_t >= EOD_SELL_TIME:
            is_theme_buy = buy_tags.get(code) == "theme_buy"
            if is_theme_buy:
                if rate >= 0:
                    on_sell(code, qty, f"테마종가매도({rate:+.2%})", current)
                    peak_tracker.pop(code, None); return
            else:
                if stage >= 1:
                    on_sell(code, qty, f"종가매도({rate:+.2%})", current)
                    peak_tracker.pop(code, None); return
                elif stage == 0 and -0.01 <= rate <= 0.01:
                    on_sell(code, qty, f"종가매도횡보({rate:+.2%})", current)
                    peak_tracker.pop(code, None); return

        # 3차 익절 +15%
        if stage >= 2 and rate >= SELL_3RD_RATE:
            on_sell(code, qty, f"3차익절전량({rate:+.2%})", current)
            peak_tracker.pop(code, None); return

        # 트레일링 스탑
        if stage >= 2 and rate <= peak_rate - TRAIL_STOP:
            on_sell(code, qty, f"트레일링스탑({rate:+.2%})", current)
            peak_tracker.pop(code, None); return

        # 2차 익절 +10%
        if stage < 2 and rate >= SELL_2ND_RATE:
            sell_qty = max(int(tracker["remain_qty"] * SELL_2ND_QTY / (1 - SELL_1ST_QTY)), 1)
            sell_qty = min(sell_qty, qty)
            on_sell(code, sell_qty, f"2차익절({rate:+.2%})", current)
            tracker["stage"] = 2; return

        # 1차 익절 +5%
        if stage < 1 and rate >= SELL_1ST_RATE:
            sell_qty = max(int(qty * SELL_1ST_QTY), 1)
            on_sell(code, sell_qty, f"1차익절({rate:+.2%})", current)
            tracker["stage"]      = 1
            tracker["remain_qty"] = qty - sell_qty; return

        # 손절
        stop_line = STOP_LOSS_WEAK if _is_weak else (
            STOP_LOSS_AFTER if stage >= 1 else STOP_LOSS_BASIC
        )
        if rate <= stop_line:
            label = "손절(익절후)" if stage >= 1 else "손절"
            if _is_weak: label += "(약세장)"
            on_sell(code, qty, f"{label}({rate:+.2%})", current)
            on_loss()
            peak_tracker.pop(code, None)
            print(f"📉 손절 {code} | {rate:+.2%} | 기준:{stop_line:.0%}")
