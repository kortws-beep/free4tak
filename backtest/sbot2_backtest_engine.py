"""
sbot2_backtest_engine.py — 중단기 스윙봇2 백테스트 엔진
================================================================
[sbot1 엔진과의 핵심 차이]

항목            sbot1                   sbot2
─────────────────────────────────────────────────────
익절선          +8% / +15% / +25%       +15% / +25% / +40%
손절선          -7% (1차 후 본절 -3%)   -10% (1차 후 본절 -5%)
트레일링        고점 -4% / -3%          고점 -8% / -10%
보유기간        최대 11영업일           최대 20영업일
종목 필터       거래대금 100억+         거래대금 200억+ (중대형주)
매수금액        50만원~                 100만원~
MA 필터         없음                    MA20 위 필수 + MA60 체크
눌림목 체크     없음                    52주 고가 대비 -10~25%

[사용법]
  from sbot2_backtest_engine import SBot2BacktestEngine, SBot2BacktestConfig

  cfg = SBot2BacktestConfig(
      initial_cash  = 10_000_000,
      base_buy_amt  = 1_000_000,
      max_positions = 5,
      start_date    = "2024-01-01",
      end_date      = "2025-12-31",
      codes         = ["005930","000660",...],
  )
  engine = SBot2BacktestEngine(cfg, db_path="backtest_data.db")
  engine.run()
  report = engine.get_report()
================================================================
"""
import os
import sys
import sqlite3
import datetime
from dataclasses import dataclass, field, asdict
from typing import Optional

import pandas as pd

# ── 경로 탐색 ────────────────────────────────────────────────
_stock_bot_root = None
_candidates = [
    os.environ.get("K_BOT_ROOT"),
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..")),
    "/home/free4tak/k-bot/stock_bot",
    "/mnt/project",
]
for _root in _candidates:
    if _root and os.path.exists(os.path.join(_root, "core", "sbot2_strategy.py")):
        _stock_bot_root = _root
        break
    if _root and os.path.exists(os.path.join(_root, "sbot2_strategy.py")):
        _stock_bot_root = os.path.dirname(_root)
        break

if not _stock_bot_root:
    # sbot_strategy.py라도 있으면 사용
    for _root in _candidates:
        if _root and os.path.exists(os.path.join(_root, "core", "sbot_strategy.py")):
            _stock_bot_root = _root
            break

if _stock_bot_root:
    for _sub in ["core", "intelligence", "bots", "backtest", ""]:
        _p = os.path.join(_stock_bot_root, _sub)
        if os.path.isdir(_p) and _p not in sys.path:
            sys.path.insert(0, _p)
    PROJECT_ROOT = _stock_bot_root
    print(f"✅ sbot2_backtest_engine: PROJECT_ROOT={_stock_bot_root}")
else:
    raise ImportError("sbot2_strategy.py 위치를 찾을 수 없음. K_BOT_ROOT 환경변수 설정 필요")

try:
    from sbot2_strategy import MidSwingStrategy as _StrategyClass
    print("✅ MidSwingStrategy (sbot2_strategy.py) 로드")
except ImportError:
    from sbot_strategy import SwingStrategy as _StrategyClass
    print("⚠️ MidSwingStrategy 없음 → SwingStrategy 폴백")

from risk_manager    import RiskManager
from feature_builder import DataLoader, build_features_at, get_market_data_at


# ============================================================
# sbot2 매도 기준 (sbot2_strategy.py 실전과 동일)
# ============================================================
MID_SELL_1ST_RATE  = 0.15    # 1차 익절: +15%
MID_SELL_1ST_QTY   = 0.30    # → 30% 매도
MID_SELL_2ND_RATE  = 0.25    # 2차 익절: +25%
MID_SELL_2ND_QTY   = 0.40    # → 잔량의 40%
MID_SELL_3RD_RATE  = 0.40    # 3차 익절: +40% → 전량

MID_STOP_LOSS      = -0.10   # 기본 손절: -10%
MID_STOP_AFTER_1ST = -0.05   # 1차 익절 후 본절 보호: -5%

MID_TRAIL_AFTER_1ST = 0.08   # 1차 후 트레일링: 고점 -8%
MID_TRAIL_AFTER_2ND = 0.10   # 2차 후 트레일링: 고점 -10%

