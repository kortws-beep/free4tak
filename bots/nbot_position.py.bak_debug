"""
nbot_position.py — 단타봇 포지션 추적 / 매도 체크 모듈
================================================================
[이 파일이 하는 일]
  보유 종목의 손익을 매 루프마다 체크하고 매도 조건을 판단한다.

[nbot.py에서 분리된 메서드]
  _get_atr_rate()       ATR 기반 변동성 계산 (캐시 포함)
  _check_all_sells()    전체 보유 종목 매도 체크
  _check_sells_only()   일시중단 시 매도만 체크
  _save_status()        상태 파일 저장 (kiki.py가 읽음)

[의존성]
  strategy.py      check_sell() — 매도 조건 판단
  risk_manager.py  calc_atr_rate()
  common_utils.py  상태 파일 헬퍼

[사용법 — nbot.py에서]
  from nbot_position import PositionManager
  self.pos_mgr = PositionManager(api, strategy, risk, positions, ...)
  self.pos_mgr.check_all_sells(pos_mkt_cache, now_t)
"""

import time

from common_utils import (
    safe_float, now_hms,
)


class PositionManager:
    """
    포지션 추적 + 매도 체크 + 상태 저장.
    NBot 인스턴스와 공유 상태(positions, peak_tracker 등)를
    레퍼런스로 받아 직접 수정한다.
    """

    def __init__(
        self,
        api,
        strategy,
        risk,
        positions: dict,      # NBot.positions (레퍼런스 공유)
        peak_tracker: dict,   # NBot.peak_tracker
        buy_tags: dict,       # NBot.buy_tags
        code_name_map: dict,  # NBot.code_name_map
        order_mgr,            # OrderManager 인스턴스
        market_status_fn,     # callable: () → str
        market_rate_fn,       # callable: () → float
        is_paused_fn,         # callable: () → bool
        write_status_fn,      # callable: (status_dict) → None
    ):
        self.api             = api
        self.strategy        = strategy
        self.risk            = risk
        self.positions       = positions
        self.peak_tracker    = peak_tracker
        self.buy_tags        = buy_tags
        self.code_name_map   = code_name_map
        self.order           = order_mgr
        self._market_status  = market_status_fn   # () → str
        self._market_rate    = market_rate_fn      # () → float
        self._is_paused      = is_paused_fn        # () → bool
        self._write_status   = write_status_fn     # (dict) → None

        # ATR 캐시: {code: (atr_rate, timestamp)}
        self.atr_cache: dict = {}
        # 기술지표 캐시 (nbot 메인에서 공유)
        self._tech_cache: dict = {}

    # ----------------------------------------------------------
    # ATR (변동성) 계산
    # ----------------------------------------------------------
    def get_atr_rate(self, code: str) -> float:
        """
        종목의 변동성(ATR / 현재가) 반환.
        변동성 큰 종목 → 손절선을 넓게 → 잡음 손절 방지.
        30분 캐시.
        """
        if code in self.atr_cache:
            cached_rate, ts = self.atr_cache[code]
            if time.time() - ts < 1800:
                return cached_rate

        try:
            ohlc = (self.api.get_daily_ohlc(code, days=20)
                    if hasattr(self.api, "get_daily_ohlc") else [])
            if not ohlc:
                default_atr = 0.03  # 데이터 없으면 3% 기본값
                self.atr_cache[code] = (default_atr, time.time())
                return default_atr
            atr_rate = self.risk.calc_atr_rate(ohlc, period=14)
            self.atr_cache[code] = (atr_rate, time.time())
            return atr_rate
        except Exception:
            return 0.0

    def reset_atr_cache(self):
        """일일 초기화 시 호출"""
        self.atr_cache = {}

    # ----------------------------------------------------------
    # 매도 체크
    # ----------------------------------------------------------
    def check_all_sells(self, pos_mkt_cache: dict, now_t: str):
        """모든 보유 종목 매도 체크"""
        for code, pos in list(self.positions.items()):
            mdata = pos_mkt_cache.get(code) or self.api.get_market_data(code)
            if not mdata:
                continue

            tech = self._tech_cache.get(code, ({}, 0))
            ma10 = tech[0].get("ma10", 0) if isinstance(tech, tuple) else 0
            atr_rate = self.get_atr_rate(code)

            self.strategy.check_sell(
                code, pos, now_t, mdata,
                self._market_status(), self.peak_tracker, self.buy_tags,
                self._is_paused(),
                # 2차 매수 콜백
                lambda c, p, a: self.order.do_buy(c, p, a, is_second=True),
                # 매도 콜백
                lambda c, q, r, sp: self.order.do_sell(c, q, r, sp),
                # 손절 콜백
                self.order.do_loss,
                market_rate=self._market_rate(),
                ma10=ma10,
                atr_rate=atr_rate,
            )

    def check_sells_only(self, pos_mkt_cache: dict, now_t: str):
        """일시중단 시 매도만 체크 (check_all_sells 위임)"""
        self.check_all_sells(pos_mkt_cache, now_t)

    # ----------------------------------------------------------
    # 보유 종목 현황 출력
    # ----------------------------------------------------------
    def print_positions(self, pos_mkt_cache: dict) -> float:
        """
        보유 종목 현황 출력 + 총손익 반환.
        nbot.py 메인 루프에서 호출.
        """
        total_profit = 0.0
        print("📦 보유종목")
        for code, pos in self.positions.items():
            data = pos_mkt_cache.get(code) or self.api.get_market_data(code)
            if not data:
                continue
            cur    = safe_float(data.get("stck_prpr", 0))
            entry  = pos["entry_price"]
            qty    = pos["qty"]
            profit = (cur - entry) * qty
            rate   = (cur - entry) / entry * 100 if entry > 0 else 0
            total_profit += profit
            tag = "🎯" if self.buy_tags.get(code) == "theme_buy" else "  "
            name = self.code_name_map.get(code, code)
            print(f"  {tag}💰 {code}({name}) | {rate:+.2f}% | {qty}주")
        print(f"📈 총손익: {int(total_profit):,}원")
        return total_profit

    # ----------------------------------------------------------
    # 상태 저장
    # ----------------------------------------------------------
    def save_status(self, cash: int, psbl_cash: int, total_profit: float,
                    score_enter: int, pos_mkt_cache: dict, now: str,
                    active_sectors: list, market_rate: float,
                    market_status: str, daily_loss: int):
        """현재 상태를 JSON 파일에 저장 (kiki.py가 읽음)"""
        pos_detail = {}
        for code, pos in self.positions.items():
            mdata = pos_mkt_cache.get(code)
            cur   = safe_float(mdata.get("stck_prpr", 0)) if mdata else 0
            entry = pos["entry_price"]
            qty   = pos["qty"]
            rate  = (cur - entry) / entry * 100 if entry > 0 else 0
            pos_detail[code] = {
                "name":        self.code_name_map.get(code, code),
                "current":     int(cur),
                "entry_price": int(entry),
                "qty":         qty,
                "rate":        round(rate, 2),
                "buy_tag":     self.buy_tags.get(code, ""),
            }

        self._write_status({
            "cash":             cash,
            "psbl_cash":        psbl_cash,
            "total_profit":     int(total_profit),
            "positions":        len(self.positions),
            "positions_detail": pos_detail,
            "score_enter":      score_enter,
            "last_update":      now,
            "code_name_map":    self.code_name_map,
            "market_status":    market_status,
            "market_rate":      market_rate,
            "daily_loss":       daily_loss,
            "active_sectors":   active_sectors,
        })
