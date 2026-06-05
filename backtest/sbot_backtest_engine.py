"""
sbot_backtest_engine.py — 스윙봇 백테스트 엔진
================================================================
[nbot 백테스터와의 핵심 차이]

항목            nbot                    sbot
─────────────────────────────────────────────────────
익절선          +5% / +10% / +15%       +8% / +15% / +25%
손절선          -5%                     -7% (1차 후 본절 -3%)
트레일링        고점 -2.5% / -2%        고점 -4% / -3%
보유기간        당일 종가매도            최대 11영업일 (초과시 청산)
종목 대상       소/중형주 (500~5만억)    중대형주 (1조~)
매수금액        20~30만원               50만원~
매수시점        T+1 시가               T+1 시가 (동일)
매도체크        4시점(O/L/H/C)          4시점(O/L/H/C) + 장기보유
market_status   동적 반영               "normal" 고정 (조기손절 방지)

[사용법]
  from sbot_backtest_engine import SBotBacktestEngine, SBotBacktestConfig
  cfg = SBotBacktestConfig(...)
  engine = SBotBacktestEngine(cfg, db_path)
  engine.run()
  trades = engine.get_trades()
"""
import os
import sys
import sqlite3
import datetime
from dataclasses import dataclass, field, asdict
from typing import Optional

import pandas as pd

# 경로 탐색 — core/(sbot_strategy) + backtest/(feature_builder) 동시 등록
# 우선순위:
#   1. 환경변수 K_BOT_ROOT  → stock_bot 루트
#   2. 부모 디렉토리
#   3. /home/free4tak/k-bot/stock_bot (실서버 기본 경로)
_stock_bot_root = None
_candidates = [
    os.environ.get("K_BOT_ROOT"),
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..")),
    "/home/free4tak/k-bot/stock_bot",
    "/mnt/project",
]
for _root in _candidates:
    if _root and os.path.exists(os.path.join(_root, "core", "sbot_strategy.py")):
        _stock_bot_root = _root
        break
    # backtest 디렉토리 안에서 실행될 경우
    if _root and os.path.exists(os.path.join(_root, "sbot_strategy.py")):
        _stock_bot_root = os.path.dirname(_root)
        break

if _stock_bot_root:
    for _sub in ["core", "intelligence", "bots", ""]:
        _p = os.path.join(_stock_bot_root, _sub)
        if os.path.isdir(_p) and _p not in sys.path:
            sys.path.insert(0, _p)
    PROJECT_ROOT = _stock_bot_root
    print(f"✅ sbot_backtest_engine: PROJECT_ROOT={_stock_bot_root}")
else:
    # 폴백 — strategy.py 기반 (sbot_strategy 없는 환경)
    for _root in _candidates:
        if _root and os.path.exists(os.path.join(_root, "strategy.py")):
            if _root not in sys.path:
                sys.path.insert(0, _root)
            PROJECT_ROOT = _root
            break
    else:
        raise ImportError(
            "sbot_strategy.py / strategy.py 위치를 찾을 수 없음\n"
            "K_BOT_ROOT 환경변수로 stock_bot 루트 경로 지정 필요\n"
            "예: export K_BOT_ROOT=/home/free4tak/k-bot/stock_bot"
        )

# SwingStrategy 우선 — 없으면 Strategy 폴백
try:
    from sbot_strategy import SwingStrategy as _StrategyClass
    _USING_SWING_STRATEGY = True
    print("✅ SwingStrategy (sbot_strategy.py) 로드")
except ImportError:
    from strategy import Strategy as _StrategyClass
    _USING_SWING_STRATEGY = False
    print("⚠️ SwingStrategy 없음 → Strategy(nbot) 폴백 (매도 기준 다름 주의)")

from risk_manager    import RiskManager
from feature_builder import DataLoader, build_features_at, get_market_data_at


# ============================================================
# 스윙봇 매도 기준 (sbot.py 실전과 동일)
# ============================================================
SWING_SELL_1ST_RATE  = 0.08    # 1차 익절: +8%
SWING_SELL_1ST_QTY   = 0.40    # → 40% 매도
SWING_SELL_2ND_RATE  = 0.15    # 2차 익절: +15%
SWING_SELL_2ND_QTY   = 0.40    # → 잔량의 40%
SWING_SELL_3RD_RATE  = 0.25    # 3차 익절: +25% → 전량

