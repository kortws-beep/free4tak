"""
cbot_backtest_engine.py — 코인봇(cbot) 백테스트 엔진
================================================================
[설계 원칙]
sbot_backtest_engine.py와 동일한 패턴으로 구성.
cbot.py 운영 로직(ATR 추세추종 + 25일기한 + 목표1 50%매도)을 그대로 재현.

[단순화한 부분 — 4시간봉 데이터(약 33일)만으로는 재현 불가능한 요소]
- AI 점수(Claude API) → 룰 점수로 대체 (ai_score_mode="rule_proxy")
- BTC 시장상태/공포탐욕지수 → 데이터 없어 "normal"/50 고정
- 1시간봉 보조 확인(멀티타임프레임) → 생략
- 알트코인 동시보유 한도 → 생략
- 야간 매수 제한 → 생략

[검증 핵심 — 그대로 재현, 2026-08-17 실전값 재동기화]
- MA5 > MA20 (정배열) / RSI 40~79 / 거래량 ≥ 20봉 평균 × 1.2배 / 현재가 > MA20
- ATR 기반 손절(ATR×2)/목표(ATR×3) 산정, ATR 미사용 시 폴백 -7%/+15%
- 목표1 달성 → 50% 매도 + 손절 상향(ATR×1) + 목표 재설정(ATR×3)
- 목표2+ 달성 → 손절을 직전 목표가로, 목표 재설정
- 트레일링 스탑 (목표1 달성 이후, 고점 대비 ATR×1.5)
- 25일(캘린더일) 보유기한 초과 시 강제 청산 (stage==0 한정)
- 매수금액 100만원(★2026-08-17: 40만→100만, 08-07 실전 변경) / 시딩 300만원
  ("300만원 모드") / 최대 3코인 — 예전엔 40만원×1000만원 기준으로 어긋나
  있었음(정비 전 실제로는 늘 700만원이 놀아 수익률이 실제보다 희석됨)

[사용법]
  from cbot_backtest_engine import CBotBacktestEngine, CBotBacktestConfig
  cfg = CBotBacktestConfig(codes=["KRW-BTC", "KRW-ETH"])
  engine = CBotBacktestEngine(cfg, db_path="backtestc/coin_backtest.db")
  engine.run()
  trades = engine.get_trades()
"""
import os
import sqlite3
import datetime
from dataclasses import dataclass, field, asdict
from typing import Optional

import pandas as pd
import numpy as np


# ============================================================
# ATR 배수 (cbot.py 운영값과 동일하게 유지)
# ============================================================
ATR_STOP_MULT   = 2.0
ATR_TARGET_MULT = 3.0
ATR_RAISE_MULT  = 1.0
ATR_TRAIL_MULT  = 1.5

# ★ 2026-09-03: "%하드 트레일링 앙상블" 실험(사용자 제안) — 급등 시 ATR도
#   같이 커져서 트레일링이 너무 느슨해지는(고점 대비 수익 반납이 큰) 문제
#   완화. 누적 최고수익(peak_rate)이 일정 % 이상 찍힌 뒤부터는 ATR
#   트레일링과 %기반 트레일링 중 더 타이트한(먼저 도달하는) 쪽을 적용.
PCT_TRAIL_PROFIT_THRESHOLD = 0.15   # 이 이상 찍어야 % 트레일링 활성화
PCT_TRAIL_PCT               = 0.045  # 고점 대비 -4.5%

FALLBACK_STOP   = -0.07
# ★ 2026-08-17: 0.12 → 0.15 (cbot.py FALLBACK_TARGET과 불일치했음 — 정비 중 발견)
FALLBACK_TARGET = 0.15

MIN_ORDER_AMT = 5_000  # 업비트 최소 주문금액 근사치

# ★ 2026-08-17: cbot.py 매수필터 값과 정비 — RSI_MAX/VOL_MULT가 옛날 값으로
#   하드코딩돼 있어 실전과 어긋났음(RSI_MAX 70→79, VOL_MULT 1.3→1.2 둘 다
#   실전에서 완화된 이후 백테스터에 반영 안 됨)
RSI_MIN  = 40
RSI_MAX  = 79
VOL_MULT = 1.2
STOP_LOSS_CRASH = -0.05   # 직전봉 급락 즉시손절 기준 (주간 기준 — 4시간봉 백테스트는 야간 -0.08 예외 미반영)

