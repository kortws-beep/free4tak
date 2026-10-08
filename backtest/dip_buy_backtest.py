"""
dip_buy_backtest.py — 고정 4종목 눌림목 매수 백테스트 (2026-10-08 대장 아이디어)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

cbot에서 노는 현금 중 100만원으로, BTC·ETH·XRP·SOL(고정 4종목)을
"최근 N일 고점에서 X% 빠지면 산다"는 눌림목 규칙으로 굴리면 어땠을지 본다.
cbot(추세추종)과 반대 성격이라 같이 굴릴 때 손실이 겹치는지도 본다.

데이터: 업비트 일봉(공개 API, 키 불필요)을 최근 N일치 받아 backtestc/coin_daily.db에
  저장해 두고 재사용(다시 받으려면 --refresh). 4시간봉 DB(약 6개월)보다 길게 본다.
가정(보수적으로):
  - 종목당 25만원 슬롯 1개(동시에 같은 코인 1포지션), 수수료 0.05%×2 + 슬리피지 0.1%
  - 진입: 당일 저가가 '직전 N일 고가 × (1−하락폭)' 이하 → 그 가격에 체결(시가가 이미
    아래면 시가)
  - 청산: 익절가(진입가 +TP) / 손절가(진입가 −SL, 0이면 없음) / 최대 보유일 경과 시 종가.
    같은 날 익절·손절 둘 다 닿으면 손절로 침(불리하게). 진입 당일은 손절만 체크.
실행:
  python backtest/dip_buy_backtest.py              # 최근 3년, 기본 조합 비교
  python backtest/dip_buy_backtest.py --days 730 --refresh
"""
import os
import sys
import time
import sqlite3
import argparse
import datetime
import itertools

COINS = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL"]
SLOT_KRW = 250_000
FEE, SLIP = 0.0005, 0.001
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(BASE, "backtestc", "coin_daily.db")


# ── 데이터 ────────────────────────────────────────────────
def fetch_daily(market: str, days: int, session=None) -> list:
    """업비트 일봉 [(date, open, high, low, close)] 오래된→최신."""
    import requests
    s = session or requests.Session()
    out, to = [], None
    while len(out) < days:
        params = {"market": market, "count": 200}
        if to:
            params["to"] = to
        r = s.get("https://api.upbit.com/v1/candles/days", params=params, timeout=10)
        data = r.json() if r.status_code == 200 else []
        if not data:
            break
        for c in data:
            out.append((c["candle_date_time_kst"][:10], float(c["opening_price"]), float(c["high_price"]),
                        float(c["low_price"]), float(c["trade_price"])))
        to = data[-1]["candle_date_time_utc"].replace("T", " ")
        time.sleep(0.15)
    uniq = {d: row for d, *row in out}
    return sorted([(d, *v) for d, v in uniq.items()])[-days:]


def load(days: int, refresh: bool) -> dict:
    conn = sqlite3.connect(DB)
    conn.execute("CREATE TABLE IF NOT EXISTS daily (code TEXT, date TEXT, open REAL, high REAL, low REAL, "
                 "close REAL, PRIMARY KEY(code, date))")
    data = {}
    for m in COINS:
        n = conn.execute("SELECT COUNT(*) FROM daily WHERE code=?", (m,)).fetchone()[0]
        if refresh or n < days * 0.9:
            rows = fetch_daily(m, days)
            conn.executemany("INSERT OR REPLACE INTO daily VALUES (?,?,?,?,?,?)", [(m, *r) for r in rows])
            conn.commit()
            print(f"  ⬇️ {m} 일봉 {len(rows)}개 저장")
        data[m] = conn.execute("SELECT date, open, high, low, close FROM daily WHERE code=? "
                               "ORDER BY date DESC LIMIT ?", (m, days)).fetchall()[::-1]
    conn.close()
    return data


# ── 시뮬레이션 ────────────────────────────────────────────
def simulate(rows: list, lookback: int, dip: float, tp: float, sl: float, max_hold: int,
             trend_ma: int = 0, wait_new_high: bool = False) -> list:
    """한 코인 거래 목록 [{entry_date, exit_date, ret, days, reason}].
    ★ v2(2026-10-08, 1차 결과 보고): 1차는 판 다음 날에도 '고점 대비 -10%'가 그대로라
      하락장에서 매일 다시 사는(떨어지는 칼) 구조였음 →
      trend_ma: 전날 종가가 N일 이평선 위일 때만(상승 추세 중 눌림만)
      wait_new_high: 청산 후엔 새 N일 고가가 한 번 나온 뒤에만 재진입"""
    trades, pos = [], None
    blocked = False
    start = max(lookback, trend_ma)
    for i in range(start, len(rows)):
        d, o, h, l, c = rows[i]
        if pos is None:
            ref = max(r[2] for r in rows[i - lookback:i])
            if blocked:
                if h >= ref:                       # 새 N일 고가 → 다음부터 다시 매수 가능
                    blocked = False
                continue
            if trend_ma:
                ma = sum(r[4] for r in rows[i - trend_ma:i]) / trend_ma
                if rows[i - 1][4] <= ma:
                    continue
            trig = ref * (1 - dip)
            if l <= trig:
                entry = min(o, trig) * (1 + SLIP)
                pos = {"entry": entry, "i": i, "date": d}
                if sl and l <= entry * (1 - sl):          # 들어간 날 더 빠져 손절
                    trades.append(_close(pos, entry * (1 - sl), d, i, "손절"))
                    pos = None
                    blocked = wait_new_high
            continue
        held = i - pos["i"]
        if sl and l <= pos["entry"] * (1 - sl):
            trades.append(_close(pos, pos["entry"] * (1 - sl), d, i, "손절")); pos = None
        elif h >= pos["entry"] * (1 + tp):
            trades.append(_close(pos, pos["entry"] * (1 + tp), d, i, "익절")); pos = None
        elif held >= max_hold:
            trades.append(_close(pos, c, d, i, "기한")); pos = None
        if pos is None:
            blocked = wait_new_high
    if pos:
        d, *_r, c = rows[-1]
        trades.append(_close(pos, c, d, len(rows) - 1, "보유중"))
    return trades


