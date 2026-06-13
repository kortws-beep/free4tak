"""
lina_backtest.py — lina_bot 스윙 전략 백테스터
================================================================
[설계 원칙]
- 데이터: kr_theme_finance.db (200일치 OHLCV + 수급)
- 전략: swing_analyzer (VCP) + trend_analyzer (추세) 조건
- 진입: 신호 발생 다음날 시가 (T+1 시가 근사 = 당일 종가 × 1.005)
- 매도: ATR 손절 / 목표가 / 최대 보유일 초과
- 시뮬: 날짜별 롤링 윈도우로 과거 조건 재현

[사용법]
  python3 lina_backtest.py                    # 전체 백테스트
  python3 lina_backtest.py --start 2025-09-01 # 시작일 지정
  python3 lina_backtest.py --compare          # 파라미터 비교
  python3 lina_backtest.py --top 20           # 상위 N종목만
"""

import os
import sys
import sqlite3
import datetime
import argparse
import json
from dataclasses import dataclass, field

BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
LINA_DIR  = os.path.dirname(BASE_DIR)   # lina_bot 폴더
DB_PATH   = os.path.join(LINA_DIR, "kr_theme_finance.db")
RESULT_DIR = os.path.join(BASE_DIR, "results")
os.makedirs(RESULT_DIR, exist_ok=True)

# ── 기본 파라미터 ─────────────────────────────────────────────
@dataclass
class BacktestConfig:
    start_date:      str   = "2025-09-01"
    end_date:        str   = ""           # 비우면 오늘
    initial_cash:    float = 5_000_000    # 500만원
    base_buy_amt:    float = 1_500_000    # 1종목 기본 150만
    max_positions:   int   = 4            # 최대 4종목
    atr_stop_mult:   float = 1.5          # 손절 ATR 배수
    atr_target_mult: float = 3.0          # 목표 ATR 배수
    max_hold_days:   int   = 20           # 최대 보유일
    min_rr:          float = 1.5          # 최소 R:R
    # VCP 파라미터
    ma20_band:       float = 0.07         # 20일선 ±7%
    vcp_ratio:       float = 0.60         # VCP 수렴 비율
    vol_dry_ratio:   float = 0.50         # 거래량 마름 비율
    # 추세 파라미터
    pullback_band:   float = 0.08         # 눌림목 ±8%
    rsi_low:         float = 40.0
    rsi_high:        float = 60.0


# ══════════════════════════════════════════════════════════════
# 헬퍼
# ══════════════════════════════════════════════════════════════

def _ma(prices, n):
    if len(prices) >= n:
        return sum(prices[:n]) / n
    if len(prices) >= int(n * 0.95):
        return sum(prices) / len(prices)
    return 0.0

def _atr(prices, n=14):
    if len(prices) < n + 1:
        return 0.0
    return sum(abs(prices[i] - prices[i+1]) for i in range(n)) / n

def _rsi(prices, n=14):
    if len(prices) < n + 1:
        return 50.0
    gains  = [max(prices[i] - prices[i+1], 0) for i in range(n)]
    losses = [max(prices[i+1] - prices[i], 0) for i in range(n)]
    ag = sum(gains) / n
    al = sum(losses) / n
    if al == 0: return 100.0
    return 100 - (100 / (1 + ag / al))


# ══════════════════════════════════════════════════════════════
# 데이터 로더
# ══════════════════════════════════════════════════════════════

def load_stock_data(conn, stock_name: str) -> list:
    """
    종목 전체 일봉 데이터 로드
    반환: [{"date", "close", "volume", "f_net", "i_net"}, ...]  (최신→과거)
    """
    rows = conn.execute("""
        SELECT date, close_price, volume, foreign_net_buy, institution_net_buy
        FROM kr_stock_daily_data
        WHERE stock_name = ?
        ORDER BY date DESC
        LIMIT 220
    """, (stock_name,)).fetchall()

    result = []
    for r in rows:
        result.append({
            "date":    r[0],
            "close":   r[1] or 0,
            "volume":  r[2] or 0,
            "f_net":   r[3] or 0,
            "i_net":   r[4] or 0,
        })
    return result


def get_all_stocks(conn) -> list:
    rows = conn.execute(
        "SELECT DISTINCT stock_name FROM kr_stock_daily_data"
    ).fetchall()
    return [r[0] for r in rows]


