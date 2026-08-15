"""
sbo2_signal_backtest_engine.py — sbo2 실제 신호소스(추세) 백테스트
================================================================
[배경]
기존 sbo2_backtest_engine.py는 SwingStrategy(sbot 룰스코어)를 재사용하는
완전히 다른 모델이라, sbo2 실전이 실제로 쓰는 필터 체인을 전혀 반영하지
못했음(2026-07-17 사용자 지적). 이 엔진은 trend_analyzer.py의 추세 필터를
"특정 날짜까지의 데이터만" 기준으로 그대로 재현해 day-by-day 재현(replay)한다.

[반영 범위]
  ✅ 추세      — trend_analyzer.py get_trend_data()와 동일 필터체인
  ⏹ VCP(스윙) — ★ 2026-08-15 완전 제거. 퍼널 진단 결과 7개월(141거래일)간
     "30일 신고가 돌파+거래량서지 동시조건" 통과가 2건, 최종 거래량서지까지
     걸리면 0건 — 사실상 죽은 소스였음(실거래도 141일 중 VCP발 거래 단
     1건, -19.77% 손실). sbo2 실전에서도 SLOT_SWING 자체가 제거되고 AI
     모멘텀 스캐너(SLOT_MOMENTUM)로 교체됨. 모멘텀은 매일 AI 테마추출이
     들어가는 방식이라 이 엔진처럼 수식 재현이 안 됨 — 과거 재현 대신
     lina_bot.py의 "AI 모멘텀 체크인" 알림으로 매일 실거래 사후검증 중
     (사용자 결정, 백테스트 반영은 스코프 아웃).
  ⏸ 교집합/완화 — 촉매(실시간 뉴스/텔레그램) 과거기록이 없어 재현 불가.
     데이터가 쌓이면(라이브 사후검증으로) 추후 반영.
  ⏹ 생쇼 — sbo2 실전에서 슬롯 자체가 삭제됨(2026-07-25, MBN이 생쇼
     뉴스 코너 폐지). 이 엔진도 애초에 반영한 적 없어 추가 조치 불필요.

[매도 로직] sbo2.py _check_sell()과 동일한 ATR 단계별 피라미딩 재현:
  - 손절: 체결가 기준 ATR×2.0 이탈 (★ 2026-08-15 수정 — 예전엔 스캔용
    ATR×1.5를 그대로 썼는데, 실전 _check_buy()는 체결 시점에 ATR을
    다시 계산해 ×2.0/목표×3.0(상한 +20%)을 쓰고 있어 백테스트가 실전
    보다 손절폭을 좁게 시뮬레이션하고 있었음 — "정비" 요청 계기로 확인/수정)
  - 목표1(ATR×3, 상한 +20%) 도달 → 50% 매도, 손절↑(entry+ATR×1.0), 목표2=curr+ATR×3
  - 목표2+ 도달 → 추가매도 없이 손절↑(이전목표가), 목표 재상향 (무한 피라미딩)
  - stage>=1 이후엔 고점-ATR×1.5 트레일링도 병행

[매수 타이밍] VCP/추세 조건을 만족한 날의 T+1 시가 매수 (기존 엔진과 동일 관례)

[08-15 반영] 전일거래량 30만주 미만 제외(08-07 실전 필터와 동일),
  --initial-cash/--max-positions 기본값을 현재 실전(600만원/4종목)에 맞춤.
  ⚠️ 시가총액 3,000억 필터는 kr_stock_daily_data에 과거 시가총액 데이터가
  없어(종가/거래량만 있음) 백테스트엔 반영 못함 — 실전보다 소형주가 더
  많이 통과할 수 있다는 점 감안해서 결과 해석할 것.

[사용법]
  python3 sbo2_signal_backtest_engine.py --start 2025-10-01 --end 2026-07-16
"""
import os
import sys
import sqlite3
import argparse
import datetime
import re
from collections import defaultdict