def _close(pos, price, d, i, reason):
    exit_p = price * (1 - SLIP)
    ret = exit_p / pos["entry"] - 1 - 2 * FEE
    return {"entry_date": pos["date"], "exit_date": d, "ret": ret, "days": i - pos["i"], "reason": reason}


def summarize(all_trades: list) -> dict:
    if not all_trades:
        return {"n": 0}
    rets = [t["ret"] for t in all_trades]
    wins = [r for r in rets if r > 0]
    loss = [r for r in rets if r <= 0]
    pnl = sum(r * SLOT_KRW for r in rets)
    # 100만 슬리브 손익곡선(청산일 기준 누적) → 최대 낙폭(원)
    cum, peak, mdd = 0.0, 0.0, 0.0
    for t in sorted(all_trades, key=lambda x: x["exit_date"]):
        cum += t["ret"] * SLOT_KRW
        peak = max(peak, cum); mdd = min(mdd, cum - peak)
    pf = (sum(wins) / -sum(loss)) if loss and sum(loss) < 0 else float("inf")
    return {"n": len(rets), "win": len(wins) / len(rets) * 100, "avg": sum(rets) / len(rets) * 100,
            "pnl": pnl, "pf": pf, "mdd": mdd, "worst": min(rets) * 100,
            "hold": sum(t["days"] for t in all_trades) / len(all_trades),
            "open": sum(1 for t in all_trades if t["reason"] == "보유중")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=1095)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--v1", action="store_true", help="1차 규칙(추세필터·재진입대기 없음) 조합")
    args = ap.parse_args()
    data = load(args.days, args.refresh)
    span = f"{data[COINS[0]][0][0]} ~ {data[COINS[0]][-1][0]}" if data[COINS[0]] else "-"
    print(f"\n📉 고정 4종목 눌림목 매수 — 기간 {span} | 종목당 {SLOT_KRW:,}원(합 100만)")
    print(f"{'조건':<34}{'거래':>5}{'승률':>7}{'평균':>8}{'PF':>6}{'누적손익':>12}{'최대낙폭':>11}{'최악':>8}{'보유일':>7}")
    if args.v1:
        grid = [(lb, dip, tp, sl, mh, 0, False) for lb, dip, tp, sl, mh in
                itertools.product((7, 10), (0.10,), (0.05, 0.08, 0.12), (0.0, 0.08, 0.12), (10, 20))]
    else:   # v2: 추세필터(없음/50일/200일) × 재진입대기 항상
        grid = [(lb, 0.10, tp, sl, 20, ma, True) for lb, ma, tp, sl in
                itertools.product((7, 10), (0, 50, 200), (0.08, 0.12), (0.0, 0.12))]
    rows = []
    for lb, dip, tp, sl, mh, ma, wait in grid:
        trades = [t for m in COINS for t in simulate(data[m], lb, dip, tp, sl, mh, ma, wait)]
        s = summarize(trades)
        if not s["n"]:
            continue
        name = (f"{lb}일-{dip:.0%} 익+{tp:.0%} 손{'-' + format(sl, '.0%') if sl else 'X'} {mh}일"
                + (f" MA{ma}" if ma else "") + (" 대기" if wait else ""))
        rows.append((s["pnl"], name, s))
    for _, name, s in sorted(rows, key=lambda x: -x[0]):
        pf = f"{s['pf']:.2f}" if s["pf"] != float("inf") else "∞"
        print(f"{name:<34}{s['n']:>5}{s['win']:>6.0f}%{s['avg']:>+7.2f}%{pf:>6}{s['pnl']:>+12,.0f}"
              f"{s['mdd']:>+11,.0f}{s['worst']:>+7.1f}%{s['hold']:>7.1f}")
    print("\n※ 누적손익·최대낙폭은 100만원(25만×4) 기준, 수수료·슬리피지 반영. '보유중'은 마지막 날 종가로 평가.")


if __name__ == "__main__":
    sys.exit(main())
