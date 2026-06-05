"""
risk_manager.py — 리스크 관리 모듈 (★ 신규)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

손실을 줄이고 수익을 극대화하기 위한 '돈 관리' 두뇌입니다.

▶ ATR(Average True Range): 변동성 지표. 일별 평균 가격 변동폭.
   - 변동성이 큰 종목은 손절선도 넓게(잡음에 휘둘리지 않게)
   - 변동성이 작은 종목은 손절선을 좁게

▶ 포지션 사이징: 종목 점수가 높을수록 더 많이 사고, 낮을수록 적게.
   - 90점 종목 → 베팅 금액 1.3배
   - 60점 종목 → 베팅 금액 0.8배

▶ 일일 손실 한도: 하루에 일정 금액 이상 잃으면 자동 일시중단

▶ 동시 보유 자금 분배: 멀티봇이 같은 자금을 동시에 쓰지 않도록 조율
================================================================
"""
import datetime
from typing import Optional


class RiskManager:
    """포지션 사이징과 손실 제한을 담당."""

    def __init__(self,
                 base_buy_amt: int = 200000,
                 max_daily_loss_pct: float = 0.03,
                 max_daily_loss_count: int = 2):
        """
        - base_buy_amt: 기본 매수 금액 (단타 20만원, 스윙 200만원)
        - max_daily_loss_pct: 하루 최대 손실률 (계좌 대비 3%)
        - max_daily_loss_count: 하루 최대 손절 횟수
        """
        self.base_buy_amt         = base_buy_amt
        self.max_daily_loss_pct   = max_daily_loss_pct
        self.max_daily_loss_count = max_daily_loss_count

    # ============================================================
    # 1. ATR 계산 (변동성)
    # ============================================================
    def calc_atr_rate(self, ohlc_history: list, period: int = 14) -> float:
        """
        ATR(Average True Range)을 현재가 대비 비율로 반환.

        ohlc_history: [{high, low, close}, ...] 최근 N일
        반환: ATR / 현재가  (예: 0.03이면 일평균 3% 변동)

        활용: stop_line = -atr_rate * 1.5 로 손절선을 변동성에 맞게 조정
        """
        if len(ohlc_history) < period + 1:
            return 0

        try:
            true_ranges = []
            for i in range(len(ohlc_history) - 1):
                today      = ohlc_history[i]
                yesterday  = ohlc_history[i + 1]
                tr = max(
                    today["high"]  - today["low"],
                    abs(today["high"]  - yesterday["close"]),
                    abs(today["low"]   - yesterday["close"]),
                )
                true_ranges.append(tr)
                if len(true_ranges) >= period:
                    break

            if not true_ranges:
                return 0
            atr = sum(true_ranges) / len(true_ranges)
            current = ohlc_history[0]["close"]
            return atr / current if current > 0 else 0
        except (KeyError, ZeroDivisionError):
            return 0

    # ============================================================
    # 2. 포지션 사이징 (점수에 비례한 베팅)
    # ============================================================
    @staticmethod
    def calc_kelly_fraction(win_rate: float,
                            avg_win: float,
                            avg_loss: float) -> float:
        """
        켈리 공식: f* = (p×b - q) / b
          p = 승률, q = 패배율(1-p)
          b = 평균수익/평균손실 비율 (손익비)

        ★ 보수적 켈리 사용 (Full Kelly의 25%)
          - Full Kelly는 이론상 최적이나 실전서 변동성 너무 큼
          - 25% 적용 시 손실 리스크 대폭 감소

        반환: 0.5 ~ 1.5 범위로 클리핑된 베팅 배수
        """
        if avg_loss <= 0 or win_rate <= 0:
            return 1.0

        p = win_rate           # 승률 (예: 0.67)
        q = 1 - p             # 패배율
        b = avg_win / avg_loss # 손익비 (예: 1.8)

        full_kelly = (p * b - q) / b if b > 0 else 0

        # 보수적 켈리 = Full Kelly의 25%
        conservative_kelly = full_kelly * 0.25

        # 0.5 ~ 1.5 범위 클리핑 (너무 작거나 큰 베팅 방지)
        return max(0.5, min(1.5, 1.0 + conservative_kelly))

    def calc_buy_amount(self, score: int,
                        atr_rate: float = 0,
                        is_theme: bool = False,
                        psbl_cash: int = 0,
                        code: str = "",
                        db_path: str = "trade_history.db") -> int:
        """
        ★ v2 — 켈리 공식 통합 포지션 사이징

        [계산 순서]
        1. 점수 기반 기본 배수
        2. ★ 켈리 공식 보정 (과거 성과 기반)
        3. ATR 변동성 보정
        4. 테마 보정
        5. 자금 한도 적용

        [켈리 공식]
        - 종목별 최근 20건 승률 + 손익비 → 최적 베팅 비율
        - 데이터 10건 미만 → 켈리 패스 (기존 방식)
        - 보수적 켈리 25% 적용 (Full Kelly의 위험 감소)
        """
        import sqlite3

        # ── 1. 점수 기반 기본 배수 ───────────────────────────
        if   score >= 90: multiplier = 1.4
        elif score >= 80: multiplier = 1.2
        elif score >= 70: multiplier = 1.0
        elif score >= 60: multiplier = 0.8
        else:             multiplier = 0.6

        # ── 2. ★ 켈리 공식 보정 ─────────────────────────────
        kelly_factor = 1.0
        try:
            import os
            db_candidates = [db_path, "trade_history.db"]
            conn = None
            for db in db_candidates:
                if os.path.exists(db):
                    conn = sqlite3.connect(db, timeout=5)
                    break

            if conn:
                # 종목별 최근 20건
                rows = conn.execute("""
                    SELECT profit_rate FROM trades
                    WHERE sell_price IS NOT NULL
                      AND profit_rate IS NOT NULL
                      AND profit_rate > -99  -- -100% 버그 제외
                      AND code = ?
                    ORDER BY id DESC LIMIT 20
                """, (code,)).fetchall() if code else []

                profits = [r[0] for r in rows]

                # 종목별 데이터 10건 미만이면 전체 평균 사용
                if len(profits) < 10:
                    rows2 = conn.execute("""
                        SELECT profit_rate FROM trades
                        WHERE sell_price IS NOT NULL
                          AND profit_rate IS NOT NULL
                          AND profit_rate > -99
                        ORDER BY id DESC LIMIT 100
                    """).fetchall()
                    profits = [r[0] for r in rows2]

                conn.close()

                if len(profits) >= 10:
                    wins   = [p for p in profits if p >= 0]
                    losses = [abs(p) for p in profits if p < 0]

                    if wins and losses:
                        win_rate = len(wins) / len(profits)
                        avg_win  = sum(wins)   / len(wins)
                        avg_loss = sum(losses) / len(losses)

                        kelly_factor = self.calc_kelly_fraction(
                            win_rate, avg_win, avg_loss
                        )
                        print(f"   📐 켈리 {code or '전체'} | "
                              f"승률:{win_rate*100:.0f}% | "
                              f"손익비:{avg_win/avg_loss:.2f} | "
                              f"켈리배수:{kelly_factor:.2f}x")

        except Exception as e:
            print(f"   ⚠️ 켈리 계산 오류: {e}")
            kelly_factor = 1.0

        multiplier *= kelly_factor

        # ── 3. ATR 변동성 보정 ──────────────────────────────
        if atr_rate >= 0.05:
            multiplier *= 0.8
        elif atr_rate >= 0.04:
            multiplier *= 0.9

        # ── 4. 테마 보정 ─────────────────────────────────────
        if is_theme:
            multiplier *= 1.1

        amount = int(self.base_buy_amt * multiplier)

        # ── 5. 자금 한도: 가용 자금의 30% 초과 금지 ─────────
        if psbl_cash > 0:
            cap    = int(psbl_cash * 0.3)
            amount = min(amount, cap)

        return amount

    # ============================================================
    # 3. 일일 손실 한도 체크
    # ============================================================
    def should_stop_trading(self, daily_loss_count: int,
                            daily_loss_amount: int = 0,
                            account_value: int = 0) -> tuple:
        """
        오늘 더 매수해도 되는지 판단.
        반환: (중단 여부, 사유)
        """
        if daily_loss_count >= self.max_daily_loss_count:
            return True, f"손절 {daily_loss_count}회 도달"

        if account_value > 0 and daily_loss_amount < 0:
            loss_pct = abs(daily_loss_amount) / account_value
            if loss_pct >= self.max_daily_loss_pct:
                return True, f"일일 손실 {loss_pct*100:.1f}% 초과"

        return False, ""

    # ============================================================
    # 4. 시간대별 매수 가중치
    # ============================================================
    def time_score_modifier(self, now_t: str) -> int:
        """
        시간대에 따라 점수 가산/감산.
        - 09:20~10:30: 강세 시간 → +0
        - 10:30~13:00: 보통 → +0
        - 13:00~14:30: 후장 → -3 (모멘텀 약화)
        - 14:30~15:15: 종가 임박 → -8 (다음날 위험)
        """
        if "0920" <= now_t < "1030":
            return 0
        elif "1030" <= now_t < "1300":
            return 0
        elif "1300" <= now_t < "1430":
            return -3
        elif "1430" <= now_t < EOD_SELL_TIME_FOR_RISK:
            return -8
        else:
            return 0

    # ============================================================
    # 5. 시장 상태 기반 매수 허용 판단
    # ============================================================
    def allow_buy_in_market(self, market_status: str,
                            is_sector_match: bool = False) -> tuple:
        """
        ★ 개선: 약세장에서도 강세 업종 매칭 종목은 허용

        - normal: 모두 허용
        - weak: 강세 업종 매칭만 허용
        - stop: 모두 금지
        """
        if market_status == "normal":
            return True, ""
        elif market_status == "weak":
            if is_sector_match:
                return True, "약세장이지만 강세업종 허용"
            return False, "약세장 신규매수 중단"
        else:  # stop
            return False, "시장 중단 모드"


# 종가 매도 시간 (전략 파일과 동일하게 유지)
EOD_SELL_TIME_FOR_RISK = "1515"