# ══════════════════════════════════════════════════════════════
# 신호 계산 (특정 날짜 기준 과거 데이터로)
# ══════════════════════════════════════════════════════════════

def check_vcp_signal(data: list, cfg: BacktestConfig) -> dict:
    """
    VCP 스윙 신호 체크
    data: 특정 날짜 기준 과거 데이터 (최신→과거, 최소 30개)
    반환: {"signal": bool, "stop": float, "tgt": float, "rr": float, "score": int}
    """
    if len(data) < 30:
        return {"signal": False}

    closes  = [d["close"] for d in data if d["close"] > 0]
    volumes = [d["volume"] for d in data if d["volume"] > 0]

    if len(closes) < 30:
        return {"signal": False}

    curr = closes[0]

    # ETF/우선주 제외
    # (호출 전에 처리)

    # ① 200일선 위
    ma200 = _ma(closes, 200)
    if ma200 > 0 and curr < ma200:
        return {"signal": False}

    # ② 20일선 밀집
    ma20 = _ma(closes, 20)
    if ma20 == 0: return {"signal": False}
    dist_ma20 = abs(curr - ma20) / ma20
    if dist_ma20 > cfg.ma20_band:
        return {"signal": False}

    # ③ VCP 수렴
    if len(closes) < 30: return {"signal": False}
    recent_amp = (max(closes[0:15]) - min(closes[0:15])) / min(closes[0:15]) if min(closes[0:15]) > 0 else 0
    prev_amp   = (max(closes[15:30]) - min(closes[15:30])) / min(closes[15:30]) if min(closes[15:30]) > 0 else 0
    if prev_amp == 0 or recent_amp >= prev_amp * cfg.vcp_ratio:
        return {"signal": False}

    # ④ 거래량 마름
    if len(volumes) < 10: return {"signal": False}
    vol_avg = sum(volumes) / len(volumes)
    vol_rec = sum(volumes[:5]) / 5
    if vol_avg == 0 or vol_rec >= vol_avg * cfg.vol_dry_ratio:
        return {"signal": False}

    # ⑤ 스마트머니
    f_nets = [d["f_net"] for d in data[:10]]
    i_nets = [d["i_net"] for d in data[:10]]
    f_pos  = sum(1 for v in f_nets if v > 0)
    i_pos  = sum(1 for v in i_nets if v > 0)
    if f_pos < 2 and i_pos < 2 and sum(f_nets) <= 0 and sum(i_nets) <= 0:
        return {"signal": False}

    # ATR 계산
    atr  = _atr(closes)
    stop = round(curr - atr * cfg.atr_stop_mult, 0)
    tgt  = round(curr + atr * cfg.atr_target_mult, 0)
    if stop <= 0: return {"signal": False}
    stop_pct = (curr - stop) / curr * 100
    tgt_pct  = (tgt - curr) / curr * 100
    if stop_pct > 20 or tgt_pct < 8: return {"signal": False}
    rr = round(tgt_pct / stop_pct, 1) if stop_pct > 0 else 0
    if rr < cfg.min_rr: return {"signal": False}

    return {
        "signal": True,
        "type":   "VCP",
        "curr":   curr,
        "stop":   stop,
        "tgt":    tgt,
        "rr":     rr,
        "score":  70,
    }


