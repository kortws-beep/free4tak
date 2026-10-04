"""
daybot_backtest_engine.py — 단타봇(daybot) 백테스트 엔진
================================================================
[실전 로직과의 정합성 — 중요한 한계]

daybot의 실전 종목선정은 키움 실시간 조건검색(주도주검색식3/단타000/
3개월수급 당일주도주, 2026-10-05부터 090930타점 대체)인데, 이건 과거 데이터로 재생할
API가 없다(확인된 사실 — bots/daybot.py 설계 당시 조사). 그래서 이
엔진은 "그 조건들이 대략 무엇을 찾으려는 것인지"를 거래대금+시가갭으로
근사한 후보풀을 쓴다 — 실제 키움이 그날 정확히 어떤 종목을 띄웠을지와
다를 수 있다는 점을 항상 염두에 둘 것. 매도(손절/익절트레일링/보유기한)
로직은 실전 bots/daybot.py와 최대한 동일하게 맞췄다(아래 상수 참고).

[후보풀 근사 방법]
- daily_ohlcv(backtest/data/backtest_data.db)의 (시가_T - 종가_T-1)/종가_T-1
  를 "아침 갭"으로 써서 EARLY_MIN~EARLY_MAX(기본 3~8%) 범위인 종목만
  후보로 삼음 — look-ahead 방지(당일 종가 기준 등락률이 아니라, 장 시작
  시점에 이미 알 수 있는 전일종가 대비 시가 갭만 사용. feature_builder.py가
  sbot 백테스터에서 쓰는 것과 동일한 원칙).
- 거래대금(value) 상위 N% 필터로 "시장 관심" 근사.
- 위 두 조건을 다 만족하는 종목 중 상위 절반을 "tier1_proxy"(겹침 근사),
  나머지를 "tier_judu_proxy"(주도주단독 근사)로 분류 — 실제 키움 조건
  일치여부가 아니라 신호 강도 기준의 단순화.
- ★ 실전의 "2차구간"(09:40 이후, 0~15% 더 넓은 밴드)은 일봉만으로는
  장중 특정 시각의 가격을 알 수 없어 재현 불가 — 이 백테스터는 사실상
  "1차구간(초반 갭상승)" 진입만 시뮬레이션한다. 호가창 매도/매수잔량비
  (HOGA_ASK_BID_RATIO_MIN) 게이트도 과거 호가 데이터가 없어 재현 불가 —
  생략.

[매도 로직 — 실전과 동일 상수, 일봉 고가/저가로 근사]
- 손절: STOP_LOSS_PCT(-3.5%) — 해당일 저가가 진입가 대비 이 이하로
  내려가면 그 가격에 체결된 것으로 간주.
- 익절 트레일링: TAKE_PROFIT_PCT(+2.5%) 도달 후 고점(peak) 대비
  TRAILING_STOP_PCT(2.0%) 하락시 매도. 고점은 "그날의 고가"로 갱신,
  저가가 트레일링 선 아래로 내려가면 그날 매도 체결로 간주(일중 고점→
  저점 순서를 정확히 알 수 없는 근사 — 다른 봇 백테스터들도 동일 수준
  근사를 이미 받아들이고 있음).
- 보유기한청산: HOLD_DAYS_LIMIT(3영업일) — 트레일링 미진입 상태로 이
  영업일수 지나면 손익 무관 강제청산. 주말은 DB에 거래일만 있어 자동
  제외됨.

[사용법]
  from daybot_backtest_engine import DayBotBacktestEngine, DayBotBacktestConfig
  cfg = DayBotBacktestConfig(start_date="2024-06-01", end_date="2026-10-02")
  engine = DayBotBacktestEngine(cfg, db_path="backtest/data/backtest_data.db")
  engine.run()
  trades = engine.get_trades()
"""
import os
import sqlite3
import datetime
from dataclasses import dataclass, field, asdict
from typing import Optional

import pandas as pd