MID_MAX_HOLD_DAYS  = 20      # 최대 보유 영업일
MID_MA20_EXIT_RATE = 0.97    # MA20 × 0.97 이탈 시 매도 (1차 익절 후)


# ============================================================
# 거래 기록
# ============================================================
@dataclass
class MidTrade:
    code:         str
    buy_date:     str
    buy_price:    float
    qty:          int
    sell_date:    str   = ""
    sell_price:   float = 0.0
    sell_reason:  str   = ""
    profit_rate:  float = 0.0
    profit_krw:   float = 0.0
    fee:          float = 0.0
    score:        int   = 0
    hold_days:    int   = 0

    def to_dict(self) -> dict:
        return asdict(self)


# ============================================================
# 백테스트 설정
# ============================================================
@dataclass
class SBot2BacktestConfig:
    # 자본
    initial_cash:      int   = 10_000_000
    base_buy_amt:      int   = 1_000_000   # 100만원/종목
    max_positions:     int   = 5
    # 매수 임계치
    buy_score_min:     int   = 60
    # 종목 필터 (중대형주)
    min_trading_value: int   = 200         # 억원 단위 (200억 이상)
    # 거래비용
    fee_rate:          float = 0.00015
    tax_rate:          float = 0.0015
    slippage:          float = 0.0005
    # 기간
    start_date:        str   = "2024-01-01"
    end_date:          str   = "2025-12-31"
    # 종목 풀
    codes:             list  = field(default_factory=list)
    # AI 점수 모드
    ai_score_mode:     str   = "rule_proxy"
    ai_score_fixed:    int   = 60
    # 리스크
    max_daily_loss:    int   = 3
    # 디버그
    verbose:           bool  = False
    # sbot2 특화 필터
    use_ma20_filter:   bool  = True    # MA20 위 필수 조건
    use_pullback:      bool  = True    # 눌림목 필터
    use_vcp:           bool  = True    # VCP 패턴