import pandas as pd
import numpy as np

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LINA_DIR = os.path.join(os.path.dirname(BASE_DIR), "lina_bot")
sys.path.insert(0, LINA_DIR)

from stock_filters import is_etf_or_excluded
from metrics import calc_metrics, format_report, format_comparison

DB_PATH = os.path.join(LINA_DIR, "kr_theme_finance.db")

# ── sbo2 실전과 동일 상수 ────────────────────────────────────
BASE_BUY_AMT     = 1_500_000
SLOT_BUY_AMT     = int(BASE_BUY_AMT * 0.83)   # 추세 슬롯 125만원
FEE_RATE         = 0.00015
TAX_RATE         = 0.0015
SLIPPAGE         = 0.0005

# 추세 파라미터 공용 (trend_analyzer.py)
SMART_DAYS        = 10
SMART_MIN_DAYS    = 2
ATR_PERIOD        = 14
ATR_STOP_MULT     = 1.5    # ★ 후보 스캔(trend_analyzer.py)용 — 실제 포지션
ATR_TARGET_MULT   = 3.0    #   손절/목표엔 안 쓰임(아래 TRADE_* 참고)

# ★ 2026-08-15 추가 — 실제 매수 체결 시 손절/목표 (sbo2.py _check_buy()와
#   동일). 기존엔 위 스캔용 ATR_STOP_MULT(1.5)를 포지션 손절에도 그대로
#   썼는데, 실전은 매수 체결가 기준으로 ATR을 다시 계산해 손절×2.0/
#   목표×3.0(상한 +20%)을 쓰고 있어 백테스트가 실전보다 손절폭을 좁게
#   시뮬레이션하고 있었음(사용자 지적 — "정비" 요청 계기로 확인).
TRADE_ATR_STOP_MULT   = 2.0
TRADE_ATR_TARGET_MULT = 3.0
TRADE_TARGET_CAP_RATE = 0.20
# 08-07 추가된 전일거래량 최소기준(시가총액은 과거 데이터가 없어 백테스트
# 반영 불가 — 아래 main()의 안내 참고)
MIN_PREV_VOLUME = 300_000

# 추세 파라미터 (trend_analyzer.py, 2026-07-14 수정본 반영: VOL_PULL_RATIO 0.85)
PULLBACK_BAND   = 0.08
RSI_LOW, RSI_HIGH = 40, 60
VOL_PULL_RATIO  = 0.85
WAVE_RECENT     = 20
WAVE_PREV       = 20


# ============================================================
# 데이터 로딩 (한 번에 메모리로)
# ============================================================
def load_all_stocks(min_rows: int = 60) -> dict:
    conn = sqlite3.connect(DB_PATH)
    cur  = conn.cursor()
    cur.execute("SELECT DISTINCT stock_name FROM kr_stock_daily_data")
    names = [r[0] for r in cur.fetchall()]

    data = {}
    for name in names:
        pure = re.sub(r"\s*(KOSPI|KOSDAQ)\s*\d{6}$", "", name).strip()
        if is_etf_or_excluded(pure):
            continue
        rows = cur.execute("""
            SELECT date, close_price, volume, foreign_net_buy, institution_net_buy,
                   open_price, high_price, low_price
            FROM kr_stock_daily_data WHERE stock_name = ? ORDER BY date ASC
        """, (name,)).fetchall()
        if len(rows) < min_rows:
            continue
        dates   = [r[0] for r in rows]
        closes  = np.array([r[1] or 0.0 for r in rows], dtype=float)
        volumes = np.array([r[2] or 0.0 for r in rows], dtype=float)
        f_net   = np.array([r[3] if r[3] is not None else 0.0 for r in rows], dtype=float)
        i_net   = np.array([r[4] if r[4] is not None else 0.0 for r in rows], dtype=float)
        # ★ 2026-07-17: open/high/low은 2026-05-18 이후분만 채워져 있음
        #   (그 전엔 NULL/0) — 0이면 종가로 폴백해서 배열 길이/정합성 유지.
        opens   = np.array([r[5] or 0.0 for r in rows], dtype=float)
        highs   = np.array([r[6] or 0.0 for r in rows], dtype=float)
        lows    = np.array([r[7] or 0.0 for r in rows], dtype=float)
        opens   = np.where(opens > 0, opens, closes)
        highs   = np.where(highs > 0, highs, closes)
        lows    = np.where(lows > 0, lows, closes)
        data[pure] = {"dates": dates, "close": closes, "volume": volumes,
                      "f_net": f_net, "i_net": i_net,
                      "open": opens, "high": highs, "low": lows}
    conn.close()
    print(f"✅ 데이터 로드: {len(data)}종목")
    return data