# ============================================================
# 실전 상수 기본값 (bots/daybot.py 2026-10-02 기준과 동일)
# ============================================================
DEFAULT_BASE_MAX_POSITIONS = 3
DEFAULT_BONUS_SLOT_MIN_CASH = 500_000
DEFAULT_BUY_AMT_PER_SLOT   = 1_000_000
DEFAULT_TAKE_PROFIT_PCT    = 2.5
DEFAULT_STOP_LOSS_PCT      = -3.5
DEFAULT_TRAILING_STOP_PCT  = 2.0
DEFAULT_TRAILING_STOP_PCT_TIGHT = 1.5   # ★ 2026-10-03: 고점 변동률 2.5~4.5% 구간 전용
DEFAULT_TRAILING_STOP_WIDEN_PCT = 4.5   # 고점 변동률이 이 초과면 trailing_stop_pct(2.0%) 적용
DEFAULT_MIN_LOCKED_PROFIT_PCT   = 1.0   # 트레일링 매도가의 최소 보장 수익률(세전) 하한선
DEFAULT_HOLD_DAYS_LIMIT    = 3
DEFAULT_EARLY_MIN_CHANGE_PCT = 3.0
DEFAULT_EARLY_MAX_CHANGE_PCT = 8.0
DEFAULT_MIN_TRADING_VALUE_PCTL = 0.7   # 거래대금 상위 30%(=하위 70%ile 컷)


# ============================================================
# 거래 기록
# ============================================================
@dataclass
class DayBotTrade:
    code:        str
    buy_date:    str
    buy_price:   float
    qty:         int
    source_tier: str   = ""
    sell_date:   str   = ""
    sell_price:  float = 0.0
    sell_reason: str   = ""
    profit_rate: float = 0.0
    profit_krw:  float = 0.0
    fee:         float = 0.0
    held_trading_days: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


# ============================================================
# 백테스트 설정
# ============================================================
@dataclass
class DayBotBacktestConfig:
    initial_cash:      int   = 10_000_000
    base_max_positions: int  = DEFAULT_BASE_MAX_POSITIONS
    bonus_slot_min_cash: int = DEFAULT_BONUS_SLOT_MIN_CASH
    buy_amt_per_slot:  int   = DEFAULT_BUY_AMT_PER_SLOT
    take_profit_pct:   float = DEFAULT_TAKE_PROFIT_PCT
    stop_loss_pct:     float = DEFAULT_STOP_LOSS_PCT
    trailing_stop_pct: float = DEFAULT_TRAILING_STOP_PCT
    trailing_stop_pct_tight: float = DEFAULT_TRAILING_STOP_PCT_TIGHT
    trailing_stop_widen_pct: float = DEFAULT_TRAILING_STOP_WIDEN_PCT
    min_locked_profit_pct:   float = DEFAULT_MIN_LOCKED_PROFIT_PCT
    use_tiered_trailing:     bool  = True   # False면 trailing_stop_pct 단일값(구버전) 사용
    hold_days_limit:   int   = DEFAULT_HOLD_DAYS_LIMIT
    early_min_change_pct: float = DEFAULT_EARLY_MIN_CHANGE_PCT
    early_max_change_pct: float = DEFAULT_EARLY_MAX_CHANGE_PCT
    min_trading_value_pctl: float = DEFAULT_MIN_TRADING_VALUE_PCTL
    fee_rate:          float = 0.00015
    tax_rate:          float = 0.0015
    slippage:          float = 0.0005
    start_date:        str   = "2024-06-01"
    end_date:          str   = "2026-10-02"
    codes:             list  = field(default_factory=list)
    # ★ 일봉 엔진이라 "오전/오후"를 따로 구분할 하루 중 세부 시점이 없음
    #   — 요일효과를 보려면 해당 요일 전체를 스킵하는 수준까지만 가능.
    #   0=월 1=화 2=수 3=목 4=금. 예: [0]=월요일 신규매수 전체 스킵.
    exclude_weekdays: list = field(default_factory=list)
    verbose:           bool  = False