# ★ 2026-08-30: "정체 로테이션" 실험(사용자 아이디어) — 손실 중인 포지션은
#   기존 손절/25일기한 로직이 이미 커버하므로 건드리지 않고, 수익 중이지만
#   목표1(stage 0)도 못 찍고 24시간 넘게 2% 미만에서 정체된 포지션만 대상.
#   1차 실험(무조건 시간+수익폭으로 매도)은 백테스트 결과 승리 크기를
#   갉아먹어 역효과였음 — 2차 개선: (1) 거래량까지 봐서 "매수/매도가
#   치열하게 싸우는 중"(거래량 살아있음)인지 "관심밖이라 거래량도 마른
#   것"인지 구분하고, (2) 무조건 매도가 아니라 "실제로 더 점수 높은
#   대안이 대기 중일 때만" 교체(사용자 요청) — CBotBacktestConfig.
#   enable_stagnant_rotation로 on/off.
STAGNANT_HOURS         = 24
STAGNANT_PROFIT_CAP    = 0.02   # 2% 미만(0~1.999999%)
STAGNANT_VOL_RATIO_MAX = 1.0    # 최근 거래량이 20봉 평균 밑이면 "관심밖"으로 판단


# ============================================================
# 거래 기록
# ============================================================
@dataclass
class CoinTrade:
    market:      str
    buy_date:    str
    buy_price:   float
    qty:         float
    sell_date:   str   = ""
    sell_price:  float = 0.0
    sell_reason: str   = ""
    profit_rate: float = 0.0
    profit_krw:  float = 0.0
    fee:         float = 0.0
    score:       int   = 0
    hold_days:   int   = 0

    def to_dict(self) -> dict:
        return asdict(self)


# ============================================================
# 백테스트 설정
# ============================================================
@dataclass
class CBotBacktestConfig:
    initial_cash:    int   = 3_000_000    # cbot 실전 시딩 "300만원 모드" (★2026-08-17: 1000만→300만, 실전 반영)
    base_buy_amt:    int   = 1_000_000    # cbot 실전: 단일매수 100만원 (★2026-08-17: 40만→100만, 08-07 실전 변경 반영)
    max_positions:   int   = 3            # cbot 실전: 최대 3코인
    buy_score_min:   int   = 55           # AI 점수 기준선 (룰점수로 대체)

    start_date:      str   = "2026-04-06"
    end_date:        str   = ""
    codes:           list  = field(default_factory=list)

    fee_rate:        float = 0.0005       # 업비트 수수료 0.05%
    slippage:        float = 0.001
    tax_rate:        float = 0.0          # 코인은 거래세 없음

    ai_score_mode:   str   = "rule_proxy"  # AI 호출 불가 → 룰점수 사용
    verbose:         bool  = False
    enable_stagnant_rotation: bool = False  # ★ 2026-08-30: 정체 로테이션 실험 on/off
    stagnant_hours: int = STAGNANT_HOURS    # ★ 실험용 — 24h이 큰 상승 초입까지 잘라내는 사례가 있어 조정 테스트
    enable_pct_trail_ensemble: bool = False  # ★ 2026-09-03: %하드 트레일링 앙상블 실험 on/off
    pct_trail_threshold: float = PCT_TRAIL_PROFIT_THRESHOLD
    pct_trail_pct: float       = PCT_TRAIL_PCT