SWING_STOP_LOSS      = -0.07   # 기본 손절: -7%
SWING_STOP_AFTER_1ST = -0.03   # 1차 익절 후 본절 보호: -3%

SWING_TRAIL_AFTER_1ST = 0.04   # 1차 후 트레일링: 고점 -4%
SWING_TRAIL_AFTER_2ND = 0.03   # 2차 후 트레일링: 고점 -3%

SWING_MAX_HOLD_DAYS  = 11      # 최대 보유 영업일
SWING_FORCE_SELL_RATE = 0.02   # 장기보유 강제청산 기준 (수익률 +2% 이하)


# ============================================================
# 거래 기록
# ============================================================
@dataclass
class SwingTrade:
    code:        str
    buy_date:    str
    buy_price:   float
    qty:         int
    sell_date:   str   = ""
    sell_price:  float = 0.0
    sell_reason: str   = ""
    profit_rate: float = 0.0
    profit_krw:  float = 0.0
    fee:         float = 0.0
    score:       int   = 0
    hold_days:   int   = 0    # 실제 보유 영업일

    def to_dict(self) -> dict:
        return asdict(self)


# ============================================================
# 백테스트 설정
# ============================================================
@dataclass
class SBotBacktestConfig:
    # 자본 (sbot 실전: 종목당 50만~200만원)
    initial_cash:      int   = 10_000_000
    base_buy_amt:      int   = 500_000
    max_positions:     int   = 2
    # 매수 임계치
    buy_score_min:     int   = 65
    # 종목 필터
    # ★ 백테스터 DB에 시총(hts_avls) 없음 → 거래대금으로 대형주 필터
    #   삼성전자 일평균 거래대금 ~5,000억 → 100억 이상이면 중대형주
    min_trading_value: int   = 100   # 억원 단위 (100억 이상)
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