def check_trend_signal(data: list, cfg: BacktestConfig) -> dict:
    """
    상승추세 눌림목 신호 체크
    """
    if len(data) < 60:
        return {"signal": False}

    closes  = [d["close"] for d in data if d["close"] > 0]
    volumes = [d["volume"] for d in data if d["volume"] > 0]

    if len(closes) < 60: return {"signal": False}

    curr = closes[0]

    # ① 60일선 위 + 우상향
    ma60 = _ma(closes, 60)
    if ma60 == 0 or curr < ma60: return {"signal": False}
    ma60_prev = _ma(closes[5:65], 60) if len(closes) >= 65 else 0
    if ma60_prev > 0 and ma60 <= ma60_prev: return {"signal": False}

    # ② HH
    if len(closes) < 40: return {"signal": False}
    recent_hi = max(closes[0:20])
    prev_hi   = max(closes[20:40])
    if recent_hi <= prev_hi: return {"signal": False}

    # ③ HL
    recent_lo = min(closes[0:20])
    prev_lo   = min(closes[20:40])
    if recent_lo <= prev_lo: return {"signal": False}

    # ④ 눌림목
    dist_lo = abs(curr - recent_lo) / recent_lo if recent_lo > 0 else 1
    if dist_lo > cfg.pullback_band: return {"signal": False}

    # ⑤ RSI
    rsi = _rsi(closes)
    if not (cfg.rsi_low <= rsi <= cfg.rsi_high): return {"signal": False}

    # ⑥ 거래량
    if len(volumes) < 10: return {"signal": False}
    vol_avg = sum(volumes) / len(volumes)
    vol_rec = sum(volumes[:5]) / 5
    if vol_avg > 0 and vol_rec >= vol_avg * 0.7: return {"signal": False}

    # ATR
    atr  = _atr(closes)
    stop = round(curr - atr * cfg.atr_stop_mult, 0)
    tgt  = round(curr + atr * cfg.atr_target_mult, 0)
    if stop <= 0: return {"signal": False}
    stop_pct = (curr - stop) / curr * 100
    tgt_pct  = (tgt - curr) / curr * 100
    if stop_pct > 20 or tgt_pct < 8: return {"signal": False}
    rr = round(tgt_pct / stop_pct, 1) if stop_pct > 0 else 0
    if rr < cfg.min_rr: return {"signal": False}

    return {
        "signal": True,
        "type":   "TREND",
        "curr":   curr,
        "stop":   stop,
        "tgt":    tgt,
        "rr":     rr,
        "score":  70,
    }


# ══════════════════════════════════════════════════════════════
# 백테스트 엔진
# ══════════════════════════════════════════════════════════════