# ============================================================
# 백테스트 엔진
# ============================================================
class CBotBacktestEngine:
    def __init__(self, config: CBotBacktestConfig, db_path: str):
        self.config = config
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)

        self.cash = config.initial_cash
        self.positions: dict = {}
        self.peak_tracker: dict = {}
        self.open_trades: dict = {}
        self.trades: list = []
        self.equity_curve: list = []

        self.daily_loss_count = 0
        self.daily_loss_amt   = 0.0

        self._ohlcv_cache: dict = {}
        self._stagnant_info: dict = {}   # ★ {market: {"score":, "current":}} — 이번 틱 회전후보

    # ----------------------------------------------------------
    # 데이터 로드 (4시간봉)
    # ----------------------------------------------------------
    def _load_ohlcv(self, market: str) -> pd.DataFrame:
        if market in self._ohlcv_cache:
            return self._ohlcv_cache[market]
        df = pd.read_sql(
            "SELECT date, open, high, low, close, volume "
            "FROM daily_ohlcv WHERE code = ? ORDER BY date",
            self.conn, params=(market,))
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date").sort_index()
            df = df[~df.index.duplicated(keep="last")]
        self._ohlcv_cache[market] = df
        return df

    # ----------------------------------------------------------
    # 지표 계산 (해당 시점 t까지의 데이터로, t+1 시도 방지)
    # ----------------------------------------------------------
    def _calc_indicators(self, market: str, idx: int) -> Optional[dict]:
        """idx번째 캔들(0=가장 과거) 시점까지의 지표. idx는 df 내 위치(오름차순)."""
        df = self._load_ohlcv(market)
        if df.empty or idx < 21:
            return None

        window = df.iloc[max(0, idx - 60):idx + 1]  # 최근 최대 61봉
        closes  = window["close"].values
        volumes = window["volume"].values
        if len(closes) < 21:
            return None

        current = closes[-1]
        ma5  = closes[-5:].mean()
        ma20 = closes[-20:].mean()

        # RSI(14)
        deltas = closes[1:] - closes[:-1]
        period = min(14, len(deltas))
        recent_deltas = deltas[-period:]
        g = recent_deltas[recent_deltas > 0]
        l = -recent_deltas[recent_deltas < 0]
        avg_gain = g.sum() / period if len(g) else 0
        avg_loss = l.sum() / period if len(l) else 1e-9
        rs  = avg_gain / avg_loss if avg_loss > 0 else 0
        rsi = 100 - (100 / (1 + rs)) if avg_loss > 0 else 100

        vol_ma20  = volumes[-21:-1].mean() if len(volumes) >= 21 else volumes[:-1].mean()
        vol_ratio = volumes[-1] / vol_ma20 if vol_ma20 > 0 else 1.0

        candle_rate = (closes[-1] - closes[-2]) / closes[-2] if len(closes) >= 2 and closes[-2] > 0 else 0

        # ATR(14) — 고가/저가 필요
        highs = window["high"].values[-15:]
        lows  = window["low"].values[-15:]
        cl    = window["close"].values[-15:]
        trs = []
        for i in range(1, len(cl)):
            tr = max(highs[i] - lows[i],
                      abs(highs[i] - cl[i-1]),
                      abs(lows[i]  - cl[i-1]))
            trs.append(tr)
        atr = sum(trs) / len(trs) if trs else 0
        atr_rate = atr / current if current > 0 else 0

        return {
            "current": current, "ma5": ma5, "ma20": ma20, "rsi": rsi,
            "vol_ratio": vol_ratio, "candle_rate": candle_rate,
            "atr_rate": atr_rate,
        }

    # ----------------------------------------------------------
    # 매수 신호 (cbot.check_buy_signal 단순화 재현)
    # ----------------------------------------------------------
    def _check_buy_signal(self, ind: dict) -> tuple:
        if ind["candle_rate"] <= STOP_LOSS_CRASH:
            return False, "직전봉 급락"
        if ind["ma5"] <= ind["ma20"]:
            return False, "MA 역배열"
        if not (RSI_MIN <= ind["rsi"] <= RSI_MAX):
            return False, "RSI 범위 밖"
        if ind["vol_ratio"] < VOL_MULT:
            return False, "거래량 부족"
        if ind["current"] <= ind["ma20"]:
            return False, "현재가 MA20 이하"
        return True, "MA정배열+RSI양호+거래량충분"

    def _rule_score(self, ind: dict) -> int:
        """AI 호출 불가 → 룰 기반 점수로 대체"""
        score = 50
        if ind["ma5"] > ind["ma20"]:
            score += 15
        if 45 <= ind["rsi"] <= 65:
            score += 15
        elif ind["rsi"] > 70 or ind["rsi"] < 35:
            score -= 10
        if ind["vol_ratio"] >= 2.0:
            score += 15
        elif ind["vol_ratio"] >= 1.3:
            score += 8
        if ind["candle_rate"] > 0:
            score += 5
        return max(0, min(100, score))

    # ----------------------------------------------------------
    # ATR 레벨 계산 (calc_atr_levels와 동일 패턴)
    # ----------------------------------------------------------
    def _calc_atr_levels(self, entry: float, atr_rate: float) -> dict:
        if atr_rate > 0:
            atr_val = entry * atr_rate
            stop    = entry - atr_val * ATR_STOP_MULT
            target1 = entry + atr_val * ATR_TARGET_MULT
        else:
            atr_val = entry * abs(FALLBACK_STOP) / ATR_STOP_MULT
            stop    = entry * (1 + FALLBACK_STOP)
            target1 = entry * (1 + FALLBACK_TARGET)
        return {"stop_price": stop, "target1": target1, "atr_val": atr_val}

    # ----------------------------------------------------------
    # 매수 시뮬레이션
    # ----------------------------------------------------------
    def _simulate_buy(self, market: str, price: float, amount: int,
                      date: str, score: int, atr_rate: float):
        fill_price = price * (1 + self.config.slippage)
        qty = amount / fill_price
        cost = fill_price * qty
        fee  = cost * self.config.fee_rate
        total = cost + fee
        if total > self.cash or qty * fill_price < MIN_ORDER_AMT:
            return False

        self.cash -= total
        self.positions[market] = {
            "entry_price": fill_price, "qty": qty,
            "buy_date": date, "score": score,
        }
        levels = self._calc_atr_levels(fill_price, atr_rate)
        self.peak_tracker[market] = {
            "peak_rate": 0.0, "peak_price": fill_price, "stage": 0,
            "stop_price": levels["stop_price"], "target1": levels["target1"],
            "target_next": levels["target1"], "atr_val": levels["atr_val"],
            "buy_date": date,
        }
        self.open_trades[market] = CoinTrade(
            market=market, buy_date=date, buy_price=fill_price,
            qty=qty, score=score,
        )
        if self.config.verbose:
            print(f"   🟢 매수 {market} {qty:.6f}개 @ {fill_price:,.0f} | {score}점")
        return True

    # ----------------------------------------------------------
    # 매도 시뮬레이션 (부분매도 지원)
    # ----------------------------------------------------------
    def _simulate_sell(self, market: str, qty: float, price: float,
                       reason: str, date: str):
        if market not in self.positions:
            return
        fill_price = price * (1 - self.config.slippage)
        revenue = fill_price * qty
        fee = revenue * self.config.fee_rate
        net = revenue - fee
        self.cash += net

        pos = self.positions[market]
        entry = pos["entry_price"]
        pos_qty = pos["qty"]
        profit_krw = net - entry * qty
        profit_rate = (fill_price - entry) / entry

        ot = self.open_trades.get(market)
        if ot:
            ot.sell_date = date
            ot.sell_price = fill_price
            ot.sell_reason = reason
            ot.profit_rate = profit_rate
            ot.profit_krw += profit_krw
            ot.fee += fee

        is_full = qty >= pos_qty - 1e-12
        if is_full:
            if ot:
                ot.hold_days = self._calc_hold_days(pos["buy_date"], date)
                self.trades.append(ot)
            del self.open_trades[market]
            del self.positions[market]
            self.peak_tracker.pop(market, None)
        else:
            pos["qty"] -= qty

        if profit_rate < 0 and is_full:
            self.daily_loss_count += 1
            self.daily_loss_amt += profit_krw

        if self.config.verbose:
            print(f"   🔴 매도 {market} {qty:.6f}개 @ {fill_price:,.0f} "
                  f"({profit_rate:+.2%}) | {reason}")

    def _calc_hold_days(self, buy_date_str: str, sell_date_str: str) -> int:
        try:
            b = datetime.date.fromisoformat(buy_date_str[:10])
            s = datetime.date.fromisoformat(sell_date_str[:10])
            return (s - b).days
        except Exception:
            return 0

    # ----------------------------------------------------------
    # 매도 체크 — cbot._check_sell 재현 (ATR+25일+50%매도)
    # ----------------------------------------------------------
    def _check_sell_logic(self, market: str, date_str: str, ind: dict):
        pos = self.positions.get(market)
        if not pos:
            return
        tracker = self.peak_tracker.get(market)
        if not tracker:
            return

        current = ind["current"]
        entry   = pos["entry_price"]
        qty     = pos["qty"]
        rate    = (current - entry) / entry if entry > 0 else 0

        stage       = tracker["stage"]
        stop_price  = tracker["stop_price"]
        target_next = tracker["target_next"]
        atr_val     = tracker.get("atr_val", 0)
        peak_price  = tracker.get("peak_price", current)

        if current > peak_price:
            tracker["peak_price"] = current
            peak_price = current
        if rate > tracker["peak_rate"]:
            tracker["peak_rate"] = rate

        # ① 직전봉 급락 즉시 손절
        # ★ 2026-08-17: -0.08 하드코딩은 cbot.py의 "야간(23~06시)" 완화
        #   기준이었음 — 주간 기본값은 STOP_LOSS_CRASH(-0.05). 백테스트는
        #   4시간봉이라 야간을 구분 못 하므로 주간 기준(더 흔한 상황)을 씀.
        if ind["candle_rate"] <= STOP_LOSS_CRASH:
            self._simulate_sell(market, qty, current,
                               f"급락감지({ind['candle_rate']:+.2%})", date_str)
            return

        # ② 손절가 이탈
        if current <= stop_price:
            label = "손절" if stage == 0 else f"손절(stage{stage})"
            self._simulate_sell(market, qty, current, f"{label}({rate:+.2%})", date_str)
            return

        # ②-1 보유기한 초과 (stage==0, 25일)
        if stage == 0:
            buy_date_str = tracker.get("buy_date", "")
            if buy_date_str:
                try:
                    buy_date  = datetime.date.fromisoformat(buy_date_str[:10])
                    cur_date  = datetime.date.fromisoformat(date_str[:10])
                    held_days = (cur_date - buy_date).days
                    if held_days >= 25:
                        label = "기한초과" if rate >= 0 else "기한초과(손실)"
                        self._simulate_sell(market, qty, current,
                                           f"{label}({rate:+.2%})", date_str)
                        return
                except Exception:
                    pass

        # ②-2 정체 판단 (실험, stage==0 한정) — ★ 2026-08-30
        #   손실 중이면 건드리지 않음(위 손절/25일기한이 이미 커버).
        #   0%~2% 미만 수익 + 24시간 초과 + "거래량까지 마름"(vol_ratio가
        #   20봉평균 밑 — 매수/매도가 치열하게 싸우는 중이면 거래량이 살아
        #   있을 테니 그런 건 제외)인 경우만 회전 후보로 표시. 실제 매도는
        #   여기서 하지 않고, run()에서 "진짜 점수 높은 대안이 나타났을
        #   때만" 교체한다(무조건 시간손절은 1차 실험에서 큰 승리를
        #   갉아먹는 역효과 확인 — 대안 존재 조건 추가로 완화).
        if (self.config.enable_stagnant_rotation and stage == 0
                and 0 <= rate < STAGNANT_PROFIT_CAP
                and ind.get("vol_ratio", 1.0) < STAGNANT_VOL_RATIO_MAX):
            buy_dt_str = tracker.get("buy_date", "")
            if buy_dt_str:
                try:
                    buy_dt     = datetime.datetime.fromisoformat(buy_dt_str)
                    cur_dt     = datetime.datetime.fromisoformat(date_str)
                    held_hours = (cur_dt - buy_dt).total_seconds() / 3600
                    if held_hours >= self.config.stagnant_hours:
                        self._stagnant_info[market] = {
                            "score":   self._rule_score(ind),
                            "current": current,
                        }
                except Exception:
                    pass

        # ③ 트레일링 스탑 (목표1 달성 이후) — ATR 트레일링 + (실험) %하드 앙상블
        if stage >= 1 and atr_val > 0:
            trail_stop = peak_price - atr_val * ATR_TRAIL_MULT
            label = "트레일링"
            if (self.config.enable_pct_trail_ensemble
                    and tracker["peak_rate"] >= self.config.pct_trail_threshold):
                pct_trail_stop = peak_price * (1 - self.config.pct_trail_pct)
                if pct_trail_stop > trail_stop:
                    trail_stop = pct_trail_stop
                    label = "%트레일링"
            if current <= trail_stop:
                self._simulate_sell(market, qty, current, f"{label}({rate:+.2%})", date_str)
                return

        # ④ 목표가 달성 → 손절/목표가 상향 (목표1은 50%매도)
        if target_next > 0 and current >= target_next:
            if stage == 0:
                half_qty = qty if qty * current <= MIN_ORDER_AMT * 2 else qty / 2
                if half_qty > 0 and (qty - half_qty) * current >= MIN_ORDER_AMT:
                    self._simulate_sell(market, half_qty, current,
                                       f"목표1익절50%({rate:+.2%})", date_str)
                new_stop   = entry + atr_val * ATR_RAISE_MULT
                new_target = current + atr_val * ATR_TARGET_MULT
                tracker["stop_price"]  = new_stop
                tracker["target_next"] = new_target
                tracker["stage"]       = 1
            else:
                new_stop   = target_next
                new_target = current + atr_val * ATR_TARGET_MULT
                tracker["stop_price"]  = new_stop
                tracker["target_next"] = new_target
                tracker["stage"]       = stage + 1

    # ----------------------------------------------------------
    # 실행
    # ----------------------------------------------------------
    def run(self):
        end_date = self.config.end_date or datetime.date.today().isoformat()

        all_dates = set()
        for market in self.config.codes:
            df = self._load_ohlcv(market)
            if df.empty:
                continue
            mask = (df.index >= self.config.start_date) & (df.index <= end_date)
            all_dates.update(df.index[mask])
        timeline = sorted(all_dates)

        if not timeline:
            print("⚠️ 타임라인이 비어 있음 — 데이터/기간 확인 필요")
            return

        for ts in timeline:
            date_str = ts.strftime("%Y-%m-%d %H:%M:%S")
            self.daily_loss_count = 0
            self.daily_loss_amt = 0.0
            self._stagnant_info = {}   # ★ 이번 틱 정체후보 초기화

            for market in list(self.positions.keys()):
                df = self._load_ohlcv(market)
                if ts not in df.index:
                    continue
                idx = df.index.get_loc(ts)
                ind = self._calc_indicators(market, idx)
                if ind:
                    self._check_sell_logic(market, date_str, ind)

            self._record_equity(ts)

            # ★ 2026-08-30: 슬롯이 꽉 찼어도(포지션 수 == max) 정체 후보가
            #   있으면 대안 후보를 계속 스캔해야 하므로, 빈 슬롯 없다고
            #   바로 continue하지 않고 후보 스캔은 항상 수행.
            candidates = []
            for market in self.config.codes:
                if market in self.positions:
                    continue
                df = self._load_ohlcv(market)
                if ts not in df.index:
                    continue
                idx = df.index.get_loc(ts)
                ind = self._calc_indicators(market, idx)
                if not ind:
                    continue

                ok, reason = self._check_buy_signal(ind)
                if not ok:
                    continue

                score = self._rule_score(ind)
                if score >= self.config.buy_score_min:
                    candidates.append((score, market, ind))

            candidates.sort(reverse=True)

            # ★ 2026-08-30: 정체교체 — 슬롯이 꽉 찼고, 정체 후보 중 가장
            #   점수 낮은 것보다 실제로 더 점수 높은 대안이 대기 중일
            #   때만 교체. 대안이 없으면 그냥 계속 들고 감(무조건 시간
            #   손절은 1차 실험에서 역효과 확인됨).
            if (self.config.enable_stagnant_rotation
                    and len(self.positions) >= self.config.max_positions
                    and self._stagnant_info and candidates):
                worst_market = min(self._stagnant_info,
                                    key=lambda m: self._stagnant_info[m]["score"])
                worst_score  = self._stagnant_info[worst_market]["score"]
                best_score, best_market, best_ind = candidates[0]
                if best_score > worst_score:
                    worst_pos = self.positions[worst_market]
                    self._simulate_sell(
                        worst_market, worst_pos["qty"],
                        self._stagnant_info[worst_market]["current"],
                        f"정체교체(→{best_market})", date_str,
                    )
                    candidates.pop(0)
                    amount = min(self.config.base_buy_amt, self.cash * 0.95)
                    if amount >= MIN_ORDER_AMT:
                        self._simulate_buy(best_market, best_ind["current"], int(amount),
                                           date_str, best_score, best_ind["atr_rate"])

            if len(self.positions) >= self.config.max_positions:
                continue

            for score, market, ind in candidates:
                if len(self.positions) >= self.config.max_positions:
                    break
                amount = min(self.config.base_buy_amt, self.cash * 0.95)
                if amount < MIN_ORDER_AMT:
                    continue
                self._simulate_buy(market, ind["current"], int(amount),
                                   date_str, score, ind["atr_rate"])

        for market in list(self.positions.keys()):
            df = self._load_ohlcv(market)
            if df.empty:
                continue
            last_price = df["close"].iloc[-1]
            last_date  = df.index[-1].strftime("%Y-%m-%d %H:%M:%S")
            self._simulate_sell(market, self.positions[market]["qty"],
                               last_price, "백테스트종료", last_date)

        print(f"✅ [CBOT] 완료 — {len(self.trades)}건, 최종현금 {self.cash:,.0f}원")

    def _record_equity(self, ts):
        equity = self.cash
        for market, pos in self.positions.items():
            df = self._load_ohlcv(market)
            if ts in df.index:
                equity += df.loc[ts, "close"] * pos["qty"]
            else:
                equity += pos["entry_price"] * pos["qty"]
        self.equity_curve.append((ts.strftime("%Y-%m-%d %H:%M:%S"), equity))

    def get_trades(self) -> list:
        return [t.to_dict() for t in self.trades]

    def get_equity_curve(self) -> list:
        return self.equity_curve
