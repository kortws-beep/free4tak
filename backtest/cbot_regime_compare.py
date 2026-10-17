"""
cbot_regime_compare.py — 코인 '장 판단' 방식 3가지를 cbot 실제 매매로 비교 (2026-10-18)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
대장 질문: "비트만으로 장 판단은 부족하다 — 비트+이더 70%, 솔+리플 30%로 종합 판단하면?"
cbot이 실제로 산 매매(수동 제외, 다 팔린 것)마다 산 순간의 장 지표를 계산해서,
"이 지표가 기준 미만이면 매수 쉬기" 규칙이 손해를 얼마나 막았을지(=빠졌을 매매 손익의 반대) 비교한다.
  A. 비트 24시간 등락            — 지금 실전 규칙(-1% 미만 쉬기, 10-17~)
  B. 대장 안: (비트+이더) 70% · (솔+리플) 30% 가중 24시간 등락
  C. 코인판 평균선 위 비율        — 원화 전 코인 중 종가가 평균선 위인 비율(주식 '20일선 위 비율'과 같은 발상)
     C20 = 4시간봉 20개(약 3일) 평균 · C120 = 4시간봉 120개(20일) 평균
24시간 등락은 업비트 10분봉(실전 cbot과 같은 방식: 직전 끝난 봉 종가 vs 24시간 전 봉 종가)을 그때 받아서 계산,
평균선 비율은 backtestc/coin_backtest.db(매일 09:05 갱신)의 직전 끝난 4시간봉 기준.
앞·뒤 절반 기간 둘 다 손해를 막았으면 ✅ — 우연 걸러내기. 주문·저장 없음.
실행:  python backtest/cbot_regime_compare.py [최근 N일, 기본 120]
"""
import os
import sys
import time
import sqlite3
import datetime
from bisect import bisect_right

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cbot_crash_review import _find   # noqa: E402
from cbot_regime_review import load_trades, stats   # noqa: E402

COINS = {"KRW-BTC": .35, "KRW-ETH": .35, "KRW-SOL": .15, "KRW-XRP": .15}
COIN_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backtestc", "coin_backtest.db")
TEN = datetime.timedelta(minutes=10)
DAY = datetime.timedelta(hours=24)
FOUR = datetime.timedelta(hours=4)


def fetch_10m(market: str, since: datetime.datetime) -> dict:
    """{봉 시작시각(KST): 종가} — since 이후 전부(끝난 봉만)."""
    out, to, now = {}, None, datetime.datetime.now()
    while True:
        p = {"market": market, "count": 200}
        if to:
            p["to"] = to
        rows = requests.get("https://api.upbit.com/v1/candles/minutes/10", params=p, timeout=10).json()
        time.sleep(0.12)
        if not isinstance(rows, list) or not rows:
            break
        for c in rows:
            t = datetime.datetime.fromisoformat(c["candle_date_time_kst"])
            if t + TEN <= now:
                out[t] = c["trade_price"]
        if datetime.datetime.fromisoformat(rows[-1]["candle_date_time_kst"]) <= since:
            break
        to = rows[-1]["candle_date_time_utc"] + "Z"
    return out


def chg24(bars: dict, keys: list, t: datetime.datetime):
    i = bisect_right(keys, t - TEN) - 1          # 매수 시각까지 끝난 마지막 10분봉
    if i < 0 or t - keys[i] > TEN * 3:
        return None
    old = bars.get(keys[i] - DAY)
    return None if old is None else (bars[keys[i]] / old - 1) * 100


def breadth_series(ma: int) -> tuple:
    """(정렬된 봉 시각 목록, 비율 목록) — 평균선 계산 가능한 코인이 50개 이상인 봉만."""
    conn = sqlite3.connect(COIN_DB)
    rows = conn.execute("SELECT code, date, close FROM daily_ohlcv ORDER BY code, date").fetchall()
    conn.close()
    above, total = {}, {}
    cur, buf = None, []
    for code, d, c in rows:
        if code != cur:
            cur, buf, s = code, [], 0.0
        buf.append(c)
        s += c
        if len(buf) > ma:
            s -= buf[-ma - 1]
        if len(buf) >= ma:
            total[d] = total.get(d, 0) + 1
            above[d] = above.get(d, 0) + (c > s / ma)
    ks = sorted(d for d, n in total.items() if n >= 50)
    return [datetime.datetime.fromisoformat(d) for d in ks], [above[d] / total[d] * 100 for d in ks]


