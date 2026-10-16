"""
cbot_risk_regime.py — cbot 손절, BTC 20일선 아래일 때 몰리나? (2026-10-17)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

SCENARIOS.md ④ "cbot 위험회피 필터": 10-15 CPI 쇼크 때 cbot 손절이 몰렸음 —
비트코인 이동평균 아래·공포탐욕 극단공포일 때 몰렸는지 확인해 "위험회피 장엔
cbot 신규매수 쉬기" 규칙을 검토한다.

cbot_trade_history.db(trades, market_status/fear_greed 포함) + 업비트 BTC 일봉(20일선)
+ alternative.me 공포탐욕 과거값을 합쳐서 비교.

실행:  python backtest/cbot_risk_regime.py
"""
import os
import sys
import sqlite3

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRADE_DB = os.path.join(BASE, "cbot_trade_history.db")
MA = 20


def btc_daily(count: int = 200) -> list:
    """[(date, close)] 오래된→최신 — 업비트 BTC 일봉."""
    import requests
    r = requests.get("https://api.upbit.com/v1/candles/days",
                      params={"market": "KRW-BTC", "count": count}, timeout=10).json()
    r.sort(key=lambda x: x["candle_date_time_kst"])
    return [(x["candle_date_time_kst"][:10], x["trade_price"]) for x in r]


def fear_greed_history() -> dict:
    """{date: 0~100} — alternative.me 전체 과거값."""
    import requests
    import datetime
    r = requests.get("https://api.alternative.me/fng/?limit=0", timeout=10).json()
    out = {}
    for d in r.get("data", []):
        dt = datetime.datetime.fromtimestamp(int(d["timestamp"])).strftime("%Y-%m-%d")
        out[dt] = int(d["value"])
    return out


def below_ma_map(closes: list, ma: int = MA) -> dict:
    """{date: bool} — 그날 종가가 직전 ma일 평균보다 낮은지(앞 ma일 모자라면 빠짐)."""
    by_date = dict(closes)
    dates = [d for d, _ in closes]
    out = {}
    for i in range(ma, len(dates)):
        avg = sum(by_date[dates[j]] for j in range(i - ma, i)) / ma
        out[dates[i]] = by_date[dates[i]] < avg
    return out


def load_trades(db: str = TRADE_DB) -> list:
    """[(date, profit_krw, profit_rate, sell_reason, market_status, fear_greed)]."""
    conn = sqlite3.connect(db)
    try:
        return conn.execute(
            "SELECT substr(sell_time,1,10), profit_krw, profit_rate, sell_reason, "
            "market_status, fear_greed FROM trades WHERE sell_time IS NOT NULL").fetchall()
    finally:
        conn.close()


def pnl_stats(rows: list) -> dict:
    """[(profit_rate, profit_krw)] → {n, win_rate, pf, net}."""
    if not rows:
        return {"n": 0, "win_rate": 0.0, "pf": 0.0, "net": 0.0}
    n = len(rows)
    wins = sum(1 for r, _ in rows if r > 0)
    gain = sum(k for _, k in rows if k > 0)
    loss = -sum(k for _, k in rows if k < 0)
    return {"n": n, "win_rate": wins / n * 100, "pf": (gain / loss) if loss else float("inf"),
            "net": sum(k for _, k in rows)}


def analyze(trades: list, below_map: dict, fng: dict, fear_th: int = 20) -> dict:
    """{regime: pnl_stats, stops: {regime: 건수}, base_rate: {...}}."""
    stop_rows = [t for t in trades if (t[3] or "").startswith("손절")]
    below_trades = [(r, k) for d, k, r, *_ in trades if below_map.get(d)]
    above_trades = [(r, k) for d, k, r, *_ in trades if d in below_map and not below_map[d]]
    stop_below = sum(1 for d, *_ in stop_rows if below_map.get(d))
    stop_fear = sum(1 for d, *_, fg in stop_rows if (fg or 50) < fear_th)
    base_below = sum(below_map.values()) / len(below_map) * 100 if below_map else 0.0
    base_fear = (sum(1 for v in fng.values() if v < fear_th) / len(fng) * 100) if fng else 0.0
    return {
        "below_ma": pnl_stats(below_trades), "above_ma": pnl_stats(above_trades),
        "n_stops": len(stop_rows), "stop_below_ma_pct": stop_below / len(stop_rows) * 100 if stop_rows else 0.0,
        "stop_fear_pct": stop_fear / len(stop_rows) * 100 if stop_rows else 0.0,
        "base_below_ma_pct": base_below, "base_fear_pct": base_fear,
    }


def report(a: dict) -> str:
    def fmt(s):
        return f"{s['n']}건 · 승률{s['win_rate']:.0f}% · PF{s['pf']:.2f} · 순손익{s['net']:+,.0f}원"
    L = ["🛡️ cbot 위험회피 — BTC 20일선 아래 vs 위"]
    L.append(f"   전체 거래: 아래 {fmt(a['below_ma'])}")
    L.append(f"             위    {fmt(a['above_ma'])}")
    L.append(f"   손절 {a['n_stops']}건 중: MA20 아래 {a['stop_below_ma_pct']:.0f}%(기준치 {a['base_below_ma_pct']:.0f}%) · "
             f"공포<20 {a['stop_fear_pct']:.0f}%(기준치 {a['base_fear_pct']:.0f}%)")
    verdict = "MA20 신호 유효(기준치 초과)" if a["stop_below_ma_pct"] > a["base_below_ma_pct"] + 10 else "MA20 신호 약함"
    fear_verdict = "공포탐욕 신호 유효" if a["stop_fear_pct"] > a["base_fear_pct"] + 10 else "공포탐욕 신호 약함(기준치 이하)"
    L.append(f"   판정: {verdict} / {fear_verdict}")
    return "\n".join(L)


if __name__ == "__main__":
    closes = btc_daily()
    fng = fear_greed_history()
    below_map = below_ma_map(closes)
    trades = load_trades()
    print(report(analyze(trades, below_map, fng)))