# ============================================================
# sbot2 백테스트 엔진
# ============================================================
class SBot2BacktestEngine:
    """중단기 스윙봇2 전용 백테스트 엔진."""

    def __init__(self, config: SBot2BacktestConfig, db_path: str):
        self.config       = config
        self.db_path      = db_path
        self.loader       = DataLoader(db_path)
        self._market_meta = self._load_market_meta(db_path)
        self.strategy     = _StrategyClass()
        self.risk_manager = RiskManager(
            base_buy_amt=config.base_buy_amt,
            max_daily_loss_pct=0.03,
            max_daily_loss_count=config.max_daily_loss,
        )

        # 상태
        self.cash             = config.initial_cash
        self.positions        = {}
        self.open_trades      = {}
        self.trades           = []
        self.peak_tracker     = {}
        self.sold_today       = {}   # ★ 당일 매도 종목 재매수 방지
        self.daily_loss_count = 0
        self.daily_loss_amt   = 0
        self.equity_curve     = []
        self._last_date       = ""

    # ----------------------------------------------------------
    # 시장 메타 (날짜별 코스피 등락률)
    # ----------------------------------------------------------
    def _load_market_meta(self, db_path: str) -> dict:
        meta = {}
        try:
            conn = sqlite3.connect(db_path, timeout=10)
            rows = conn.execute("""
                SELECT date, market_status, kospi_rate
                FROM market_meta ORDER BY date
            """).fetchall()
            conn.close()
            for date, status, rate in rows:
                meta[date] = {"status": status or "normal", "kospi": rate or 0}
        except Exception as e:
            print(f"⚠️ market_meta 로드 오류: {e}")
        return meta

    # ----------------------------------------------------------
    # 거래비용
    # ----------------------------------------------------------
    def _calc_fee(self, price: float, qty: int, is_sell: bool) -> float:
        cfg = self.config
        fee = price * qty * cfg.fee_rate
        if is_sell:
            fee += price * qty * cfg.tax_rate
        return fee

    def _buy_price(self, open_price: float) -> float:
        return open_price * (1 + self.config.slippage)

    def _sell_price(self, target: float) -> float:
        return target * (1 - self.config.slippage)

    # ----------------------------------------------------------
    # 가상 매수
    # ----------------------------------------------------------
    def _do_buy(self, code: str, date: str, open_price: float,
                score: int, features: dict):
        cfg      = self.config
        buy_p    = self._buy_price(open_price)
        qty      = max(1, int(cfg.base_buy_amt / buy_p))
        cost     = buy_p * qty + self._calc_fee(buy_p, qty, False)

        if self.cash < cost:
            return False

        self.cash -= cost
        self.positions[code] = {
            "entry_price": buy_p,
            "qty":         qty,
            "buy_date":    date,
            "score":       score,
        }
        self.open_trades[code] = MidTrade(
            code=code, buy_date=date, buy_price=buy_p,
            qty=qty, score=score,
            fee=self._calc_fee(buy_p, qty, False),
        )
        self.peak_tracker[code] = {
            "peak_rate":       0.0,
            "stage":           0,
            "remain_qty":      qty,
            "effective_entry": buy_p,
            "buy_date":        date,
        }
        if cfg.verbose:
            print(f"  🛒 [BT2] {date} 매수 {code} @{buy_p:,.0f} × {qty}주 | {score}점")
        return True

    # ----------------------------------------------------------
    # 가상 매도
    # ----------------------------------------------------------
    def _do_sell(self, code: str, qty: int, sell_date: str,
                 sell_price: float, reason: str) -> float:
        if code not in self.positions:
            return 0.0

        pos      = self.positions[code]
        entry    = pos["entry_price"]
        sell_p   = self._sell_price(sell_price)
        fee      = self._calc_fee(sell_p, qty, True)
        proceeds = sell_p * qty - fee
        profit   = (sell_p - entry) * qty - fee
        rate     = (sell_p - entry) / entry   # 소수 단위 (0.08 = +8%)

        self.cash += proceeds

        # 남은 수량 처리
        remaining = pos["qty"] - qty
        if remaining <= 0:
            t = self.open_trades.pop(code, None)
            if t:
                t.sell_date   = sell_date
                t.sell_price  = sell_p
                t.sell_reason = reason
                t.profit_rate = rate
                t.profit_krw  = profit
                t.hold_days   = self._calc_hold_days(t.buy_date, sell_date)
                self.trades.append(t)
            self.positions.pop(code, None)
            self.peak_tracker.pop(code, None)
        else:
            self.positions[code]["qty"] = remaining
            if code in self.peak_tracker:
                self.peak_tracker[code]["remain_qty"] = remaining

        # ★ 매도 종목 당일 재매수 방지
        self.sold_today[code] = sell_date

        if reason.startswith("손절") or reason.startswith("약세") or reason.startswith("갭하락"):
            self.daily_loss_count += 1

        if self.config.verbose:
            emoji = "✅" if rate >= 0 else "❌"
            print(f"  {emoji} [BT2] {sell_date} 매도 {code} {reason} | {rate:+.2f}%")

        return rate

    def _calc_hold_days(self, buy_date: str, sell_date: str) -> int:
        try:
            bd = datetime.date.fromisoformat(buy_date)
            sd = datetime.date.fromisoformat(sell_date)
            return (sd - bd).days
        except Exception:
            return 0

    # ----------------------------------------------------------
    # ★ sbot2 특화 매수 필터
    # ----------------------------------------------------------
    def _passes_mid_filter(self, code: str, date: str,
                            features: dict) -> tuple:
        """sbot2 특화 필터: MA20 위 + 눌림목 체크"""
        cfg = self.config
        ma20 = features.get("ma20", 0)
        ma60 = features.get("ma60", 0)
        cur  = features.get("current_price", features.get("close", 0))
        rsi  = features.get("rsi",   50)
        tvol = features.get("trading_value", 0)

        # 거래대금 필터 (중대형주)
        if tvol < cfg.min_trading_value:
            return False, f"거래대금 부족({tvol:.0f}억<{cfg.min_trading_value}억)"

        # MA20 위 필터 (핵심)
        if cfg.use_ma20_filter and ma20 > 0 and cur > 0:
            if cur < ma20 * MID_MA20_EXIT_RATE:
                return False, f"MA20 하방({cur:,.0f}<MA20:{ma20:,.0f})"

        # MA60 하방 완전 하락추세 제외
        if ma60 > 0 and cur > 0 and cur < ma60 * 0.92:
            return False, "MA60 하방 — 하락추세"

        # RSI 과매수 제외
        if rsi >= 80:
            return False, f"RSI 과매수({rsi:.0f})"

        return True, ""

    # ----------------------------------------------------------
    # 하루 매도 체크 (4시점: O→L→H→C)
    # ----------------------------------------------------------
    def _check_sell_day(self, code: str, date: str,
                         o: float, h: float, l: float, c: float,
                         ma20: float, features: dict):
        if code not in self.positions:
            return

        pos     = self.positions[code]
        entry   = pos["entry_price"]
        tracker = self.peak_tracker.get(code, {})
        stage   = tracker.get("stage", 0)
        peak    = tracker.get("peak_rate", 0)
        eff_ent = tracker.get("effective_entry", entry)
        remain  = tracker.get("remain_qty", pos["qty"])
        buy_date = pos.get("buy_date", date)

        def rate_at(p): return (p - eff_ent) / eff_ent if eff_ent else 0
        def raw_rate(p): return (p - entry) / entry if entry else 0

        # ── 시가 갭하락 손절 ──────────────────────────────────
        gap_rate = rate_at(o)
        if gap_rate <= MID_STOP_LOSS and stage == 0:
            self._do_sell(code, remain, date, o, f"갭하락손절({gap_rate:+.2%})")
            return
        if stage >= 1 and gap_rate <= MID_STOP_AFTER_1ST:
            self._do_sell(code, remain, date, o, f"갭하락본절({gap_rate:+.2%})")
            return

        # ── MA20 이탈 (1차 익절 후) ────────────────────────────
        if stage >= 1 and ma20 > 0 and o < ma20 * MID_MA20_EXIT_RATE:
            self._do_sell(code, remain, date, o, f"MA20이탈")
            return

        # ── 보유기간 체크 ──────────────────────────────────────
        hold = self._calc_hold_days(buy_date, date)
        if hold >= MID_MAX_HOLD_DAYS and stage < 1:
            self._do_sell(code, remain, date, c, f"시간청산({hold}일)")
            return

        # ── 저가 손절 ─────────────────────────────────────────
        if l > 0:
            low_rate = rate_at(l)
            if low_rate <= MID_STOP_LOSS and stage == 0:
                stop_p = eff_ent * (1 + MID_STOP_LOSS)
                self._do_sell(code, remain, date,
                              max(l, stop_p), f"손절({low_rate:+.2%})")
                return
            if stage >= 1 and low_rate <= MID_STOP_AFTER_1ST:
                self._do_sell(code, remain, date, l, f"본절보호({low_rate:+.2%})")
                return

        # ── 트레일링 스탑 ─────────────────────────────────────
        if h > 0:
            hi_rate = raw_rate(h)
            if hi_rate > peak:
                tracker["peak_rate"] = hi_rate
                peak = hi_rate

            trail = MID_TRAIL_AFTER_1ST if stage == 1 else (
                    MID_TRAIL_AFTER_2ND if stage >= 2 else None)
            if trail and stage >= 1:
                trail_stop = peak - trail
                close_rate = raw_rate(c)
                if close_rate <= trail_stop:
                    self._do_sell(code, remain, date, c,
                                  f"트레일링({close_rate:+.2%}↓{peak:+.2%})")
                    return

        # ── 고가 익절 ─────────────────────────────────────────
        if h > 0 and code in self.positions:
            pos     = self.positions[code]
            entry   = pos["entry_price"]
            qty_now = pos["qty"]

            # 3차 익절 +40%
            if stage >= 2 and raw_rate(h) >= MID_SELL_3RD_RATE:
                target = entry * (1 + MID_SELL_3RD_RATE)
                self._do_sell(code, self.peak_tracker[code]["remain_qty"],
                              date, min(h, target), f"3차익절(+{MID_SELL_3RD_RATE:.0%})")
                if code in self.peak_tracker:
                    self.peak_tracker[code]["stage"] = 3
                return

            # 2차 익절 +25%
            if stage == 1 and raw_rate(h) >= MID_SELL_2ND_RATE:
                target  = entry * (1 + MID_SELL_2ND_RATE)
                remain2 = self.peak_tracker[code]["remain_qty"]
                qty2    = max(1, round(remain2 * MID_SELL_2ND_QTY))
                self._do_sell(code, qty2, date,
                              min(h, target), f"2차익절(+{MID_SELL_2ND_RATE:.0%})")
                if code in self.peak_tracker:
                    self.peak_tracker[code]["stage"] = 2
                    self.peak_tracker[code]["effective_entry"] = (
                        eff_ent * 0.5 + min(h, target) * 0.5)
                return

            # 1차 익절 +15%
            if stage == 0 and raw_rate(h) >= MID_SELL_1ST_RATE:
                target = entry * (1 + MID_SELL_1ST_RATE)
                qty1   = max(1, round(qty_now * MID_SELL_1ST_QTY))
                self._do_sell(code, qty1, date,
                              min(h, target), f"1차익절(+{MID_SELL_1ST_RATE:.0%})")
                if code in self.peak_tracker:
                    self.peak_tracker[code]["stage"] = 1
                return

    # ----------------------------------------------------------
    # 메인 실행
    # ----------------------------------------------------------
    def run(self):
        cfg       = self.config
        codes     = cfg.codes
        dates     = self._get_trading_dates(cfg.start_date, cfg.end_date)

        print(f"\n{'='*60}")
        print(f"🚀 sbot2 백테스트 시작")
        print(f"   기간: {cfg.start_date} ~ {cfg.end_date}")
        print(f"   종목: {len(codes)}개 | 초기자금: {cfg.initial_cash:,}원")
        print(f"   매수금액: {cfg.base_buy_amt:,}원 | 최대종목: {cfg.max_positions}")
        print(f"   매수기준: {cfg.buy_score_min}점 | MA20필터: {cfg.use_ma20_filter}")
        print(f"{'='*60}\n")

        for i, date in enumerate(dates):
            meta   = self._market_meta.get(date, {})
            status = meta.get("status", "normal")

            # 날짜 변경 — 손절 카운터 리셋
            if i > 0 and dates[i-1][:7] != date[:7]:
                self.daily_loss_count = 0

            self._run_one_day(date, status, codes)

            # equity curve
            portfolio_val = self.cash + self._calc_portfolio_value(date)
            self.equity_curve.append((date, portfolio_val))

            if (i + 1) % 50 == 0:
                print(f"  📅 {date} | 자산: {portfolio_val:,.0f}원 "
                      f"| 보유: {len(self.positions)}종목 "
                      f"| 완료거래: {len(self.trades)}건")

        # 미청산 포지션 강제 청산
        last_date = dates[-1] if dates else cfg.end_date
        self._force_close_all(last_date)

        print(f"\n✅ 백테스트 완료: {len(self.trades)}건")

    def _run_one_day(self, date: str, market_status: str, codes: list):
        cfg = self.config

        # 1) 매도 체크 (오늘 OHLC 기준)
        for code in list(self.positions.keys()):
            try:
                ohlcv = get_market_data_at(self.loader, code, date)
                if not ohlcv:
                    continue
                # get_market_data_at 반환 키: stck_oprc/stck_hgpr/stck_lwpr/stck_prpr
                o = float(ohlcv.get("stck_oprc",  ohlcv.get("open_price",  0)))
                h = float(ohlcv.get("stck_hgpr",  ohlcv.get("high_price",  0)))
                l = float(ohlcv.get("stck_lwpr",  ohlcv.get("low_price",   0)))
                c = float(ohlcv.get("stck_prpr",  ohlcv.get("current_price", 0)))
                if o <= 0:
                    continue
                feat = build_features_at(self.loader, code, date)
                ma20 = feat.get("ma20", 0) if feat else 0
                self._check_sell_day(code, date, o, h, l, c, ma20, feat or {})
            except Exception as e:
                if cfg.verbose:
                    print(f"⚠️ 매도체크 오류 {code} {date}: {e}")

        # 2) 매수 (슬롯 있을 때)
        avail = cfg.max_positions - len(self.positions)
        if (avail <= 0
                or self.cash < cfg.base_buy_amt
                or market_status == "stop"
                or self.daily_loss_count >= cfg.max_daily_loss):
            return

        # 후보 선별
        candidates = []
        for code in codes:
            if code in self.positions:
                continue
            try:
                feat = build_features_at(self.loader, code, date)
                if not feat:
                    continue

                # sbot2 특화 필터
                ok, reason = self._passes_mid_filter(code, date, feat)
                if not ok:
                    continue

                # 룰 점수
                score = self._calc_rule_score(feat)
                if score < cfg.buy_score_min:
                    continue

                candidates.append((code, score, feat))
            except Exception as e:
                if cfg.verbose:
                    print(f"⚠️ 분석 오류 {code}: {e}")

        candidates.sort(key=lambda x: x[1], reverse=True)

        # T+1 매수 (내일 시가)
        next_dates = self._get_trading_dates(date, cfg.end_date)
        if len(next_dates) < 2:
            return
        next_date = next_dates[1]

        # 날짜 변경 시 sold_today 리셋
        if next_date != self._last_date:
            self.sold_today = {}
            self._last_date = next_date

        for code, score, feat in candidates:
            if avail <= 0 or self.cash < cfg.base_buy_amt:
                break
            # ★ 당일 매도 종목 재매수 방지
            if code in self.sold_today:
                continue
            try:
                next_ohlcv = get_market_data_at(self.loader, code, next_date)
                if not next_ohlcv:
                    continue
                next_open = float(next_ohlcv.get("stck_oprc", next_ohlcv.get("open_price", next_ohlcv.get("open", 0))))
                if next_open <= 0:
                    continue
                # ★ 갭하락 -3% 이상이면 매수 스킵 (feat의 current_price = 전일 종가)
                cur_close = float(feat.get("current_price", feat.get("close", 0)))
                if cur_close > 0 and next_open > 0:
                    gap = (next_open - cur_close) / cur_close
                    if gap <= -0.03:
                        if cfg.verbose:
                            print(f"  ⏭️ {code} 갭하락 스킵 ({gap:+.1%})")
                        continue
                if self._do_buy(code, next_date, next_open, score, feat):
                    avail -= 1
            except Exception as e:
                if cfg.verbose:
                    print(f"⚠️ 매수 오류 {code}: {e}")

    def _calc_rule_score(self, features: dict) -> int:
        """features dict → 룰 점수 (sbot2_strategy 핵심 로직 반영)"""
        score = 30
        ma5   = features.get("ma5",   0)
        ma20  = features.get("ma20",  0)
        ma60  = features.get("ma60",  0)
        ma120 = features.get("ma120", 0)
        cur   = features.get("current_price", features.get("close", 0))
        rsi   = features.get("rsi",   50)
        tvol  = features.get("trading_value", 0)
        foreign5 = features.get("foreign_5d", 0)
        orgn5    = features.get("institution_5d", 0)
        high_52w = features.get("high_52w", 0)
        vol_ratio = features.get("volume_ratio", 0)
        bb_width  = features.get("bb_width", 0)
        roe       = features.get("roe",    0)
        op_yoy    = features.get("op_yoy", 0)

        # MA 배열/추세
        if ma5 > 0 and ma20 > 0 and ma60 > 0:
            if ma5 > ma20 > ma60:
                score += 15
                if ma120 > 0 and ma60 > ma120:
                    score += 5
            elif ma5 > ma20:
                score += 8
            elif cur > ma20:
                score += 4

        # 눌림목
        if high_52w > 0 and cur > 0:
            pb = (cur - high_52w) / high_52w
            if -0.25 <= pb <= -0.10:
                score += 10
            elif pb >= -0.05:
                score += 6

        # VCP
        if vol_ratio > 0:
            if 0.5 <= vol_ratio <= 0.8 and bb_width < 0.04:
                score += 12
            elif vol_ratio >= 1.5 and bb_width > 0.05:
                score += 8

        # 실적 턴어라운드
        if roe > 0 and op_yoy >= 50:
            score += 15
        elif roe > 0 and op_yoy >= 20:
            score += 8

        # 수급
        if foreign5 > 0 and orgn5 > 0:
            score += 10
        elif foreign5 > 0:
            score += 6
        elif orgn5 > 0:
            score += 4

        # 거래대금
        if   tvol >= 500: score += 8
        elif tvol >= 200: score += 5
        elif tvol >= 100: score += 3

        # RSI
        if   40 <= rsi <= 55: score += 8
        elif 55 < rsi <= 65:  score += 5
        elif rsi > 75:        score -= 5

        return min(100, max(0, score))

    def _calc_portfolio_value(self, date: str) -> float:
        total = 0.0
        for code, pos in self.positions.items():
            try:
                ohlcv = get_market_data_at(self.loader, code, date)
                if ohlcv:
                    c = float(ohlcv.get("stck_prpr", ohlcv.get("current_price", ohlcv.get("close", pos["entry_price"]))))
                    total += c * pos["qty"]
                else:
                    total += pos["entry_price"] * pos["qty"]
            except Exception:
                total += pos["entry_price"] * pos["qty"]
        return total

    def _force_close_all(self, date: str):
        for code in list(self.positions.keys()):
            pos = self.positions[code]
            try:
                ohlcv = get_market_data_at(self.loader, code, date)
                price = float(ohlcv.get("close", pos["entry_price"])) if ohlcv else pos["entry_price"]
            except Exception:
                price = pos["entry_price"]
            self._do_sell(code, pos["qty"], date, price, "백테스트종료청산")

    def _get_trading_dates(self, start: str, end: str) -> list:
        try:
            conn  = sqlite3.connect(self.db_path, timeout=10)
            rows  = conn.execute("""
                SELECT DISTINCT date FROM daily_ohlcv
                WHERE date >= ? AND date <= ?
                ORDER BY date
            """, (start, end)).fetchall()
            conn.close()
            return [r[0] for r in rows]
        except Exception as e:
            print(f"⚠️ 날짜 조회 오류: {e}")
            return []

    # ----------------------------------------------------------
    # 결과 리포트
    # ----------------------------------------------------------
    def get_trades(self) -> list:
        return [t.to_dict() for t in self.trades]

    def get_report(self) -> dict:
        trades = self.trades
        if not trades:
            return {"total_trades": 0, "message": "거래 없음"}

        profits     = [t.profit_rate * 100 for t in trades]  # % 단위로 변환
        wins        = [p for p in profits if p > 0]
        losses      = [p for p in profits if p <= 0]
        win_rate    = len(wins) / len(profits) * 100 if profits else 0
        avg_profit  = sum(profits) / len(profits) if profits else 0
        profit_factor = (sum(wins) / abs(sum(losses))
                         if losses and sum(losses) != 0 else 999)

        final_cash  = self.cash + sum(
            self.equity_curve[-1][1] - self.config.initial_cash
            for _ in [1] if self.equity_curve
        )
        total_return = (self.equity_curve[-1][1] / self.config.initial_cash - 1) * 100 \
                       if self.equity_curve else 0

        # MDD
        mdd = 0.0
        peak_val = self.config.initial_cash
        for _, val in self.equity_curve:
            if val > peak_val:
                peak_val = val
            dd = (val - peak_val) / peak_val
            if dd < mdd:
                mdd = dd

        # 평균 보유일
        avg_hold = sum(t.hold_days for t in trades) / len(trades) if trades else 0

        # 매도 사유 분포
        reasons = {}
        for t in trades:
            r = t.sell_reason.split("(")[0]
            reasons[r] = reasons.get(r, 0) + 1

        report = {
            "total_trades":    len(trades),
            "win_rate":        round(win_rate, 2),
            "avg_profit_pct":  round(avg_profit, 2),
            "total_return_pct":round(total_return, 2),
            "max_drawdown_pct":round(mdd * 100, 2),
            "profit_factor":   round(profit_factor, 2),
            "avg_hold_days":   round(avg_hold, 1),
            "sell_reasons":    reasons,
            "equity_curve":    self.equity_curve,
            "trades":          self.get_trades(),
        }

        print(f"\n{'='*60}")
        print(f"📊 sbot2 백테스트 결과")
        print(f"   총 거래: {report['total_trades']}건")
        print(f"   승률:    {report['win_rate']:.1f}%")
        print(f"   평균수익: {report['avg_profit_pct']:+.2f}%")
        print(f"   총수익률: {report['total_return_pct']:+.2f}%")
        print(f"   MDD:     {report['max_drawdown_pct']:.2f}%")
        print(f"   PF:      {report['profit_factor']:.2f}")
        print(f"   평균보유: {report['avg_hold_days']:.1f}일")
        print(f"   매도사유: {reasons}")
        print(f"{'='*60}\n")

        return report