class DataLoader:
    """backtest_data.db의 daily_ohlcv 로더 (feature_builder.DataLoader와
    동일 패턴, daybot 엔진 전용 — 별도 테이블 스키마 의존 없이 단순화)."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._cache: dict = {}

    def all_codes(self) -> list:
        conn = sqlite3.connect(self.db_path, timeout=10)
        rows = conn.execute("SELECT DISTINCT code FROM daily_ohlcv").fetchall()
        conn.close()
        return [r[0] for r in rows]

    def load(self, code: str) -> pd.DataFrame:
        if code in self._cache:
            return self._cache[code]
        conn = sqlite3.connect(self.db_path, timeout=10)
        df = pd.read_sql(
            "SELECT date, open, high, low, close, volume, value, change "
            "FROM daily_ohlcv WHERE code = ? ORDER BY date",
            conn, params=(code,))
        conn.close()
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"], format="mixed")
            df = df.set_index("date").sort_index()
            df = df[~df.index.duplicated(keep="last")]
        self._cache[code] = df
        return df


# ============================================================
# daybot 백테스트 엔진
# ============================================================
class DayBotBacktestEngine:

    def __init__(self, config: DayBotBacktestConfig, db_path: str):
        self.config = config
        self.loader = DataLoader(db_path)
        self.codes  = config.codes or self.loader.all_codes()

        self.cash         = config.initial_cash
        self.positions    = {}    # code -> {entry_price, qty, buy_date, peak_price, held_trading_days, source_tier}
        self.open_trades  = {}    # code -> DayBotTrade
        self.trades        = []
        self.equity_curve  = []

        self._per_code = {c: self.loader.load(c) for c in self.codes}
        self._all_dates = self._build_trading_calendar()

    def _build_trading_calendar(self) -> list:
        all_idx = set()
        for df in self._per_code.values():
            if not df.empty:
                all_idx.update(df.index)
        dates = sorted(d for d in all_idx
                       if self.config.start_date <= d.strftime("%Y-%m-%d") <= self.config.end_date)
        return dates

    # ------------------------------------------------------------
    # 후보풀 근사 (모듈 docstring의 "후보풀 근사 방법" 참고)
    # ------------------------------------------------------------
    def _candidates_for_date(self, date: pd.Timestamp) -> list:
        rows = []
        for code, df in self._per_code.items():
            if date not in df.index:
                continue
            idx = df.index.get_loc(date)
            if idx == 0:
                continue   # 전일 데이터 없으면 갭 계산 불가
            today = df.iloc[idx]
            prev_close = df.iloc[idx - 1]["close"]
            if not prev_close or prev_close <= 0:
                continue
            gap_pct = (today["open"] - prev_close) / prev_close * 100
            if not (self.config.early_min_change_pct <= gap_pct <= self.config.early_max_change_pct):
                continue
            value = today.get("value", 0) or 0
            rows.append((code, gap_pct, value, today["open"]))

        if not rows:
            return []

        rows.sort(key=lambda r: r[2])  # 거래대금 오름차순
        cutoff_idx = int(len(rows) * self.config.min_trading_value_pctl)
        rows = rows[cutoff_idx:]  # 거래대금 상위 구간만 남김
        if not rows:
            return []

        # 거래대금 내림차순 재정렬 후 상위 절반을 tier1_proxy로 라벨
        rows.sort(key=lambda r: r[2], reverse=True)
        half = max(1, len(rows) // 2)
        out = []
        for i, (code, gap_pct, value, open_price) in enumerate(rows):
            tier = "tier1_proxy" if i < half else "tier_judu_proxy"
            out.append((code, open_price, tier))
        return out

    # ------------------------------------------------------------
    # 체결 시뮬레이션
    # ------------------------------------------------------------
    def _effective_max(self) -> int:
        if len(self.positions) >= self.config.base_max_positions:
            if self.cash >= self.config.bonus_slot_min_cash:
                return self.config.base_max_positions + 1
        return self.config.base_max_positions

    def _simulate_buy(self, code: str, price: float, date: pd.Timestamp, tier: str):
        amount = min(self.config.buy_amt_per_slot, self.cash)
        if amount < 500_000:  # MIN_ANALYSIS_CASH와 동일한 하한
            return
        fill_price = price * (1 + self.config.slippage)
        qty = int(amount / (fill_price * (1 + self.config.fee_rate)))
        if qty <= 0:
            return
        cost = fill_price * qty
        fee  = cost * self.config.fee_rate
        total = cost + fee
        if total > self.cash:
            return

        self.cash -= total
        self.positions[code] = {
            "entry_price": fill_price, "qty": qty,
            "buy_date": date, "peak_price": None,
            "held_trading_days": 0, "source_tier": tier,
        }
        self.open_trades[code] = DayBotTrade(
            code=code, buy_date=date.strftime("%Y-%m-%d"),
            buy_price=fill_price, qty=qty, source_tier=tier,
        )
        if self.config.verbose:
            print(f"   🟢 매수 {code} {qty}주 @ {fill_price:,.0f} [{tier}] ({date.date()})")

    def _simulate_sell(self, code: str, price: float, reason: str, date: pd.Timestamp):
        pos = self.positions.get(code)
        if not pos:
            return
        fill_price = price * (1 - self.config.slippage)
        revenue = fill_price * pos["qty"]
        fee  = revenue * self.config.fee_rate
        tax  = revenue * self.config.tax_rate
        net  = revenue - fee - tax
        self.cash += net

        trade = self.open_trades.pop(code, None)
        if trade:
            trade.sell_date = date.strftime("%Y-%m-%d")
            trade.sell_price = fill_price
            trade.sell_reason = reason
            trade.fee = fee + tax
            # ★ metrics.py/calc_metrics()는 sbot_backtest_engine.py와 동일하게
            #   profit_rate를 0~1 "비율"로 기대함(퍼센트 숫자 아님) — *100을
            #   붙이면 승수가 100배로 뻥튀기돼 집계가 전부 깨짐(실제 발견된 버그).
            trade.profit_rate = (fill_price - pos["entry_price"]) / pos["entry_price"]
            trade.profit_krw  = net - pos["entry_price"] * pos["qty"]
            trade.held_trading_days = pos["held_trading_days"]
            self.trades.append(trade)
        self.positions.pop(code, None)
        if self.config.verbose:
            print(f"   🔴 매도 {code} @ {fill_price:,.0f} | {reason} ({date.date()})")

    # ------------------------------------------------------------
    # 보유 포지션 일일 체크 (손절/트레일링/보유기한)
    # ------------------------------------------------------------
    def _check_exits_for_date(self, date: pd.Timestamp):
        for code in list(self.positions.keys()):
            df = self._per_code.get(code)
            if df is None or date not in df.index:
                continue
            bar = df.loc[date]
            pos = self.positions[code]
            entry = pos["entry_price"]

            if pos["peak_price"] is not None:
                new_peak = max(pos["peak_price"], bar["high"])
                pos["peak_price"] = new_peak
                peak_rate = (new_peak - entry) / entry * 100
                if self.config.use_tiered_trailing and peak_rate <= self.config.trailing_stop_widen_pct:
                    trail_pct = self.config.trailing_stop_pct_tight
                else:
                    trail_pct = self.config.trailing_stop_pct
                trail_stop = new_peak * (1 - trail_pct / 100)
                floor_price = entry * (1 + self.config.min_locked_profit_pct / 100)
                trail_stop = max(trail_stop, floor_price)
                if bar["low"] <= trail_stop:
                    rate = (trail_stop - entry) / entry * 100
                    self._simulate_sell(code, trail_stop,
                                        f"트레일링청산(고점{new_peak:,.0f}대비"
                                        f"-{trail_pct:.1f}%, 총{rate:+.2f}%)",
                                        date)
                continue

            if bar["high"] >= entry * (1 + self.config.take_profit_pct / 100):
                pos["peak_price"] = bar["high"]
                continue

            if bar["low"] <= entry * (1 + self.config.stop_loss_pct / 100):
                stop_price = entry * (1 + self.config.stop_loss_pct / 100)
                self._simulate_sell(code, stop_price,
                                    f"손절({self.config.stop_loss_pct:.1f}%)", date)
                continue

            pos["held_trading_days"] += 1
            if pos["held_trading_days"] >= self.config.hold_days_limit:
                rate = (bar["close"] - entry) / entry * 100
                self._simulate_sell(code, bar["close"],
                                    f"보유기한청산({pos['held_trading_days']}영업일, {rate:+.2f}%)",
                                    date)

    # ------------------------------------------------------------
    # 메인 루프
    # ------------------------------------------------------------
    def run(self):
        for date in self._all_dates:
            weekday = date.weekday()  # 0=월 ... 4=금
            self._check_exits_for_date(date)

            if weekday not in self.config.exclude_weekdays:
                effective_max = self._effective_max()
                if len(self.positions) < effective_max:
                    candidates = self._candidates_for_date(date)
                    for code, open_price, tier in candidates:
                        if len(self.positions) >= effective_max:
                            break
                        if code in self.positions:
                            continue
                        self._simulate_buy(code, open_price, date, tier)

            total_value = self.cash
            for code, pos in self.positions.items():
                df = self._per_code.get(code)
                if df is not None and date in df.index:
                    total_value += df.loc[date]["close"] * pos["qty"]
                else:
                    total_value += pos["entry_price"] * pos["qty"]
            self.equity_curve.append((date.strftime("%Y-%m-%d"), total_value))

        # 종료 시점 미청산 포지션은 마지막 종가로 강제 청산(평가손익 반영)
        last_date = self._all_dates[-1] if self._all_dates else None
        if last_date is not None:
            for code in list(self.positions.keys()):
                df = self._per_code.get(code)
                price = df.loc[last_date]["close"] if (df is not None and last_date in df.index) \
                    else self.positions[code]["entry_price"]
                self._simulate_sell(code, price, "백테스트종료청산", last_date)

    def get_trades(self) -> list:
        return [t.to_dict() for t in self.trades]

    def get_equity_curve(self) -> list:
        return self.equity_curve