def _ma(arr_desc, n):
    if len(arr_desc) < n:
        return 0.0
    return float(np.mean(arr_desc[:n]))


def _atr(arr_desc, n=14):
    if len(arr_desc) < n + 1:
        return 0.0
    diffs = np.abs(arr_desc[:n] - arr_desc[1:n+1])
    return float(np.mean(diffs))


def _rsi(arr_desc, n=14):
    if len(arr_desc) < n + 1:
        return 50.0
    gains  = [max(arr_desc[i] - arr_desc[i+1], 0) for i in range(n)]
    losses = [max(arr_desc[i+1] - arr_desc[i], 0) for i in range(n)]
    ag, al = sum(gains)/n, sum(losses)/n
    if al == 0:
        return 100.0
    return round(100 - (100 / (1 + ag/al)), 1)


def _is_upslope(arr_desc, n=60):
    if len(arr_desc) < n:
        return False
    half = n // 2
    ma_recent = np.mean(arr_desc[:half])
    ma_prev   = np.mean(arr_desc[half:n])
    return ma_recent > ma_prev


def _smart_ok(f_net_desc, i_net_desc, supply_len):
    if supply_len == 0:
        return True
    sw = min(SMART_DAYS, supply_len)
    f_pos = int(np.sum(f_net_desc[:sw] > 0))
    i_pos = int(np.sum(i_net_desc[:sw] > 0))
    f_cum = float(np.sum(f_net_desc[:sw]))
    i_cum = float(np.sum(i_net_desc[:sw]))
    adj_min = max(2, int(SMART_MIN_DAYS * supply_len / SMART_DAYS))
    return (f_pos >= adj_min or i_pos >= adj_min or
            (f_cum > 0 and i_cum > 0) or (f_cum > 0 or i_cum > 0))


# ============================================================
# 추세 — as-of 스캔
# ============================================================
def scan_trend(data: dict, idx_map: dict) -> list:
    out = []
    for name, d in data.items():
        cursor = idx_map.get(name, 0)
        if cursor < 60:
            continue
        closes_desc  = d["close"][:cursor][::-1]
        volumes_desc = d["volume"][:cursor][::-1]
        curr = closes_desc[0]
        if curr <= 0:
            continue

        ma60 = _ma(closes_desc, 60)
        if ma60 == 0 or curr < ma60:
            continue
        if not _is_upslope(closes_desc, 60):
            continue

        if len(closes_desc) < WAVE_RECENT + WAVE_PREV:
            continue
        recent_window = closes_desc[0:WAVE_RECENT]
        recent_hi, recent_lo = float(np.max(recent_window)), float(np.min(recent_window))
        prev_window = closes_desc[WAVE_RECENT:WAVE_RECENT+WAVE_PREV]
        prev_hi, prev_lo = float(np.max(prev_window)), float(np.min(prev_window))
        if recent_hi <= prev_hi or recent_lo <= prev_lo:
            continue

        dist_from_lo = abs(curr - recent_lo) / recent_lo if recent_lo > 0 else 1
        if dist_from_lo > PULLBACK_BAND:
            continue
        idx_lo = int(np.argmin(recent_window))
        if idx_lo < 2 or curr <= recent_lo:
            continue

        rsi = _rsi(closes_desc, 14)
        if not (RSI_LOW <= rsi <= RSI_HIGH):
            continue

        valid_vol = volumes_desc[volumes_desc > 0]
        if len(valid_vol) < 10:
            continue
        vol_avg_all    = float(np.mean(valid_vol))
        vol_avg_recent = float(np.mean(volumes_desc[:5]))
        if vol_avg_all == 0 or vol_avg_recent >= vol_avg_all * VOL_PULL_RATIO:
            continue

        f_net_desc = d["f_net"][:cursor][::-1]
        i_net_desc = d["i_net"][:cursor][::-1]
        supply_len = max(int(np.sum(f_net_desc != 0)), int(np.sum(i_net_desc != 0)))
        if not _smart_ok(f_net_desc, i_net_desc, supply_len):
            continue

        atr = _atr(closes_desc, ATR_PERIOD)
        stop_price = round(curr - atr * ATR_STOP_MULT, 0)
        tgt_price  = round(curr + atr * ATR_TARGET_MULT, 0)
        if stop_price <= 0:
            continue
        out.append({"name": name, "curr_price": curr, "stop_price": stop_price,
                    "tgt_price": tgt_price, "atr_val": atr, "score": 70})
    return out


