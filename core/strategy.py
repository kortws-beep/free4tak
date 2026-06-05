"""
strategy.py — 단타봇 매수/매도 전략 (개선판)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

종목을 살지 말지 점수로 판단하고, 산 종목을 언제 팔지 결정하는 두뇌입니다.

▶ 매수 결정: 룰 점수(50점 시작 → 가감) + AI 점수 → 일정 점수 이상이면 매수
▶ 매도 결정: 수익률에 따라 단계별로 분할 매도
   - 1차: +5% 도달 → 30% 매도 (수익 확정)
   - 2차: +10% 도달 → 40% 매도 (잔량 30% 유지)
   - 3차: +15% 도달 → 전량 매도
   - 트레일링: 고점 대비 일정 % 떨어지면 매도

[주요 개선 사항]
1. ★ 본절(Break-even) 보호 — 1차 익절 후 가격이 본전 근처로 떨어지면 즉시 청산
2. ★ 분할익절 후 effective_entry 보정 — 정확한 손익 계산
3. ★ 트레일링 스탑을 stage>=1부터 적용 (기존: stage>=2부터)
4. ★ 종가매도 범위 확대 (-3%까지) — 다음날 갭하락 위험 차단
5. ★ ATR 기반 동적 손절선 (변동성 큰 종목은 손절선도 넓게)
6. ★ 약세장에서도 강세 업종은 매수 허용
================================================================
"""
import os
import time
import datetime
from typing import Optional, Callable


# ==========================================================
# 매도 단계별 기준 (수익률 / 매도 비율)
# ==========================================================
SELL_1ST_RATE   = 0.03    # 1차 익절: +3% (★ 백테스트 최적)
SELL_1ST_QTY    = 0.30    # → 30% 매도
SELL_2ND_RATE   = 0.06    # 2차 익절: +6% (★ 백테스트 최적)
SELL_2ND_QTY    = 0.40    # → 40% 매도 (잔량의)
SELL_3RD_RATE   = 0.10    # 3차 익절: +10% (★ 백테스트 최적)

# 트레일링 스탑 (고점 대비 -2% 떨어지면 매도)
TRAIL_STOP_AFTER_1ST = 0.025  # 1차 후: 더 보수적 (2.5%)
TRAIL_STOP_AFTER_2ND = 0.02   # 2차 후: 2%

# 손절 기준
STOP_LOSS_BASIC      = -0.10  # 기본 손절: -10% (★ 백테스트 최적)
STOP_LOSS_AFTER_1ST  = -0.02  # ★ 1차 익절 후 본절 보호: 진입가 대비 -2%
STOP_LOSS_WEAK       = -0.03  # 약세장 손절: -3% (더 빠르게)

def get_dynamic_sell_rates(market_status: str, market_rate: float = 0.0) -> tuple:
    """시장 상황에 따른 동적 익절/손절 비율 반환"""
    # ★ 손절선은 시장 상황과 무관하게 -5% 고정
    # 약세/stop 모드에서 손절 축소 시 당일 저점 후 반등 전에 손절당하는 문제
    if market_status == "stop" or market_rate <= -2.5:
        return (0.02, 0.05, 0.08, STOP_LOSS_BASIC)   # 급락장 — 익절 보수적, 손절 유지
    elif market_status == "weak" or market_rate <= -1.0:
        return (0.03, 0.07, 0.10, STOP_LOSS_BASIC)   # 약세장 — 익절 보수적, 손절 유지
    elif market_rate >= 1.5:
        return (0.07, 0.13, 0.18, -0.06)   # 강세장
    elif market_rate >= 0.5:
        return (0.06, 0.11, 0.16, -0.05)   # 소폭 강세
    else:
        return (SELL_1ST_RATE, SELL_2ND_RATE, SELL_3RD_RATE, STOP_LOSS_BASIC)


# 종가 매도 시간
EOD_SELL_TIME        = "1515"

# ==========================================================
# 분할매수
# ==========================================================
BUY_2ND_AMT          = 100000
BUY_2ND_THRESHOLD    = -0.02     # -2% 하락 시 2차 매수 (눌림목)
BUY_2ND_WEAK_ONLY    = True      # 약세장에선 물타기 금지