def at_4h(ts: list, vs: list, t: datetime.datetime):
    i = bisect_right(ts, t - FOUR) - 1           # 매수 시각까지 끝난 마지막 4시간봉
    return None if i < 0 or t - ts[i] > FOUR * 3 else vs[i]


RULES = [("A 비트24h", "btc", (-1, -0.5, 0, 1)),
         ("B 대장안 70/30", "combo", (-1, -0.5, 0, 1)),
         ("C20 3일선 위 비율", "b20", (30, 40, 50)),
         ("C120 20일선 위 비율", "b120", (20, 30, 40))]


def report(trades: list, days: int) -> str:
    ts = sorted(trades, key=lambda x: x["buy_time"])
    half = ts[len(ts) // 2]["buy_time"]
    L = [f"🧭 cbot 장 판단 비교 — 최근 {days}일 끝난 매수 {len(trades)}건 · 전체 {stats(trades)} · 앞뒤 기준 {half:%m-%d}"]
    for name, k, _ in RULES:
        cov = sum(1 for x in trades if x[k] is not None)
        L.append(f"   {name}: 값 있는 매수 {cov}/{len(trades)}")
    L.append("■ 'X 미만이면 매수 쉬기' — 효과 = 빠졌을 매매 손익의 반대(+면 손해를 막음)")
    for name, k, ths in RULES:
        for th in ths:
            sk = [x for x in trades if x[k] is not None and x[k] < th]
            if not sk:
                L.append(f"   {name} < {th:g}: 해당 0건")
                continue
            a = -sum(x["krw"] for x in sk if x["buy_time"] < half)
            b = -sum(x["krw"] for x in sk if x["buy_time"] >= half)
            tag = "✅" if a > 0 and b > 0 else "❌"
            L.append(f"   {name} < {th:g}: 빠지는 {len(sk)}건(손절 {sum(x['loss_exit'] for x in sk)}, "
                     f"승률 {sum(x['krw'] > 0 for x in sk) / len(sk) * 100:.0f}%) · 효과 {(a + b) / 1e4:+.1f}만 "
                     f"(앞 {a / 1e4:+.1f} · 뒤 {b / 1e4:+.1f}) {tag}")
    agree = [x for x in trades if x["btc"] is not None and x["combo"] is not None]
    diff = [x for x in agree if (x["btc"] < -1) != (x["combo"] < -1)]
    L.append(f"■ A와 B가 -1% 기준에서 판단이 갈린 매수 {len(diff)}/{len(agree)}건: {stats(diff)}")
    for x in diff:
        L.append(f"   {x['buy_time']:%m-%d %H:%M} {x['market']:<11} 비트 {x['btc']:+.1f}% · 종합 {x['combo']:+.1f}% → "
                 f"{x['krw'] / 1e4:+.1f}만")
    L.append("   ※ 매수 95건 안팎·한 시기라 규칙 여러 개를 같이 보면 하나쯤은 우연히 좋아 보일 수 있음 — 백테스터로 교차확인")
    return "\n".join(L)


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    trades = load_trades(_find("cbot_trade_history.db"), days)
    start = min(x["buy_time"] for x in trades) - datetime.timedelta(days=2)
    bars = {m: fetch_10m(m, start) for m in COINS}
    keys = {m: sorted(b) for m, b in bars.items()}
    b20, b120 = breadth_series(20), breadth_series(120)
    for x in trades:
        t = x["buy_time"]
        c = {m: chg24(bars[m], keys[m], t) for m in COINS}
        x["btc"] = c["KRW-BTC"]
        x["combo"] = None if None in c.values() else sum(c[m] * w for m, w in COINS.items())
        x["b20"], x["b120"] = at_4h(*b20, t), at_4h(*b120, t)
    print(report(trades, days))