class LinaBacktest:

    def __init__(self, cfg: BacktestConfig):
        self.cfg    = cfg
        self.trades = []     # 완료 거래
        self.positions = {}  # 현재 보유 {stock_name: {...}}

        if not cfg.end_date:
            self.end_date = datetime.date.today().strftime("%Y-%m-%d")
        else:
            self.end_date = cfg.end_date

        self.cash = cfg.initial_cash

        # 연결
        self.conn = sqlite3.connect(DB_PATH, timeout=15)
        print(f"✅ DB 연결: {DB_PATH}")

    def close(self):
        self.conn.close()

    # ── 전체 날짜 목록 생성 ───────────────────────────────────
    def _get_trade_dates(self) -> list:
        rows = self.conn.execute("""
            SELECT DISTINCT date FROM kr_stock_daily_data
            WHERE date BETWEEN ? AND ?
            ORDER BY date
        """, (self.cfg.start_date, self.end_date)).fetchall()
        return [r[0] for r in rows]

    # ── 특정 날짜 기준 데이터 슬라이싱 ───────────────────────
    def _get_data_as_of(self, stock_name: str, as_of_date: str) -> list:
        """as_of_date 이전 데이터만 반환 (미래 데이터 누출 방지)"""
        rows = self.conn.execute("""
            SELECT date, close_price, volume, foreign_net_buy, institution_net_buy
            FROM kr_stock_daily_data
            WHERE stock_name = ? AND date <= ?
            ORDER BY date DESC
            LIMIT 220
        """, (stock_name, as_of_date)).fetchall()
        return [{"date": r[0], "close": r[1] or 0, "volume": r[2] or 0,
                 "f_net": r[3] or 0, "i_net": r[4] or 0} for r in rows]

    # ── 특정 날짜 종가 조회 ──────────────────────────────────
    def _get_close(self, stock_name: str, date: str) -> float:
        row = self.conn.execute("""
            SELECT close_price FROM kr_stock_daily_data
            WHERE stock_name = ? AND date = ?
        """, (stock_name, date)).fetchone()
        return float(row[0]) if row and row[0] else 0.0

    # ── 매도 체크 ─────────────────────────────────────────────
    def _check_sell(self, date: str):
        for name, pos in list(self.positions.items()):
            curr = self._get_close(name, date)
            if curr <= 0:
                continue

            entry      = pos["entry"]
            stop       = pos["stop"]
            tgt        = pos["tgt"]
            hold_days  = pos["hold_days"]
            qty        = pos["qty"]
            reason     = None

            # 손절
            if stop > 0 and curr <= stop:
                reason = f"손절 {(curr-entry)/entry*100:+.1f}%"

            # 목표가
            elif tgt > 0 and curr >= tgt:
                reason = f"목표달성 {(curr-entry)/entry*100:+.1f}%"

            # 최대 보유일
            elif hold_days >= self.cfg.max_hold_days:
                reason = f"기간초과 {(curr-entry)/entry*100:+.1f}%"

            if reason:
                profit     = (curr - entry) * qty
                profit_pct = (curr - entry) / entry * 100
                self.cash += curr * qty

                self.trades.append({
                    "stock_name":  name,
                    "type":        pos.get("type", "?"),
                    "entry_date":  pos["entry_date"],
                    "exit_date":   date,
                    "entry_price": entry,
                    "exit_price":  curr,
                    "qty":         qty,
                    "profit":      round(profit, 0),
                    "profit_pct":  round(profit_pct, 2),
                    "hold_days":   hold_days,
                    "reason":      reason,
                })
                del self.positions[name]
            else:
                pos["hold_days"] += 1

    # ── 신호 스캔 & 매수 ──────────────────────────────────────
    def _scan_and_buy(self, date: str, all_stocks: list):
        slots = self.cfg.max_positions - len(self.positions)
        if slots <= 0:
            return

        etf_kw = ["KODEX","TIGER","KBSTAR","ARIRANG","HANARO","KOSEF",
                  "TREX","SOL","ACE","PLUS","RISE","KIWOOM","인버스","레버리지"]

        candidates = []

        for stock_name in all_stocks:
            import re
            pure = re.sub(r'\s*(KOSPI|KOSDAQ)\s*\d{6}$', '', stock_name).strip()

            # ETF/우선주 제외
            if any(k in pure for k in etf_kw): continue
            if pure.endswith("우") or pure.endswith("우B"): continue

            # 이미 보유중
            if stock_name in self.positions: continue

            # 데이터 로드
            data = self._get_data_as_of(stock_name, date)
            if len(data) < 30: continue

            # VCP 신호
            sig = check_vcp_signal(data, self.cfg)
            if not sig["signal"]:
                # 추세 신호
                sig = check_trend_signal(data, self.cfg)

            if sig["signal"]:
                candidates.append({
                    "stock_name": stock_name,
                    "pure_name":  pure,
                    **sig,
                })

        # 스코어 순 정렬 (같으면 VCP 우선)
        candidates.sort(key=lambda x: (x["score"], x["type"] == "VCP"), reverse=True)

        for cand in candidates[:slots]:
            curr       = cand["curr"]
            # T+1 시가 근사 (당일 종가 × 1.005)
            entry_price = round(curr * 1.005, 0)
            amount     = min(self.cfg.base_buy_amt, self.cash * 0.95)
            if amount < entry_price: continue
            qty        = max(1, int(amount / entry_price))
            cost       = entry_price * qty

            if cost > self.cash: continue

            self.cash -= cost
            self.positions[cand["stock_name"]] = {
                "name":       cand["pure_name"],
                "type":       cand["type"],
                "entry":      entry_price,
                "stop":       cand["stop"],
                "tgt":        cand["tgt"],
                "qty":        qty,
                "entry_date": date,
                "hold_days":  0,
            }

    # ── 메인 실행 ─────────────────────────────────────────────
    def run(self) -> dict:
        all_stocks  = get_all_stocks(self.conn)
        trade_dates = self._get_trade_dates()

        print(f"\n🚀 [lina 백테스터] 시작")
        print(f"   기간: {self.cfg.start_date} ~ {self.end_date}")
        print(f"   종목: {len(all_stocks)}개 | 거래일: {len(trade_dates)}일")
        print(f"   시드: {self.cfg.initial_cash:,}원 | 1종목: {self.cfg.base_buy_amt:,}원")

        for i, date in enumerate(trade_dates):
            # 매도 체크 먼저
            self._check_sell(date)

            # 매수 신호 스캔 (09:10 이후 = 당일 종가 다음날 시가)
            self._scan_and_buy(date, all_stocks)

            if (i + 1) % 20 == 0:
                total_val = self.cash + sum(
                    self._get_close(s, date) * p["qty"]
                    for s, p in self.positions.items()
                )
                print(f"   [{i+1}/{len(trade_dates)}] {date} | "
                      f"보유:{len(self.positions)} | 평가:{total_val:,.0f}원")

        # 잔여 포지션 청산
        if trade_dates:
            last_date = trade_dates[-1]
            for name, pos in list(self.positions.items()):
                curr = self._get_close(name, last_date)
                if curr > 0:
                    profit     = (curr - pos["entry"]) * pos["qty"]
                    profit_pct = (curr - pos["entry"]) / pos["entry"] * 100
                    self.cash += curr * pos["qty"]
                    self.trades.append({
                        "stock_name":  pos["name"],
                        "type":        pos["type"],
                        "entry_date":  pos["entry_date"],
                        "exit_date":   last_date,
                        "entry_price": pos["entry"],
                        "exit_price":  curr,
                        "qty":         pos["qty"],
                        "profit":      round(profit, 0),
                        "profit_pct":  round(profit_pct, 2),
                        "hold_days":   pos["hold_days"],
                        "reason":      "기간종료",
                    })

        return self._calc_metrics()

    # ── 성과 계산 ─────────────────────────────────────────────
    def _calc_metrics(self) -> dict:
        trades = self.trades
        if not trades:
            return {"error": "거래 없음"}

        total      = len(trades)
        wins       = [t for t in trades if t["profit_pct"] > 0]
        losses     = [t for t in trades if t["profit_pct"] <= 0]
        win_rate   = len(wins) / total * 100
        total_pnl  = sum(t["profit"] for t in trades)
        avg_win    = sum(t["profit_pct"] for t in wins) / len(wins) if wins else 0
        avg_loss   = sum(t["profit_pct"] for t in losses) / len(losses) if losses else 0
        avg_hold   = sum(t["hold_days"] for t in trades) / total

        gross_profit = sum(t["profit"] for t in wins)
        gross_loss   = abs(sum(t["profit"] for t in losses))
        pf           = gross_profit / gross_loss if gross_loss > 0 else 999

        # MDD 계산
        equity = self.cfg.initial_cash
        peak   = equity
        mdd    = 0.0
        for t in sorted(trades, key=lambda x: x["exit_date"]):
            equity += t["profit"]
            peak    = max(peak, equity)
            dd      = (equity - peak) / peak * 100
            mdd     = min(mdd, dd)

        final_equity = self.cfg.initial_cash + total_pnl
        total_return = (final_equity - self.cfg.initial_cash) / self.cfg.initial_cash * 100

        # 전략별 분류
        vcp_trades   = [t for t in trades if t["type"] == "VCP"]
        trend_trades = [t for t in trades if t["type"] == "TREND"]

        return {
            "total_trades":  total,
            "win_rate":      round(win_rate, 1),
            "total_return":  round(total_return, 2),
            "total_pnl":     round(total_pnl, 0),
            "avg_win_pct":   round(avg_win, 2),
            "avg_loss_pct":  round(avg_loss, 2),
            "avg_hold_days": round(avg_hold, 1),
            "profit_factor": round(pf, 2),
            "mdd":           round(mdd, 2),
            "final_equity":  round(final_equity, 0),
            "vcp_trades":    len(vcp_trades),
            "trend_trades":  len(trend_trades),
            "vcp_winrate":   round(sum(1 for t in vcp_trades if t["profit_pct"] > 0) / len(vcp_trades) * 100, 1) if vcp_trades else 0,
            "trend_winrate": round(sum(1 for t in trend_trades if t["profit_pct"] > 0) / len(trend_trades) * 100, 1) if trend_trades else 0,
        }

    def get_trades(self) -> list:
        return self.trades


