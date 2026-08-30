"""
sbot_strategy.py — 스윙봇 매수/매도 전략 (v3 — ATR 추세추종)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

스윙봇은 단타봇과 달리 며칠~수주 보유하는 추세추종 전략입니다.
- 매수 금액: 1종목당 시드의 일정 비율
- 손절/목표가: ATR(변동성) 기반 — 종목마다 다름
- 분할 매도 X — 전량 보유하며 목표가 상향

[v3 전략 — ATR 추세추종]
매수 시:
  손절가  = 매수가 - ATR × 2
  목표가1 = 매수가 + ATR × 3  (★ 2026-08-17: sbo2처럼 2.0 하향 검토했으나
    backtest_data.db 검증 결과 표본별로 최적값이 뒤바뀌어 3.0 유지로 결정)

목표가1 달성:
  손절가  = 매수가 + ATR × 1  (본전 위로 올림)
  목표가2 = 현재가 + ATR × 3  (새 목표 산정)

목표가2 이상:
  손절가  = 직전 목표가       (수익 보호)
  목표가  = 현재가 + ATR × 3 (계속 갱신)
  트레일링 = 고점 대비 ATR × 1.5 하락 시 청산

청산 조건 (2026-08-16 기준 — 실제 살아있는 조건만):
  ① 손절가 이탈 (손절)
  ② 트레일링 스탑 (목표가1 달성 이후, 고점 대비 ATR × 1.5 하락)
  ※ MA20 이탈 청산은 2026-06-23 제거(손절선보다 멀어 오히려 손절
     지연 부작용 — 삼화콘덴서 -21.3%까지 방치된 사례).
  ※ 보유기한(20영업일) 강제청산도 2026-07-06 제거(하락장에서 손실
     구간 포지션을 기간만료로 털어버리는 부작용 반복 — 사용자 결정).
     둘 다 ATR 손절/트레일링/목표가만으로 관리하는 현재 방식이 더
     낫다고 판단해 뺀 것으로, 코드 누락이 아님. 필요시 git 히스토리
     복원 가능.
================================================================
"""
from typing import Optional, Callable

# ==========================================================
# ATR 배수 설정
# ==========================================================
ATR_STOP_MULT    = 2.0    # 손절: 매수가 - ATR × 2
# ★ 2026-08-17: sbo2에서 목표1을 3.0→2.0으로 낮춰 개선된 걸 보고 sbot도
#   같은 조치를 검토했으나, backtest_data.db(2024-01~2026-08, 2.5년치)로
#   2.0/2.5/3.0 비교 백테스트 해보니 sbo2처럼 "낮출수록 좋아지는" 깔끔한
#   패턴이 아니라 종목 표본에 따라 최적값이 뒤바뀜(50종목 샘플=3.0이
#   최고 PF2.80, 전체종목=2.5가 최고지만 2.0은 오히려 PF0.91/MDD-13%로
#   최악) — 신뢰할 신호가 아니라고 판단해 **3.0 유지로 결정**(사용자
#   확인, 다음 주말 재검토 예정). 목표1/목표2+ 배수를 분리해둔 인프라만
#   남겨서 나중에 데이터 더 쌓이면 다시 테스트하기 쉽게 해둠.
ATR_TARGET1_MULT = 3.0    # 목표1(최초): 매수가 + ATR × 3 (2026-08-17 재검토, 유지 결정)
ATR_TARGET_MULT  = 3.0    # 목표2+(재상향): 현재가 + ATR × 3
TARGET1_CAP_RATE = 0.20   # ★ 목표가1 상한 +20% (ATR×3과 비교해 작은 값 사용)
ATR_RAISE_MULT   = 1.0    # 목표1 달성 후 손절 올림: 매수가 + ATR × 1
ATR_TRAIL_MULT   = 1.5    # 트레일링: 고점 - ATR × 1.5

# ATR 데이터 없을 때 폴백 고정 %
FALLBACK_STOP    = -0.07  # -7%
FALLBACK_TARGET  = 0.12   # +12%
FALLBACK_TRAIL   = 0.05   # 고점 대비 -5%

# ==========================================================
# 물타기 (2차 매수)
# ==========================================================
BUY_2ND_AMT       = 500_000   # 2차 매수 금액
BUY_2ND_THRESHOLD = -0.03     # -3% 하락 시

# ==========================================================
# 가산점
# ==========================================================
NEW_BONUS = 7