# ==========================================================
# 업종/테마 가산점
# ==========================================================
SECTOR_BONUS = 10   # 강세 업종 종목
THEME_BONUS  = 5    # 테마 종목
NEW_BONUS    = 7    # 신규 추천 종목
COND_BONUS   = 8    # ★ 키움 조건검색식(단타/090930) 출처 종목


class Strategy:
    """단타봇 매수/매도 전략."""

    # ============================================================
    # 1. 룰 기반 점수 (AI 호출 전 1차 필터)
    # ============================================================
    def get_rule_score(self, data: dict) -> int:
        """
        ★ v2 — MACD / 볼린저밴드 / 스토캐스틱 / 캔들패턴 추가
        50점에서 시작 → 좋은 신호 +, 나쁜 신호 -

        [기존]
        - 등락률, 거래대금, 거래량, MA, RSI, 외국인/기관

        [★ 신규]
        - MACD 히스토그램: 골든크로스/데드크로스
        - 볼린저밴드: 하단 돌파(매수기회), 상단 근접(과열)
        - 스토캐스틱: 과매도 반등 신호
        - 캔들 패턴: 망치형(매수) / 역망치형(매도)
        - 외국인/기관 가중치 강화
        """
        try:
            score       = 30  # ★ 기본 30점 (지표로 +70까지)
            change      = data.get("change_rate",    0)
            value       = data.get("trading_value",  0)
            vol_ratio   = data.get("volume_ratio",   0)
            vol_tnrt    = data.get("vol_tnrt",       0)
            rsi         = data.get("rsi",            50)
            ma5         = data.get("ma5",             0)
            ma20        = data.get("ma20",            0)
            ma60        = data.get("ma60",            0)
            foreign     = data.get("foreign_5d",      0)
            institution = data.get("institution_5d",  0)
            # ★ 당일 실시간 수급
            foreign_today  = data.get("foreign_today",  0)
            orgn_today     = data.get("orgn_today",     0)
            prsn_today     = data.get("prsn_today",     0)   # 개인 역지표
            foreign_ratio  = data.get("foreign_ratio",  50)  # 외국인 매수비율
            orgn_ratio     = data.get("orgn_ratio",     50)  # 기관 매수비율
            buy_pressure   = data.get("buy_pressure",   0)   # 매수압력 지수
            # ★ 신규 지표
            macd_hist   = data.get("macd_hist",       0)
            bb_pct      = data.get("bb_pct",          0.5)
            bb_width    = data.get("bb_width",        0)
            stoch_k     = data.get("stoch_k",         50)
            candle_pat  = data.get("candle_pattern",  0)

            # ── 등락률 (+0~15) ───────────────────────────────
            if   change > 5:  score += 15
            elif change > 3:  score += 10
            elif change > 1:  score += 5
            else:             score -= 5

            # ── 거래대금 (+0~10) ─────────────────────────────
            if   value > 300: score += 10
            elif value > 100: score += 5
            elif value < 30:  score -= 5

            # ── 거래량 증가율 (+0~8) ─────────────────────────
            if   vol_ratio > 300: score += 8
            elif vol_ratio > 200: score += 5
            elif vol_ratio > 120: score += 2
            elif vol_ratio < 50:  score -= 5

            # ── 거래량 회전율 (+0~5) ─────────────────────────
            if   vol_tnrt > 50: score += 5
            elif vol_tnrt > 20: score += 2

            # ── RSI (+0~3) ───────────────────────────────────
            # attribution: 활성 시 -2.87%p 역효과 → +8 에서 +3 으로 축소
            if   45 < rsi < 65:  score += 3
            elif rsi > 75:       score -= 10  # 과매수 경고는 유지
            elif rsi < 30:       score -= 3

            # ── 이동평균선 정배열 (+0~8) ─────────────────────
            if   ma5 > ma20 > ma60 > 0: score += 8
            elif ma5 > ma20 > 0:        score += 4
            else:                       score -= 3

            # ── 수급 외국인 5일 누적 (+0~8) ─────────────────
            if   foreign > 10000:  score += 8
            elif foreign > 5000:   score += 5
            elif foreign > 1000:   score += 3
            elif foreign < -10000: score -= 6
            elif foreign < -5000:  score -= 3

            # ── 수급 기관 5일 누적 (+0~6) ────────────────────
            if   institution > 10000:  score += 6
            elif institution > 5000:   score += 4
            elif institution > 1000:   score += 2
            elif institution < -10000: score -= 4
            elif institution < -5000:  score -= 2

            # ── ★ 당일 실시간 외국인 순매수 (+0~8) ───────────
            if   foreign_today > 5000:   score += 8   # 강한 당일 매수
            elif foreign_today > 2000:   score += 5
            elif foreign_today > 500:    score += 3
            elif foreign_today < -5000:  score -= 8   # 강한 당일 매도
            elif foreign_today < -2000:  score -= 5

            # ── ★ 당일 실시간 기관 순매수 (+0~6) ────────────
            if   orgn_today > 3000:   score += 6
            elif orgn_today > 1000:   score += 4
            elif orgn_today > 300:    score += 2
            elif orgn_today < -3000:  score -= 5
            elif orgn_today < -1000:  score -= 3

            # ── ★ 외국인 매수비율 (당일 체결) (+0~5) ─────────
            # 50% 기준 → 높을수록 외국인이 더 많이 사는 것
            if   foreign_ratio > 60:  score += 5   # 외국인 매수 우세
            elif foreign_ratio > 55:  score += 3
            elif foreign_ratio < 40:  score -= 5   # 외국인 매도 우세
            elif foreign_ratio < 45:  score -= 3

            # ── ★ 개인 역지표 (+0~4) ─────────────────────────
            # 개인이 팔면 기관/외국인이 삼 → 매수 신호
            if   prsn_today < -2000:  score += 4   # 개인 강한 매도 → 역매수
            elif prsn_today < -500:   score += 2
            elif prsn_today > 5000:   score -= 3   # 개인 강한 매수 → 주의

            # ── ★ 매수 압력 지수 (+0~5) ──────────────────────
            if   buy_pressure > 5:    score += 5   # 강한 매수압력
            elif buy_pressure > 2:    score += 3
            elif buy_pressure < -5:   score -= 5
            elif buy_pressure < -2:   score -= 3

            # ── ★ MACD 히스토그램 (+0~5) ─────────────────────
            if   macd_hist > 0:  score += 5    # 상승 모멘텀
            elif macd_hist < 0:  score -= 4    # 하락 모멘텀

            # ── ★ 볼린저밴드 (+0~7) ──────────────────────────
            if   bb_pct < 0.2:        score += 7   # 하단 반등 기회
            elif bb_pct > 0.85:       score -= 7   # 상단 과열
            elif 0.3 < bb_pct < 0.7:  score += 2   # 안정 구간
            if 0 < bb_width < 0.05:   score += 3   # 밴드 좁음 = 폭발 임박

            # ── ★ 스토캐스틱 (+0~3) ──────────────────────────
            # attribution 꾸준히 -0.4~-0.8%p → +6/-6 에서 +3/-3 하향
            if   stoch_k < 20:        score += 3   # 과매도 반등
            elif stoch_k > 80:        score -= 3   # 과매수 주의
            elif 40 < stoch_k < 60:   score += 1   # 중립

            # ── ★ 캔들 패턴 (+0~4) ───────────────────────────
            if   candle_pat == 1:  score += 4   # 망치형
            elif candle_pat == -1: score -= 4   # 역망치형

            # ── ★ 호가잔량 비율 (매도/매수) — 눌린 스프링 포착 ──
            # 매도잔량 >> 매수잔량 = 가격 눌려있음 = 소화 시 급등 타점
            ask_rsqn = data.get("total_ask_rsqn", 0)
            bid_rsqn = data.get("total_bid_rsqn", 0)
            if bid_rsqn > 0:
                hoga_ratio = ask_rsqn / bid_rsqn
                if   hoga_ratio >= 5.0: score += 8   # 극단적 눌림 → 대반전 임박
                elif hoga_ratio >= 3.0: score += 5   # ★ 핵심 타점
                elif hoga_ratio >= 2.0: score += 3   # 약한 신호
                elif hoga_ratio <= 0.3: score -= 10  # 매수잔량 과다 → 천장 가능성
                elif hoga_ratio <= 0.5: score -= 5   # 약한 천장 신호

            return max(0, min(100, score))
        except Exception as e:
            print(f"⚠️ 룰 점수 오류: {e}")
            return 0

    # ============================================================
    # 2. 매수 필터 (양봉 조건 등)
    # ============================================================
    def passes_buy_filter(self, data: dict, is_sector_match: bool = False) -> tuple:
        """
        ★ 개선: 강한 추세 + 수급 우호 종목은 양봉 조건 면제
        반환: (통과 여부, 탈락 사유)
        """
        change   = data.get("change_rate", 0)
        ma5      = data.get("ma5",  0)
        ma20     = data.get("ma20", 0)
        foreign  = data.get("foreign_5d", 0)

        # 상한가/과열 제외
        if change >= 29.5:
            return False, "상한가 제외"
        if change > 15:
            return False, "과열 제외"

        # ★ 강한 종목은 음봉/약양봉도 허용 (눌림목 매수 기회 확보)
        is_strong = (
            ma5 > ma20 > 0           # 단기 정배열
            and foreign > 1000        # 외국인 순매수
            and is_sector_match       # 강세 업종 + 테마
        )
        if is_strong:
            # 강한 종목은 -2% ~ +30% 모두 허용
            if change < -2:
                return False, "약세종목(-2% 미만)"
        else:
            # 일반 종목은 +1% 이상 양봉만
            if change < 1.0:
                return False, "양봉 미달(+1% 미만)"
            
        return True, ""

    # ============================================================
    # 3. 업종/테마 가산점
    # ============================================================
    def apply_sector_bonus(self, code: str, score: int,
                           active_sectors: list,
                           sector_group_map: dict,
                           theme_codes: list,
                           new_codes_list: list,
                           cond_codes: set = None) -> tuple:
        """업종/테마/조건검색 매칭 시 점수 가산. 반환: (보정 점수, 사유, buy_tag)"""
        bonus   = 0
        reasons = []
        buy_tag = ""

        for kw in active_sectors:
            if code in sector_group_map.get(kw, []):
                bonus += SECTOR_BONUS
                reasons.append(f"업종활성(+{SECTOR_BONUS})[{kw}]")
                buy_tag = "theme_buy"
                break

        if code in theme_codes:
            bonus += THEME_BONUS
            reasons.append(f"테마(+{THEME_BONUS})")
            if not buy_tag:
                buy_tag = "theme_buy"

        if code in new_codes_list:
            bonus += NEW_BONUS
            reasons.append(f"신규추천(+{NEW_BONUS})")
            if not buy_tag:
                buy_tag = "theme_buy"

        # ★ 조건검색식(단타/090930) 출처 종목 가점
        if cond_codes and code in cond_codes:
            bonus += COND_BONUS
            reasons.append(f"조건검색(+{COND_BONUS})")
            if not buy_tag:
                buy_tag = "theme_buy"  # 우선 매수 대상

        if bonus == 0:
            return score, "", ""

        new_score = max(0, min(100, score + bonus))
        reason    = "|".join(reasons)
        print(f"   🎯 가점 {code}: {score}→{new_score}점 | {reason}")
        return new_score, reason, buy_tag

    # ============================================================
    # 4. 매도 체크 (★ 핵심 개선부)
    # ============================================================
    def check_sell(self, code: str, pos: dict, now_t: str,
                   market_data: dict, market_status: str,
                   peak_tracker: dict, buy_tags: dict,
                   is_paused: bool,
                   on_buy: Callable,
                   on_sell: Callable,
                   on_loss: Callable,
                   market_rate: float = 0.0,
                   ma10: float = 0,
                   atr_rate: float = 0) -> Optional[str]:
        """
        매도 의사결정 함수.

        매개변수:
        - pos: {entry_price, qty} — 한투 API에서 받은 보유 정보
        - now_t: 현재 시각 'HHMM'
        - market_data: 한투 시세 응답 (stck_prpr=현재가)
        - market_status: 'normal' / 'weak' / 'stop'
        - peak_tracker: 종목별 고점/단계 추적 dict (이 함수가 갱신함)
        - buy_tags: 종목별 매수 태그 (theme_buy 등)
        - is_paused: 봇 일시중단 여부
        - on_buy/on_sell/on_loss: 콜백 함수
        - ma10: 10일 이동평균 (트레일링 보조용)
        - atr_rate: ATR/현재가 비율 (변동성 — 손절선 동적 조정용)

        반환: 매도 사유 문자열 또는 None
        """
        if not market_data:
            return None

        # 시장 상황에 따른 동적 익절/손절 비율
        _s1, _s2, _s3, _sl = get_dynamic_sell_rates(market_status, market_rate)
        current = float(market_data.get("stck_prpr", 0))
        entry   = pos["entry_price"]
        qty     = pos["qty"]
        if entry == 0 or current == 0 or qty <= 0:
            return None

        rate = (current - entry) / entry  # 진입가 대비 수익률

        # 트래커 초기화 (재시작 후 처음 보는 종목)
        if code not in peak_tracker:
            peak_tracker[code] = {
                "peak_rate":       rate,
                "stage":           0,
                "remain_qty":      qty,
                "buy2_done":       True,   # 재시작 시 2차 매수 안함
                "buy1_price":      entry,
                "effective_entry": entry,  # ★ 분할 익절 후 실효 진입가
            }

        tracker         = peak_tracker[code]
        stage           = tracker["stage"]
        peak_rate       = tracker["peak_rate"]
        buy2_done       = tracker.get("buy2_done", True)
        buy1_price      = tracker.get("buy1_price", entry)
        effective_entry = tracker.get("effective_entry", entry)

        # 고점 갱신
        if rate > peak_rate:
            tracker["peak_rate"] = rate
            peak_rate            = rate

        # ----------------------------------------------------------
        # ① 2차 분할매수 (눌림목 / 추격)
        # ----------------------------------------------------------
        buy2_rate = (current - buy1_price) / buy1_price if buy1_price else 0
        is_weak   = market_status in ("weak", "stop")

        # ★ 2차 매수는 눌림목(-3% 이하)만 허용 (추격매수 금지)
        buy2_allowed = (
            buy2_rate <= BUY_2ND_THRESHOLD
            and not (is_weak and BUY_2ND_WEAK_ONLY)
        )
        if (not buy2_done and stage == 0
                and buy2_allowed and not is_paused):
            print(f"➕ 2차 매수(눌림목) {code} | {buy2_rate:+.2%}")
            on_buy(code, current, BUY_2ND_AMT)
            tracker["buy2_done"] = True
        elif not buy2_done and buy2_rate < 0 and is_weak and BUY_2ND_WEAK_ONLY:
            print(f"⚠️ 약세장 물타기 금지 {code} ({buy2_rate:+.2%})")

        # ----------------------------------------------------------
        # ② 종가 매도 (15:15 이후)
        # [규칙]
        # - stage >= 1 (1차 익절 완료) → 무조건 전량 청산 (당일 수익 확정)
        # - 손실 -3% 이하 → 전량 종가손절 (오버나이트 손실 확대 방지)
        # - +3% 이상 수익 → 홀딩 (강한 모멘텀 종목 다음날 기대)
        # - 그 외 (-3% ~ +3%) → 홀딩
        # ----------------------------------------------------------
        if now_t >= EOD_SELL_TIME:
            # 15:10 이후 매수(종가매수)는 당일 종가매도 제외
            buy_time_str = pos.get("buy_time", "") or pos.get("buy_date", "")
            is_eod_buy = False
            try:
                import datetime as _dt
                _bt = _dt.datetime.fromisoformat(buy_time_str) if "T" in buy_time_str \
                    else _dt.datetime.strptime(buy_time_str[:16], "%Y-%m-%d %H:%M")
                is_eod_buy = (_bt.strftime("%H%M") >= "1510")
            except Exception:
                pass

            if is_eod_buy:
                pass  # 당일 종가매수 → 종가매도 제외

            elif stage >= 1:
                # ★ 1차 익절 완료 → 무조건 전량 청산 (당일 수익 확정)
                on_sell(code, qty, f"종가매도-익절후({rate:+.2%})", current)
                peak_tracker.pop(code, None)
                return "종가매도-익절후"

            elif rate <= -0.03:
                # ★ -3% 이하 손실 → 오버나이트 손절 (다음날 추가 하락 방지)
                on_sell(code, qty, f"종가손절({rate:+.2%})", current)
                on_loss()
                peak_tracker.pop(code, None)
                print(f"🌙 종가손절 {code} | {rate:+.2%} (오버나이트 방지)")
                return "종가손절"

            elif rate >= 0.03:
                # ★ +3% 이상 수익 → 홀딩 (모멘텀 유지 기대)
                print(f"🌙 종가홀딩 {code} | {rate:+.2%} (모멘텀 홀딩)")

            else:
                # -3% ~ +3% → 홀딩 (다음날 판단)
                print(f"🌙 종가홀딩 {code} | {rate:+.2%} (관망)")

        # ----------------------------------------------------------
        # ③ 3차 익절 (+15% 전량)
        # ----------------------------------------------------------
        if stage >= 2 and rate >= _s3:
            on_sell(code, qty, f"3차익절전량({rate:+.2%})", current)
            peak_tracker.pop(code, None)
            return "3차익절"

        # ----------------------------------------------------------
        # ④ 트레일링 스탑 — ★ 개선: stage>=1부터 작동
        # ----------------------------------------------------------
        if stage >= 2:
            # 2차 익절 후: 고점 대비 -2% 떨어지면 매도
            if rate <= peak_rate - TRAIL_STOP_AFTER_2ND:
                on_sell(code, qty, f"트레일링스탑2({rate:+.2%})", current)
                peak_tracker.pop(code, None)
                return "트레일링2"
            # MA10 이탈 시에도 매도 (보조 트레일링)
            if ma10 > 0 and current < ma10 and rate >= 0.05:
                on_sell(code, qty, f"MA10이탈({rate:+.2%})", current)
                peak_tracker.pop(code, None)
                return "MA10이탈"
        elif stage == 1:
            # ★ 1차 익절 후: 고점 대비 -2.5% 떨어지면 매도 (보호 강화)
            if rate <= peak_rate - TRAIL_STOP_AFTER_1ST:
                on_sell(code, qty, f"트레일링스탑1({rate:+.2%})", current)
                peak_tracker.pop(code, None)
                return "트레일링1"

        # ----------------------------------------------------------
        # ⑤ 2차 익절 (+10%, 잔량의 40%)
        # ----------------------------------------------------------
        if stage < 2 and rate >= _s2:
            sell_qty = max(int(tracker["remain_qty"] * SELL_2ND_QTY / (1 - SELL_1ST_QTY)), 1)
            sell_qty = min(sell_qty, qty)
            on_sell(code, sell_qty, f"2차익절({rate:+.2%})", current)
            tracker["stage"] = 2
            # ★ 실효 진입가 보정: 일부 수익을 확정했으므로 잔량의 실효 단가는 더 낮음
            realized_gain = (current - entry) * sell_qty
            tracker["effective_entry"] = max(
                entry - realized_gain / max(qty - sell_qty, 1),
                entry * 0.95,  # 너무 낮아지지 않게 안전선
            )
            return "2차익절"

        # ----------------------------------------------------------
        # ⑥ 1차 익절 (+5%, 30% 매도)
        # ----------------------------------------------------------
        if stage < 1 and rate >= _s1:
            sell_qty = max(int(qty * SELL_1ST_QTY), 1)
            on_sell(code, sell_qty, f"1차익절({rate:+.2%})", current)
            tracker["stage"]      = 1
            tracker["remain_qty"] = qty - sell_qty
            # ★ 실효 진입가 보정
            realized_gain = (current - entry) * sell_qty
            tracker["effective_entry"] = max(
                entry - realized_gain / max(qty - sell_qty, 1),
                entry * 0.97,
            )
            return "1차익절"

        # ----------------------------------------------------------
        # ⑦ 손절 — ★ 개선: 단계별 + ATR 동적 조정
        # ----------------------------------------------------------
        if stage >= 1:
            # 1차 익절 후 본절 보호 (-2%)
            stop_line = STOP_LOSS_AFTER_1ST
            label     = "본절보호"
        elif is_weak:
            stop_line = _sl
            label     = "손절(약세장)"
        else:
            stop_line = _sl
            label     = "손절"

        # ★ ATR 보정: 변동성 큰 종목은 손절선을 ATR의 1.5배까지 확장 (잡음으로 손절 방지)
        if atr_rate > 0:
            atr_floor = -atr_rate * 1.5
            stop_line = max(stop_line, atr_floor)  # 더 깊은 쪽 채택 안함 (덜 깊게)

        if rate <= stop_line:
            on_sell(code, qty, f"{label}({rate:+.2%})", current)
            on_loss()
            peak_tracker.pop(code, None)
            print(f"📉 {label} {code} | {rate:+.2%} | 기준:{stop_line:.2%}")
            return label

        return None