# ============================================================
# 스윙봇 백테스트 엔진
# ============================================================
class SBotBacktestEngine:
    """
    스윙봇 전용 백테스트 엔진.
    nbot 엔진과 공유 데이터(daily_ohlcv DB)를 사용하되
    매도 기준/보유기간/종목 필터가 다름.
    """

    def __init__(self, config: SBotBacktestConfig, db_path: str):
        self.config       = config
        self.db_path      = db_path
        self.loader       = DataLoader(db_path)
        self._market_meta = self._load_market_meta(db_path)
        self.strategy     = _StrategyClass()   # SwingStrategy 또는 Strategy 폴백
        self._using_swing = _USING_SWING_STRATEGY
        self.risk_manager = RiskManager(
            base_buy_amt=config.base_buy_amt,
            max_daily_loss_pct=0.03,
            max_daily_loss_count=config.max_daily_loss,
        )

        # 상태
        self.cash             = config.initial_cash
        self.positions        = {}   # code → {entry_price, qty, buy_date, score}
        self.open_trades      = {}   # code → SwingTrade
        self.trades           = []   # 완료 거래
        self.peak_tracker     = {}   # 고점 추적
        self.buy_tags         = {}
        self.daily_loss_count = 0
        self.daily_loss_amt   = 0
        self.equity_curve     = []

    # ----------------------------------------------------------
    # 가상 체결
    # ----------------------------------------------------------
    def _simulate_buy(self, code: str, price: float, qty: int,
                      date: str, score: int = 0) -> bool:
        fill_price = price * (1 + self.config.slippage)
        cost  = fill_price * qty
        fee   = cost * self.config.fee_rate
        total = cost + fee

        if total > self.cash:
            return False

        self.cash -= total
        self.positions[code] = {
            "entry_price": fill_price,
            "qty":         qty,
            "buy_date":    date,
            "score":       score,
        }
        self.peak_tracker[code] = {
            "peak_rate":       0.0,
            "stage":           0,
            "remain_qty":      qty,
            "buy2_done":       True,   # 스윙은 2차 매수 없음
            "buy1_price":      fill_price,
            "effective_entry": fill_price,
        }
        self.open_trades[code] = SwingTrade(
            code=code, buy_date=date,
            buy_price=fill_price, qty=qty, score=score,
        )
        if self.config.verbose:
            print(f"   🟢 매수 {code} {qty}주 @ {fill_price:,.0f} | {score}점")
        return True

    def _simulate_sell(self, code: str, qty: int, price: float,
                       reason: str, date: str):
        if code not in self.positions:
            return

        fill_price = price * (1 - self.config.slippage)
        revenue    = fill_price * qty
        fee        = revenue * self.config.fee_rate
        tax        = revenue * self.config.tax_rate
        net        = revenue - fee - tax

        self.cash += net

        pos        = self.positions[code]
        entry      = pos["entry_price"]
        pos_qty    = pos["qty"]
        profit_krw = net - entry * qty
        profit_rate = (fill_price - entry) / entry

        ot = self.open_trades.get(code)
        if ot:
            ot.sell_date   = date
            ot.sell_price  = fill_price
            ot.sell_reason = reason
            ot.profit_rate = profit_rate
            ot.profit_krw += profit_krw
            ot.fee        += fee + tax

        if qty >= pos_qty:
            # 전량 매도 — 보유 영업일 계산
            if ot:
                ot.hold_days = self._calc_hold_days(pos["buy_date"], date)
                self.trades.append(ot)
            del self.open_trades[code]
            del self.positions[code]
            self.peak_tracker.pop(code, None)
            self.buy_tags.pop(code, None)
        else:
            pos["qty"] -= qty
            # ★ 버그수정: 부분 매도 시 peak_tracker 잔량 동기화
            if code in self.peak_tracker:
                self.peak_tracker[code]["remain_qty"] = pos["qty"]

        if profit_rate < 0 and qty >= pos_qty:
            self.daily_loss_count += 1
            self.daily_loss_amt   += profit_krw

        if self.config.verbose:
            print(f"   🔴 매도 {code} {qty}주 @ {fill_price:,.0f} "
                  f"({profit_rate:+.2%}) | {reason}")

    def _on_loss(self):
        pass  # _simulate_sell에서 카운트

    # ----------------------------------------------------------
    # 스윙 매도 체크 (nbot과 분리된 핵심 로직)
    # ----------------------------------------------------------
    def _check_swing_sell(self, code: str, pos: dict, date_str: str,
                          market_data: dict, now_t: str):
        """
        스윙봇 전용 매도 판단.
        strategy.check_sell 대신 스윙 기준(+8/+15/+25%, -7%)을 직접 적용.
        """
        entry   = pos["entry_price"]
        qty     = pos["qty"]
        current = float(market_data.get("stck_prpr", 0))
        if current <= 0 or entry <= 0:
            return

        rate    = (current - entry) / entry
        tracker = self.peak_tracker.get(code, {
            "peak_rate": 0.0, "stage": 0,
            "remain_qty": qty, "buy2_done": True,
            "buy1_price": entry, "effective_entry": entry,
        })
        self.peak_tracker[code] = tracker
        stage = tracker.get("stage", 0)

        # 고점 갱신
        if rate > tracker["peak_rate"]:
            tracker["peak_rate"] = rate

        peak = tracker["peak_rate"]

        # ── 종가매도 시간 (1515) ───────────────────────────
        # 스윙은 종가매도 없음 (다일 보유)

        # ── 트레일링 스탑 ─────────────────────────────────
        if stage >= 2 and peak > SWING_SELL_2ND_RATE:
            trail = peak - SWING_TRAIL_AFTER_2ND
            if rate <= trail:
                self._simulate_sell(code, qty, current,
                                    f"트레일링2차({rate:+.2%})", date_str)
                return
        elif stage >= 1 and peak > SWING_SELL_1ST_RATE:
            trail = peak - SWING_TRAIL_AFTER_1ST
            if rate <= trail:
                self._simulate_sell(code, qty, current,
                                    f"트레일링1차({rate:+.2%})", date_str)
                return

        # ── 3차 익절 +25% ────────────────────────────────
        if stage < 3 and rate >= SWING_SELL_3RD_RATE:
            self._simulate_sell(code, qty, current,
                                f"3차익절({rate:+.2%})", date_str)
            tracker["stage"] = 3
            return

        # ── 2차 익절 +15% ────────────────────────────────
        if stage < 2 and rate >= SWING_SELL_2ND_RATE:
            sell_qty = max(int(qty * SWING_SELL_2ND_QTY), 1)
            sell_qty = min(sell_qty, qty)
            self._simulate_sell(code, sell_qty, current,
                                f"2차익절({rate:+.2%})", date_str)
            tracker["stage"] = 2
            return

        # ── 1차 익절 +8% ─────────────────────────────────
        if stage < 1 and rate >= SWING_SELL_1ST_RATE:
            sell_qty = max(int(qty * SWING_SELL_1ST_QTY), 1)
            sell_qty = min(sell_qty, qty)
            self._simulate_sell(code, sell_qty, current,
                                f"1차익절({rate:+.2%})", date_str)
            tracker["stage"] = 1
            # effective_entry 보정
            realized_gain = (current - entry) * sell_qty
            tracker["effective_entry"] = max(
                entry - realized_gain / max(qty - sell_qty, 1),
                entry * 0.97,
            )
            return

        # ── 손절 ─────────────────────────────────────────
        if stage >= 1:
            stop = SWING_STOP_AFTER_1ST   # -3% 본절 보호
            label = "본절보호"
        else:
            stop  = SWING_STOP_LOSS       # -7%
            label = "손절"

        if rate <= stop:
            self._simulate_sell(code, qty, current,
                                f"{label}({rate:+.2%})", date_str)
            self._on_loss()
            return

    # ----------------------------------------------------------
    # 장기 보유 청산 (11영업일 초과 + 수익 +2% 이하)
    # ----------------------------------------------------------
    def _check_over_hold(self, code: str, pos: dict,
                         date_str: str, market_data: dict):
        hold_days = self._calc_hold_days(pos["buy_date"], date_str)
        if hold_days < SWING_MAX_HOLD_DAYS:
            return

        current = float(market_data.get("stck_prpr", 0))
        entry   = pos["entry_price"]
        if current <= 0 or entry <= 0:
            return

        rate = (current - entry) / entry
        if rate <= SWING_FORCE_SELL_RATE:
            self._simulate_sell(
                code, pos["qty"], current,
                f"장기보유청산({rate:+.2%},{hold_days}일)", date_str,
            )
            if self.config.verbose:
                print(f"📅 {code} {hold_days}영업일 초과 → 장기보유청산 ({rate:+.2%})")

    # ----------------------------------------------------------
    # 하루 시뮬레이션
    # ----------------------------------------------------------
    def _replay_day(self, date: pd.Timestamp):
        date_str = date.strftime("%Y-%m-%d")

        # 일일 손실 카운터 리셋
        self.daily_loss_count = 0
        self.daily_loss_amt   = 0

        # ★ sbot: market_status "normal" 고정
        # (약세장 손절선 -3% 축소 방지 → 원래 손절선 -7% 유지)
        day_market_rate, _ = self._get_market_status_at(date)

        # ── ① 매도 체크 ──────────────────────────────────
        for code in list(self.positions.keys()):
            if code not in self.positions:
                continue
            pos = self.positions[code]

            # ATR / MA20 — DB 컬럼 우선, 없으면 features에서 보완
            df       = self.loader.load_ohlcv(code)
            atr_rate = 0.0
            ma20     = 0.0
            if not df.empty and date in df.index:
                _row = df.loc[date]
                if hasattr(_row, "columns"): _row = _row.iloc[-1]
                try:
                    atr14 = float(_row.get("atr14", 0) or 0)
                    if atr14 > 0 and pos["entry_price"] > 0:
                        atr_rate = atr14 / pos["entry_price"]
                    ma20 = float(_row.get("ma20", 0) or 0)
                except Exception:
                    pass
            # ★ DB 컬럼에 없으면 feature_builder 결과에서 보완
            if ma20 == 0:
                feat_now = build_features_at(self.loader, code, date)
                if feat_now:
                    ma20     = float(feat_now.get("ma20", 0) or 0)
                    atr_rate = atr_rate or float(feat_now.get("atr_rate", 0) or 0)

            for px_type, _ in [
                ("open",  "0900"),
                ("low",   "1000"),
                ("high",  "1200"),
                ("close", "1500"),
            ]:
                if code not in self.positions:
                    break
                mdata = get_market_data_at(
                    self.loader, code, date, price_type=px_type)
                if not mdata:
                    continue

                if self._using_swing:
                    # ★ SwingStrategy.check_sell 시그니처 (sbot.py 실전과 동일)
                    # px_type → now_t 매핑 (시간대별 매도 조건 반영)
                    time_map = {"open": "0900", "low": "1000",
                                "high": "1200", "close": "1500"}
                    now_t_bt = time_map.get(px_type, "1200")
                    self.strategy.check_sell(
                        code, pos, mdata, "normal",
                        self.peak_tracker, False,
                        lambda c, p, a: None,   # 2차 매수 없음 (백테스트)
                        lambda c, q, r, p: self._simulate_sell(c, q, p, r, date_str),
                        self._on_loss,
                        now_t=now_t_bt,
                        ma20=ma20, atr_rate=atr_rate,
                    )
                else:
                    # 폴백: 자체 스윙 매도 로직
                    self._check_swing_sell(code, pos, date_str, mdata, "1200")

            # 장기 보유 청산 체크 (종가 기준)
            if code in self.positions:
                mdata_close = get_market_data_at(
                    self.loader, code, date, price_type="close")
                if mdata_close:
                    self._check_over_hold(code, pos, date_str, mdata_close)

        self._record_equity(date)

        # ── ② 매수 후보 평가 ─────────────────────────────
        if len(self.positions) >= self.config.max_positions:
            return

        stop, _ = self.risk_manager.should_stop_trading(
            self.daily_loss_count, self.daily_loss_amt)
        if stop:
            return

        candidates = []
        filter_log = {"no_features": 0, "value_filter": 0,
                      "buy_filter": 0, "score_low": 0, "pass": 0}

        for code in self.config.codes:
            if code in self.positions:
                continue

            features = build_features_at(self.loader, code, date)
            if not features:
                filter_log["no_features"] += 1
                continue

            # ★ 시총(hts_avls)은 백테스터 DB에 없음 → 거래대금으로 대체
            # sbot 대상: 거래대금 100억 이상 (중대형주 실질 필터)
            trading_value = features.get("trading_value", 0)  # 억원 단위
            if trading_value < self.config.min_trading_value:
                filter_log["value_filter"] += 1
                continue

            # 매수 필터
            if self._using_swing:
                ok, reason = self.strategy.passes_buy_filter(features, is_new=False)
            else:
                ok, reason = self.strategy.passes_buy_filter(features, is_sector_match=False)
            if not ok:
                filter_log["buy_filter"] += 1
                if self.config.verbose:
                    print(f"   필터제외 {code}: {reason}")
                continue

            # 룰 점수
            rule_score = self.strategy.get_rule_score(features)

            # AI 점수
            if self.config.ai_score_mode == "fixed":
                ai_score = self.config.ai_score_fixed
            elif self.config.ai_score_mode == "cache":
                ai_score = self._get_cached_ai_score(
                    code, date, features.get("current_price", 0))
                if ai_score is None:
                    ai_score = rule_score
            else:  # rule_proxy
                ai_score = rule_score

            base_score = (rule_score + ai_score) // 2
            if self.config.verbose:
                print(f"   {code} | 거래대금:{trading_value:.0f}억 | "
                      f"룰:{rule_score} AI:{ai_score} → 최종:{base_score}점")

            if base_score >= self.config.buy_score_min:
                filter_log["pass"] += 1
                candidates.append((base_score, code, features))
            else:
                filter_log["score_low"] += 1

        if self.config.verbose and any(v > 0 for v in filter_log.values()):
            print(f"   [{date_str}] 필터결과: "
                  + " | ".join(f"{k}:{v}" for k, v in filter_log.items() if v > 0))

        # 점수 높은 순 매수
        candidates.sort(reverse=True)
        for score, code, features in candidates:
            if len(self.positions) >= self.config.max_positions:
                break

            # T+1 시가 매수
            df = self.loader.load_ohlcv(code)
            future = df[df.index > date]
            if future.empty:
                continue
            next_day = future.index[0]
            next_row = future.iloc[0]
            buy_price = float(next_row.get("open", next_row.get("close", 0)))
            if buy_price <= 0:
                continue

            qty = max(int(self.config.base_buy_amt / buy_price), 1)
            if qty * buy_price * (1 + self.config.slippage) > self.cash:
                continue

            self._simulate_buy(
                code, buy_price, qty,
                next_day.strftime("%Y-%m-%d"), score,
            )

    # ----------------------------------------------------------
    # 보조 메서드
    # ----------------------------------------------------------
    def _calc_hold_days(self, buy_date_str: str, sell_date_str: str) -> int:
        """매수→매도 사이 영업일 수 계산"""
        try:
            buy  = datetime.datetime.strptime(buy_date_str,  "%Y-%m-%d").date()
            sell = datetime.datetime.strptime(sell_date_str, "%Y-%m-%d").date()
            days = 0
            cur  = buy
            while cur < sell:
                cur += datetime.timedelta(days=1)
                if cur.weekday() < 5:
                    days += 1
            return days
        except Exception:
            return 0

    def _load_market_meta(self, db_path: str) -> dict:
        result = {}
        try:
            conn = sqlite3.connect(db_path, timeout=5)
            rows = conn.execute(
                "SELECT date, kospi, market_status FROM market_meta"
            ).fetchall()
            for d, kospi, status in rows:
                result[d] = {"kospi": kospi, "status": status}
            conn.close()
            print(f"✅ market_meta 로드: {len(result)}일치")
        except Exception as e:
            print(f"⚠️ market_meta 로드 실패: {e}")
        return result

    def _get_market_status_at(self, date) -> tuple:
        date_str = date.strftime("%Y-%m-%d")
        meta = self._market_meta.get(date_str, {})
        return meta.get("kospi", 0.0), meta.get("status", "normal")

    def _get_cached_ai_score(self, code: str, date,
                              current_price: float = 0):
        ai_db = os.path.normpath(
            os.path.join(os.path.dirname(self.db_path), "..", "ai_cache.db"))
        if not os.path.exists(ai_db):
            return None
        try:
            conn = sqlite3.connect(ai_db, timeout=5)
            row  = conn.execute("""
                SELECT score, cached_price FROM ai_analysis
                WHERE code = ? AND analyzed_at LIKE ?
                ORDER BY analyzed_at DESC LIMIT 1
            """, (code, f"{date.strftime('%Y-%m-%d')}%")).fetchone()
            conn.close()
            if not row:
                return None
            score, cached_price = row
            if cached_price and current_price > 0:
                if abs(current_price - cached_price) / cached_price > 0.10:
                    return None
            return int(score)
        except Exception:
            return None

    def _record_equity(self, date: pd.Timestamp):
        market_value = 0
        for code, pos in self.positions.items():
            df = self.loader.load_ohlcv(code)
            if not df.empty and date in df.index:
                row = df.loc[date]
                if hasattr(row, "columns"):
                    row = row.iloc[-1]
                market_value += float(row["close"]) * pos["qty"]
            else:
                market_value += pos["entry_price"] * pos["qty"]
        self.equity_curve.append((date.strftime("%Y-%m-%d"),
                                   self.cash + market_value))

    # ----------------------------------------------------------
    # 메인 실행
    # ----------------------------------------------------------
    def run(self):
        all_dates = set()
        for code in self.config.codes:
            df = self.loader.load_ohlcv(code)
            if not df.empty:
                all_dates.update(df.index)

        start = pd.to_datetime(self.config.start_date)
        end   = pd.to_datetime(self.config.end_date)
        dates = sorted(d for d in all_dates if start <= d <= end)

        if not dates:
            print("⚠️ 거래일 없음 — 데이터 확인 필요")
            return

        print(f"🚀 [SBOT] 백테스트: {self.config.start_date} ~ "
              f"{self.config.end_date} | {len(dates)}일 | "
              f"{len(self.config.codes)}종목")

        for i, date in enumerate(dates, 1):
            try:
                self._replay_day(date)
            except Exception as e:
                print(f"⚠️ {date.strftime('%Y-%m-%d')} 오류: {e}")
                if self.config.verbose:
                    import traceback; traceback.print_exc()
            if i % 50 == 0:
                print(f"   진행 {i}/{len(dates)} | 보유 {len(self.positions)}개 | "
                      f"현금 {self.cash:,.0f} | 완료 {len(self.trades)}건")

        # 잔여 포지션 강제 청산
        last_date = dates[-1]
        last_str  = last_date.strftime("%Y-%m-%d")
        for code in list(self.positions.keys()):
            df = self.loader.load_ohlcv(code)
            if not df.empty and last_date in df.index:
                row = df.loc[last_date]
                if hasattr(row, "columns"):
                    row = row.iloc[-1]
                self._simulate_sell(
                    code, self.positions[code]["qty"],
                    float(row["close"]), "백테스트종료", last_str)

        print(f"\n✅ [SBOT] 완료 — {len(self.trades)}건, "
              f"최종현금 {self.cash:,.0f}원")

    def get_trades(self)      -> list: return [t.to_dict() for t in self.trades]
    def get_equity_curve(self) -> list: return self.equity_curve