# ══════════════════════════════════════════════════════════════
# 시나리오 비교
# ══════════════════════════════════════════════════════════════

def run_scenario(name: str, cfg: BacktestConfig) -> dict:
    bt = LinaBacktest(cfg)
    metrics = bt.run()
    trades  = bt.get_trades()
    bt.close()
    return {"name": name, "metrics": metrics, "trades": trades}


def print_report(results: list):
    print("\n" + "=" * 70)
    print("📊 [lina 백테스터 — 시나리오 비교]")
    print("=" * 70)
    print(f"{'시나리오':<25} {'수익률':>8} {'승률':>7} {'PF':>6} {'MDD':>8} {'거래수':>6} {'평균보유':>8}")
    print("-" * 70)

    for r in results:
        m = r["metrics"]
        if "error" in m:
            print(f"{r['name']:<25} 거래없음")
            continue
        print(f"{r['name']:<25} "
              f"{m['total_return']:>+7.1f}% "
              f"{m['win_rate']:>6.1f}% "
              f"{m['profit_factor']:>6.2f} "
              f"{m['mdd']:>7.1f}% "
              f"{m['total_trades']:>6} "
              f"{m['avg_hold_days']:>7.1f}일")

    print("=" * 70)
    print("\n📈 [전략별 상세]")
    for r in results:
        m = r["metrics"]
        if "error" in m: continue
        print(f"\n  [{r['name']}]")
        print(f"    VCP   : {m['vcp_trades']}건 / 승률 {m['vcp_winrate']}%")
        print(f"    추세  : {m['trend_trades']}건 / 승률 {m['trend_winrate']}%")
        print(f"    손익  : 평균수익 {m['avg_win_pct']:+.1f}% / 평균손실 {m['avg_loss_pct']:+.1f}%")
        print(f"    최종  : {int(m['final_equity']):,}원 (시드대비 {m['total_return']:+.1f}%)")

    # 판단 기준
    print("\n" + "─" * 70)
    print("🎯 [판단 기준]")
    print("  PF > 1.3, 승률 > 50%, MDD > -10%  → 현행 유지")
    print("  PF 1.0~1.3                          → 관찰 유지")
    print("  PF < 1.0                            → 파라미터 재조정 필요")