class SwingStrategy:
    """스윙봇 매수/매도 전략 (v3 — ATR 추세추종)."""

    # ============================================================
    # 1. 룰 점수 (스윙 특화 — 변경 없음)
    # ============================================================
    def get_rule_score(self, data: dict) -> int:
        try:
            score       = 30
            change      = data.get("change_rate",   0)
            value       = data.get("trading_value", 0)
            rsi         = data.get("rsi",           50)
            ma5         = data.get("ma5",            0)
            ma20        = data.get("ma20",           0)
            ma60        = data.get("ma60",           0)
            foreign     = data.get("foreign_5d",     0)
            institution = data.get("institution_5d", 0)

            if   change > 5:  score += 12
            elif change > 3:  score += 8
            elif change > 1:  score += 5
            else:             score -= 5

            if   value > 500: score += 10
            elif value > 200: score += 7
            elif value > 100: score += 3
            elif value < 50:  score -= 10

            if   ma5 > ma20 > ma60 > 0: score += 15
            elif ma5 > ma20 > 0:        score += 8
            else:                       score -= 8

            if   40 < rsi < 70:  score += 8
            elif rsi > 80:       score -= 15
            elif rsi < 30:       score -= 3

            if   foreign > 10000: score += 10
            elif foreign > 5000:  score += 7
            elif foreign > 1000:  score += 3
            elif foreign < -5000: score -= 8

            if   institution > 10000: score += 8
            elif institution > 5000:  score += 5
            elif institution > 1000:  score += 2
            elif institution < -5000: score -= 5

            return max(0, min(100, score))
        except Exception as e:
            print(f"⚠️ 스윙 룰 점수 오류: {e}")
            return 0

    # ============================================================
    # 2. 매수 필터 (변경 없음)
    # ============================================================
    def passes_buy_filter(self, data: dict, is_new: bool = False) -> tuple:
        change   = data.get("change_rate", 0)
        ma5      = data.get("ma5", 0)
        ma20     = data.get("ma20", 0)
        foreign  = data.get("foreign_5d", 0)

        vi_code = data.get("iscd_stat_cls_code", "55")
        if vi_code == "51":
            return False, "VI 발동 중 (단일가매매 — 체결 불가)"

        if change >= 29.5:
            return False, "상한가 제외"

        if change != 0:
            is_strong = (ma5 > ma20 > 0 and foreign > 5000) or is_new
            if is_strong:
                if change < -2:
                    return False, "약세종목(-2% 미만)"
            else:
                if change < 1.0:
                    return False, "양봉 미달(+1% 미만)"

        return True, ""

    # ============================================================
    # 3. new 그룹 가산점 (변경 없음)
    # ============================================================
    def apply_new_bonus(self, code: str, score: int,
                        new_codes_list: list) -> tuple:
        if not new_codes_list or code not in new_codes_list:
            return score, ""
        new_score = min(100, score + NEW_BONUS)
        reason    = f"신규추천(+{NEW_BONUS})"
        print(f"   🆕 new 가점 {code}: {score}→{new_score}점")
        return new_score, reason

    # ============================================================
    # 4. ATR 기반 초기 손절/목표가 계산 (매수 시 호출)
    # ============================================================
    def calc_atr_levels(self, entry: float, atr_rate: float) -> dict:
        """
        매수 시 ATR 기반 손절/목표가 계산
        반환: {stop_price, target1, atr_val}
        """
        if atr_rate > 0:
            atr_val  = entry * atr_rate
            stop     = round(entry - atr_val * ATR_STOP_MULT, 0)
            # ★ 목표가1 상한 캡 (2026-06-23) — ATR×3과 +20% 중 작은 값
            #   고변동성 종목(예: 테크윙)은 ATR×3이 +50~80%까지 치솟아
            #   1차 목표가 사실상 도달불가능해지는 문제 방지
            target1_atr = entry + atr_val * ATR_TARGET1_MULT
            target1_cap = entry * (1 + TARGET1_CAP_RATE)
            target1     = round(min(target1_atr, target1_cap), 0)
        else:
            # ATR 없을 때 폴백
            atr_val  = entry * abs(FALLBACK_STOP) / ATR_STOP_MULT
            stop     = round(entry * (1 + FALLBACK_STOP), 0)
            target1  = round(entry * (1 + FALLBACK_TARGET), 0)

        print(f"   📐 ATR 타점 | 손절:{stop:,.0f} | 목표1:{target1:,.0f} "
              f"| ATR:{atr_rate:.2%}")
        return {"stop_price": stop, "target1": target1, "atr_val": atr_val}

    # ============================================================
    # 5. 매도 체크 (v3 — ATR 추세추종)
    # ============================================================
    def check_sell(self, code: str, pos: dict,
                   market_data: dict, market_status: str,
                   peak_tracker: dict, is_paused: bool,
                   on_buy: Callable, on_sell: Callable, on_loss: Callable,
                   ma20: float = 0,
                   atr_rate: float = 0,
                   vol_ratio: float = 0.0,
                   now_t: str = '1200') -> Optional[str]:
        """
        ATR 추세추종 매도 의사결정.
        - 분할 매도 없음 — 전량 보유하며 목표가/손절가 상향
        - 트레일링은 목표가1 달성 이후부터 작동
        """
        if not market_data:
            return None

        current = float(market_data.get("stck_prpr", 0))
        entry   = pos["entry_price"]
        qty     = pos["qty"]
        if entry == 0 or current == 0 or qty <= 0:
            return None

        rate = (current - entry) / entry

        # ── tracker 초기화 ────────────────────────────────────
        if code not in peak_tracker:
            # 매수 시 ATR 레벨 계산
            levels   = self.calc_atr_levels(entry, atr_rate)
            atr_val  = levels["atr_val"]
            import datetime as _dt
            peak_tracker[code] = {
                "peak_rate":   rate,
                "peak_price":  current,
                "stage":       0,           # 0=초기, 1=목표1달성, 2=목표2이상
                "buy2_done":   True,
                "buy1_price":  entry,
                "stop_price":  levels["stop_price"],
                "target1":     levels["target1"],
                "target_next": levels["target1"],
                "atr_val":     atr_val,
                "buy_date":    _dt.date.today().isoformat(),  # ★ 25일 기한 추적용
            }

        tracker     = peak_tracker[code]

        # ★ 2026-08-17: 평단가 변동 시 손절/목표 재계산 — 2차매수(물타기,
        #   아래 ①)든 수동 추가매수든 entry_price가 바뀌면 손절/목표가
        #   원래 진입가 기준으로 고정된 채 남아있던 문제(sbo2의 "실계좌"
        #   평단변경 재계산 기능과 동일 취지, 08-07 sbo2에 먼저 적용됐던
        #   걸 사용자 요청으로 sbot에도 포팅). stage==0(목표1 미달성)일
        #   때만 적용 — stage>=1은 이미 트레일링/재상향으로 진행 중인
        #   보호 손절을 되돌리면 안 되므로 건드리지 않음.
        if tracker["stage"] == 0:
            last_entry = tracker.get("last_entry", entry)
            if abs(entry - last_entry) > 1:
                levels = self.calc_atr_levels(entry, atr_rate)
                tracker["stop_price"]  = levels["stop_price"]
                tracker["target1"]     = levels["target1"]
                tracker["target_next"] = levels["target1"]
                tracker["atr_val"]     = levels["atr_val"]
                tracker["buy1_price"]  = entry
                print(f"   🔄 {code} 평단 변경({last_entry:,.0f}→{entry:,.0f}) — "
                      f"손절/목표 재계산(손절:{levels['stop_price']:,.0f} "
                      f"목표:{levels['target1']:,.0f})")
        tracker["last_entry"] = entry

        stage       = tracker["stage"]
        stop_price  = tracker["stop_price"]
        target1     = tracker["target1"]
        target_next = tracker["target_next"]
        atr_val     = tracker.get("atr_val", entry * abs(FALLBACK_STOP) / ATR_STOP_MULT)
        buy2_done   = tracker.get("buy2_done", True)
        buy1_price  = tracker.get("buy1_price", entry)

        # 고점 갱신
        if rate > tracker["peak_rate"]:
            tracker["peak_rate"]  = rate
            tracker["peak_price"] = current

        peak_price = tracker["peak_price"]

        # ----------------------------------------------------------
        # ① 물타기 (stage=0, -3% 하락, MA20 위, 거래량 충분)
        # ----------------------------------------------------------
        is_weak = market_status in ("weak", "stop")
        buy2_rate = (current - buy1_price) / buy1_price if buy1_price else 0
        ma20_ok   = (ma20 > 0 and current >= ma20)
        mkt_ok    = (market_status == "normal")
        VOL_RATIO_MIN = 150.0
        vol_ok    = (vol_ratio <= 0) or (vol_ratio >= VOL_RATIO_MIN)

        if (not buy2_done and stage == 0
                and buy2_rate <= BUY_2ND_THRESHOLD
                and not is_paused and not is_weak
                and ma20_ok and mkt_ok and vol_ok):
            print(f"➕ 2차 매수(물타기) {code} | {buy2_rate:+.2%}")
            on_buy(code, current, BUY_2ND_AMT)
            tracker["buy2_done"] = True

        # ----------------------------------------------------------
        # ② MA20 이탈 체크 — 제거함 (2026-06-23)
        #   ATR 기반 손절/트레일링/목표가로 충분히 추세 관리되며,
        #   MA20이 손절선보다 멀리 있어 오히려 손절이 늦어지는 부작용
        #   발생(삼화콘덴서 -21.3%까지 방치). sbo2와 동일한 이유로 비활성화.
        #   (필요시 git 히스토리에서 복원 가능)

        # ----------------------------------------------------------
        # ③ 손절가 이탈 — ★ 2026-08-30: "홀드" 설정된 종목은 이 체크만
        #   건너뜀(트레일링/목표달성 로직은 그대로 적용). KiKi "!h 종목"
        #   명령으로 설정, bots/sbot.py의 pending_cmd "hold" 처리 참고.
        # ----------------------------------------------------------
        if current <= stop_price and not tracker.get("hold", False):
            label = "손절" if stage == 0 else f"손절(stage{stage})"
            print(f"🛑 {label} {code} | 현재:{current:,.0f} ≤ 손절:{stop_price:,.0f} ({rate:+.2%})")
            on_sell(code, qty, f"{label}({rate:+.2%})", current)
            if stage == 0:
                on_loss()
            peak_tracker.pop(code, None)
            return label

        # ★ 2026-07-06: 보유기한(25일) 강제청산 로직 제거 — ATR 손절/트레일링/
        #   목표가만으로 관리 (사용자 결정, 최근 장세에서 기간매도가 손실
        #   구간에서 포지션을 털어버리는 부작용이 반복됨). bots/sbot.py의
        #   11/20영업일 룰도 같은 이유로 함께 제거함.

        # ----------------------------------------------------------
        # ④ 트레일링 스탑 (목표가1 달성 이후)
        # ----------------------------------------------------------
        if stage >= 1 and atr_val > 0:
            trail_stop = peak_price - atr_val * ATR_TRAIL_MULT
            if current <= trail_stop:
                print(f"🔻 트레일링 {code} | 고점:{peak_price:,.0f} → "
                      f"트레일:{trail_stop:,.0f} | 현재:{current:,.0f} ({rate:+.2%})")
                on_sell(code, qty, f"트레일링({rate:+.2%})", current)
                peak_tracker.pop(code, None)
                return "트레일링"
        elif stage >= 1:
            # ATR 없을 때 폴백 트레일링
            trail_rate = tracker["peak_rate"] - FALLBACK_TRAIL
            if rate <= trail_rate:
                on_sell(code, qty, f"트레일링({rate:+.2%})", current)
                peak_tracker.pop(code, None)
                return "트레일링"

        # ----------------------------------------------------------
        # ⑤ 목표가 달성 → 손절/목표가 상향
        # ----------------------------------------------------------
        if current >= target_next:
            if stage == 0:
                # ★ 목표가1 달성 → 50% 매도(수익실현) + 손절을 매수가+ATR×1로 올림
                sell_qty = qty if qty <= 1 else qty // 2
                if sell_qty > 0:
                    on_sell(code, sell_qty, f"목표1익절50%({rate:+.2%})", current)
                new_stop   = round(entry + atr_val * ATR_RAISE_MULT, 0)
                new_target = round(current + atr_val * ATR_TARGET_MULT, 0)
                tracker["stop_price"]  = new_stop
                tracker["target_next"] = new_target
                tracker["stage"]       = 1
                tracker["half_sold"]   = True
                print(f"🎯 목표가1 달성 {code} ({rate:+.2%}) | 50%매도:{sell_qty}주 | "
                      f"손절 상향:{new_stop:,.0f} | 새목표:{new_target:,.0f}")
            else:
                # 목표가2+ 달성 → 손절을 직전 목표가로 올림
                new_stop   = target_next   # 직전 목표가가 새 손절
                new_target = round(current + atr_val * ATR_TARGET_MULT, 0)
                tracker["stop_price"]  = new_stop
                tracker["target_next"] = new_target
                tracker["stage"]       = stage + 1
                print(f"🎯 목표가{stage+1} 달성 {code} ({rate:+.2%}) | "
                      f"손절 상향:{new_stop:,.0f} | 새목표:{new_target:,.0f}")

        return None