# ============================================================
# 포트폴리오 시뮬레이션
# ============================================================
class Sbo2SignalBacktest:
    def __init__(self, data: dict, initial_cash: int, max_positions: int):
        self.data          = data
        self.cash          = initial_cash
        self.initial_cash  = initial_cash
        self.max_positions = max_positions
        self.positions     = {}   # name -> dict
        self.trades        = []
        self.equity_curve  = []

    def _price_on(self, name, cursor):
        """cursor 인덱스(당일 포함)의 종가"""
        d = self.data[name]
        if cursor <= 0 or cursor > len(d["close"]):
            return 0.0
        return float(d["close"][cursor - 1])

    def _next_open(self, name, cursor):
        """cursor 다음날 실제 시가 (2026-05-18 이전 데이터는 종가로 폴백됨, load_all_stocks 참고)"""
        d = self.data[name]
        if cursor >= len(d["open"]):
            return 0.0
        return float(d["open"][cursor])

    def _check_sell_day(self, name, cursor):
        """
        ★ 2026-07-17: open/high/low 실데이터 반영 — 종가 1번만 체크하던 것을
        시가→저가→고가→종가 4개 체크포인트로 확장(기존 sbot 백테스트 엔진과
        동일 관례). 저가로 손절/트레일링을, 고가로 목표가 도달을 판단해야
        장중 터치를 종가만으로 놓치는 걸 방지 — 2026-05-18 이전 데이터는
        시가=고가=저가=종가로 폴백되어 있어 자동으로 종가 1회 체크와 동일해짐.
        """
        pos = self.positions.get(name)
        if not pos:
            return
        d = self.data[name]
        if cursor <= 0 or cursor > len(d["close"]):
            return
        i = cursor - 1
        checkpoints = [
            ("시가", float(d["open"][i])),
            ("저가", float(d["low"][i])),
            ("고가", float(d["high"][i])),
            ("종가", float(d["close"][i])),
        ]

        for _label, curr in checkpoints:
            if name not in self.positions:
                return
            pos = self.positions[name]
            if curr <= 0:
                continue
            entry = pos["entry_price"]
            rate  = (curr - entry) / entry * 100
            if curr > pos["peak_price"]:
                pos["peak_price"] = curr
            stage = pos["stage"]
            atr_val = pos["atr_val"]

            reason = None
            if pos["stop_price"] > 0 and curr <= pos["stop_price"]:
                reason = f"손절({rate:+.1f}%)"
            if not reason and stage >= 1 and atr_val > 0:
                trail = pos["peak_price"] - atr_val * 1.5
                if curr <= trail:
                    reason = f"트레일링({rate:+.1f}%)"

            if not reason and pos["target_next"] > 0 and curr >= pos["target_next"]:
                if stage == 0:
                    half = max(pos["qty"] // 2, 1) if pos["qty"] > 1 else pos["qty"]
                    self._sell(name, half, curr, f"목표1익절50%({rate:+.1f}%)", cursor, is_partial=True)
                    new_stop   = round(entry + atr_val * 1.0, 0) if atr_val > 0 else round(entry * 1.02, 0)
                    new_target = round(curr + atr_val * 3.0, 0) if atr_val > 0 else round(curr * 1.10, 0)
                    pos["stop_price"], pos["tgt_price"], pos["target_next"] = new_stop, new_target, new_target
                    pos["stage"] = 1
                else:
                    new_stop   = pos["target_next"]
                    new_target = round(curr + atr_val * 3.0, 0) if atr_val > 0 else round(curr * 1.10, 0)
                    pos["stop_price"], pos["tgt_price"], pos["target_next"] = new_stop, new_target, new_target
                    pos["stage"] += 1
                continue

            if reason:
                self._sell(name, pos["qty"], curr, reason, cursor, is_partial=False)
                return

    def _sell(self, name, qty, price, reason, cursor, is_partial):
        pos = self.positions[name]
        fill = price * (1 - SLIPPAGE)
        revenue = fill * qty
        fee = revenue * FEE_RATE
        tax = revenue * TAX_RATE
        net = revenue - fee - tax
        self.cash += net
        entry = pos["entry_price"]
        profit_krw = net - entry * qty
        profit_rate = (fill - entry) / entry

        if qty >= pos["qty"]:
            hold_days = cursor - pos["buy_idx"]
            self.trades.append({
                "code": name, "buy_date": pos["buy_date"], "buy_price": entry,
                "qty": pos["orig_qty"], "sell_date": self.data[name]["dates"][cursor-1] if cursor-1 < len(self.data[name]["dates"]) else "",
                "sell_price": fill, "sell_reason": reason,
                "profit_rate": profit_rate, "profit_krw": pos.get("realized_krw", 0) + profit_krw,
                "hold_days": hold_days, "score": pos["score"],
            })
            del self.positions[name]
        else:
            pos["qty"] -= qty
            pos["realized_krw"] = pos.get("realized_krw", 0) + profit_krw

    def _buy(self, name, price, cursor, atr_val, stop_price, tgt_price, score):
        if len(self.positions) >= self.max_positions or name in self.positions:
            return
        fill = price * (1 + SLIPPAGE)
        qty = int(SLOT_BUY_AMT / (fill * (1 + FEE_RATE)))
        if qty <= 0:
            return
        cost = fill * qty * (1 + FEE_RATE)
        if cost > self.cash:
            return
        self.cash -= cost
        self.positions[name] = {
            "entry_price": fill, "qty": qty, "orig_qty": qty,
            "buy_idx": cursor, "buy_date": self.data[name]["dates"][cursor-1] if cursor-1 < len(self.data[name]["dates"]) else "",
            "stop_price": stop_price, "tgt_price": tgt_price, "target_next": tgt_price,
            "atr_val": atr_val, "peak_price": fill, "stage": 0, "score": score,
            "realized_krw": 0,
        }

    def run(self, calendar: list):
        idx_map = {name: 0 for name in self.data}
        # 각 종목의 dates 배열에서 오늘 이하 최대 인덱스를 빠르게 찾기 위한 포인터
        ptr = {name: 0 for name in self.data}

        for day_i, day in enumerate(calendar):
            # 각 종목 커서 갱신 (day 이하 마지막 인덱스)
            for name, d in self.data.items():
                dates = d["dates"]
                p = ptr[name]
                n = len(dates)
                while p < n and dates[p] <= day:
                    p += 1
                ptr[name] = p
                idx_map[name] = p

            # ① 매도 체크
            for name in list(self.positions.keys()):
                self._check_sell_day(name, idx_map.get(name, 0))

            # ② 자산 기록
            mv = self.cash
            for name, pos in self.positions.items():
                mv += self._price_on(name, idx_map.get(name, 0)) * pos["qty"]
            self.equity_curve.append((day, mv))

            if len(self.positions) >= self.max_positions:
                continue

            # ③ 후보 스캔 (오늘까지 데이터로 추세만 — VCP는 08-15 제거)
            cands = [(c, "trend") for c in scan_trend(self.data, idx_map)]

            for c, tag in cands:
                if len(self.positions) >= self.max_positions:
                    break
                name = c["name"]
                if name in self.positions:
                    continue
                cursor = idx_map.get(name, 0)

                # ★ 2026-08-15 추가 — 전일거래량 30만주 미만 제외 (sbo2.py
                #   _check_buy()의 08-07 필터와 동일 기준)
                d = self.data[name]
                if cursor > 0 and d["volume"][cursor - 1] < MIN_PREV_VOLUME:
                    continue

                buy_price = self._next_open(name, cursor)
                if buy_price <= 0:
                    continue

                # ★ 체결가 기준으로 손절/목표 재계산 (sbo2.py _check_buy()와
                #   동일 — 스캔일의 ATR×1.5 대신 체결일 ATR×2.0/3.0 사용)
                fill_cursor = cursor + 1
                closes_desc_fill = d["close"][:fill_cursor][::-1]
                atr_fill = _atr(closes_desc_fill, ATR_PERIOD)
                if atr_fill > 0:
                    stop_price = round(buy_price - atr_fill * TRADE_ATR_STOP_MULT, 0)
                    tgt_atr    = buy_price + atr_fill * TRADE_ATR_TARGET_MULT
                    tgt_cap    = buy_price * (1 + TRADE_TARGET_CAP_RATE)
                    tgt_price  = round(min(tgt_atr, tgt_cap), 0)
                else:
                    stop_price = round(buy_price * 0.90, 0)
                    tgt_price  = round(buy_price * 1.15, 0)

                self._buy(name, buy_price, fill_cursor, atr_fill,
                          stop_price, tgt_price, c["score"])

            if (day_i + 1) % 50 == 0:
                print(f"   진행 {day_i+1}/{len(calendar)} | 보유 {len(self.positions)}개 | "
                      f"완료거래 {len(self.trades)}건")

        # 잔여 포지션 종료일 청산
        if calendar:
            last_cursor_map = idx_map
            for name in list(self.positions.keys()):
                cursor = last_cursor_map.get(name, 0)
                price = self._price_on(name, cursor)
                if price > 0:
                    self._sell(name, self.positions[name]["qty"], price, "백테스트종료", cursor, False)

    def get_trades(self):
        return self.trades

    def get_equity_curve(self):
        return self.equity_curve


def main():
    parser = argparse.ArgumentParser(description="sbo2 VCP/추세 실신호 백테스트")
    parser.add_argument("--start", default="2025-12-01")
    parser.add_argument("--end", default="")
    # ★ 2026-08-15: sbo2 실전 현재값(600만원 시드, 4종목 상한)에 맞춤.
    #   --slot 옵션 제거 — VCP 완전 제거로 추세만 남아 선택지가 무의미해짐.
    parser.add_argument("--initial-cash", type=int, default=6_000_000)
    parser.add_argument("--max-positions", type=int, default=4)
    args = parser.parse_args()

    end = args.end or datetime.date.today().strftime("%Y-%m-%d")
    data = load_all_stocks()
    calendar = sorted({dt for d in data.values() for dt in d["dates"] if args.start <= dt <= end})
    print(f"🚀 [SBO2-신호(추세)] {args.start} ~ {end} | {len(calendar)}일")

    bt = Sbo2SignalBacktest(data, args.initial_cash, args.max_positions)
    bt.run(calendar)
    metrics = calc_metrics(bt.get_trades(), bt.get_equity_curve(), args.initial_cash)
    print(format_report(metrics, "trend"))

    return [{"name": "trend", "metrics": metrics, "trades": bt.get_trades(),
             "equity": bt.get_equity_curve()}]


if __name__ == "__main__":
    main()