# ══════════════════════════════════════════════════════════════
# 진입점
# ══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="lina 백테스터")
    parser.add_argument("--start",   default="2025-09-01", help="시작일 YYYY-MM-DD")
    parser.add_argument("--end",     default="",           help="종료일 (기본: 오늘)")
    parser.add_argument("--compare", action="store_true",  help="시나리오 비교")
    parser.add_argument("--cash",    type=float, default=5_000_000)
    parser.add_argument("--top",     type=int,   default=0, help="상위 N종목만")
    args = parser.parse_args()

    base = BacktestConfig(
        start_date   = args.start,
        end_date     = args.end,
        initial_cash = args.cash,
    )

    if args.compare:
        scenarios = [
            ("기본(ATR×1.5/3.0)", BacktestConfig(
                start_date=args.start, end_date=args.end,
                atr_stop_mult=1.5, atr_target_mult=3.0)),
            ("공격적(ATR×1.0/3.0)", BacktestConfig(
                start_date=args.start, end_date=args.end,
                atr_stop_mult=1.0, atr_target_mult=3.0)),
            ("보수적(ATR×2.0/4.0)", BacktestConfig(
                start_date=args.start, end_date=args.end,
                atr_stop_mult=2.0, atr_target_mult=4.0)),
            ("VCP전용(MA20±5%)", BacktestConfig(
                start_date=args.start, end_date=args.end,
                ma20_band=0.05)),
            ("최대보유10일", BacktestConfig(
                start_date=args.start, end_date=args.end,
                max_hold_days=10)),
        ]
        results = []
        for name, cfg in scenarios:
            print(f"\n▶ [{name}] 실행 중...")
            results.append(run_scenario(name, cfg))
        print_report(results)

        # JSON 저장
        ts  = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        out = os.path.join(RESULT_DIR, f"lina_backtest_{ts}.json")
        with open(out, "w", encoding="utf-8") as f:
            json.dump([{**r, "trades": r["trades"][:100]}
                       for r in results],
                      f, ensure_ascii=False, indent=2, default=str)
        print(f"\n💾 결과 저장: {out}")

    else:
        result = run_scenario("단일실행", base)
        m = result["metrics"]
        if "error" not in m:
            print(f"\n✅ 완료!")
            print(f"   수익률: {m['total_return']:+.2f}%")
            print(f"   승률  : {m['win_rate']}%")
            print(f"   PF    : {m['profit_factor']}")
            print(f"   MDD   : {m['mdd']}%")
            print(f"   거래수: {m['total_trades']}건")


if __name__ == "__main__":
    main()
