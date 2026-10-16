"""
cbot_regime_review.py — cbot 매매를 "그때 장 상태"별로 나눠 보기 (2026-10-17, 로드맵 4번 위험회피 필터)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

10-15 CPI 쇼크 때 cbot 손절이 몰렸다. "위험회피 장엔 cbot 신규매수 쉬기" 규칙을 넣을지 숫자로 본다.
cbot은 이미 공포탐욕 20 미만이면 매수를 멈추므로, 그 위 구간 안에서 더 나눌 게 있나가 질문.

cbot_trade_history.db(매수 시점의 market_status·공포탐욕이 같이 저장됨) + coin_price_ticks.db(30초 시세,
비트코인 24시간 흐름 계산용)로 봇 매수 1건(분할매도는 합침)마다:
  · 공포탐욕 구간 / 비트 상태(normal·weak·stop) / 매수 시점 비트 24시간 등락 / 비트가 24시간 평균 위·아래
구간별 건수·승률·손절 비중·손익 합계 → "이 조건이면 매수 쉬기" 규칙별로 빠졌을 손익(앞·뒤 절반 기간으로
나눠 둘 다 같은 방향인지도). 주문·저장 없음. 수동매매(ai_score=-1)는 뺌.
실행:  python backtest/cbot_regime_review.py [최근 N일, 기본 90]   (stock_bot 폴더에서)
"""
import os
import sys
import sqlite3
import datetime
from bisect import bisect_left, bisect_right

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cbot_crash_review import _find, _dt   # noqa: E402

LOSS_WORDS = ("손절", "급락감지")
BTC = "KRW-BTC"


def load_trades(trade_db: str, days: int) -> list:
    """매수 1건 = (market, buy_time) 묶음 — 분할매도 행 손익을 합치고, 다 팔린 것만."""
    since = (datetime.datetime.now() - datetime.timedelta(days=days)).isoformat()
    conn = sqlite3.connect(trade_db)
    try:
        rows = conn.execute("""SELECT market, buy_time, buy_price, qty, sell_time, profit_krw, sell_reason,
                                      market_status, fear_greed, ai_score
                               FROM trades WHERE buy_time >= ? ORDER BY buy_time""", (since,)).fetchall()
    finally:
        conn.close()
    g = {}
    for m, bt, bp, q, st, pk, why, ms, fg, ai in rows:
        if ai == -1:
            continue
        x = g.setdefault((m, bt), {"market": m, "buy_time": _dt(bt), "status": ms or "?", "fg": fg,
                                   "krw": 0.0, "cost": 0.0, "open": False, "loss_exit": False, "last": None})
        x["cost"] += (bp or 0) * (q or 0)
        if st is None:
            x["open"] = True
            continue
        x["krw"] += pk or 0.0
        if any(w in (why or "") for w in LOSS_WORDS):
            x["loss_exit"] = True
        x["last"] = max(x["last"] or st, st)
    return [x for x in g.values() if not x["open"] and x["last"]]


class BtcTicks:
    def __init__(self, tick_db: str, start: datetime.datetime):
        conn = sqlite3.connect(tick_db)
        try:
            rows = conn.execute("SELECT ts, price FROM price_ticks WHERE market=? AND ts>=? ORDER BY ts",
                                (BTC, (start - datetime.timedelta(days=2)).isoformat(timespec="seconds"))).fetchall()
        except sqlite3.OperationalError:
            rows = []
        finally:
            conn.close()
        self.t = [_dt(a) for a, _ in rows]
        self.p = [b for _, b in rows]

    def at(self, t: datetime.datetime):
        """(24시간 등락 %, 24시간 평균 대비 %) — 24시간 전 시세가 없으면 (None, None)."""
        i = bisect_right(self.t, t) - 1          # 매수 시각 바로 전(같은 시각 포함) 시세
        if i < 0:
            return None, None
        if (t - self.t[i]).total_seconds() > 600:
            return None, None
        j = bisect_left(self.t, t - datetime.timedelta(hours=24))
        if j >= i or (self.t[j] - (t - datetime.timedelta(hours=24))).total_seconds() > 1800:
            return None, None
        win = self.p[j:i + 1]
        return (self.p[i] / self.p[j] - 1) * 100, (self.p[i] / (sum(win) / len(win)) - 1) * 100


def enrich(trades: list, btc: BtcTicks) -> list:
    for x in trades:
        x["btc24"], x["btc_ma"] = btc.at(x["buy_time"])
    return trades


def fg_bucket(v):
    if v is None:
        return "?"
    return "20~29" if v < 30 else "30~44" if v < 45 else "45~59" if v < 60 else "60+"


def btc_bucket(v):
    if v is None:
        return "?"
    return "-3%↓" if v < -3 else "-3~-1%" if v < -1 else "-1~+1%" if v <= 1 else "+1%↑"


def stats(xs: list) -> str:
    if not xs:
        return "0건"
    win = sum(1 for x in xs if x["krw"] > 0) / len(xs) * 100
    loss = sum(1 for x in xs if x["loss_exit"]) / len(xs) * 100
    tot = sum(x["krw"] for x in xs)
    return f"{len(xs)}건 · 승률 {win:.0f}% · 손절 {loss:.0f}% · 합계 {tot / 1e4:+,.1f}만 · 건당 {tot / len(xs) / 1e4:+.2f}만"


RULES = [
    ("공포탐욕 30 미만 쉬기", lambda x: x["fg"] is not None and x["fg"] < 30),
    ("공포탐욕 45 미만 쉬기", lambda x: x["fg"] is not None and x["fg"] < 45),
    ("비트 상태 weak/stop 쉬기", lambda x: x["status"] in ("weak", "stop")),
    ("비트 24시간 -3%↓ 쉬기", lambda x: x["btc24"] is not None and x["btc24"] < -3),
    ("비트 24시간 -1%↓ 쉬기", lambda x: x["btc24"] is not None and x["btc24"] < -1),
    ("비트 24시간 +1% 미만 쉬기(오를 때만 매수)", lambda x: x["btc24"] is not None and x["btc24"] < 1),
    ("비트 24시간 평균 아래 쉬기", lambda x: x["btc_ma"] is not None and x["btc_ma"] < 0),
    ("비트 평균 아래 + 공포탐욕 45 미만", lambda x: x["btc_ma"] is not None and x["btc_ma"] < 0
     and x["fg"] is not None and x["fg"] < 45),
]


def report(trades: list, days: int) -> str:
    L = [f"🪙 cbot 매수 장 상태별 성적 — 최근 {days}일, 끝난 매수 {len(trades)}건 (수동매매 제외)"]
    if not trades:
        return L[0] + "\n   해당 매매 없음"
    cov = sum(1 for x in trades if x["btc24"] is not None)
    L.append(f"   전체: {stats(trades)} · 비트 24시간 시세 있는 건 {cov}/{len(trades)}")
    for title, key, order in (
            ("공포탐욕(매수 시점)", lambda x: fg_bucket(x["fg"]), ["20~29", "30~44", "45~59", "60+", "?"]),
            ("비트 상태(cbot market_status)", lambda x: x["status"], ["normal", "weak", "stop", "?"]),
            ("비트 24시간 등락", lambda x: btc_bucket(x["btc24"]), ["-3%↓", "-3~-1%", "-1~+1%", "+1%↑", "?"]),
            ("비트 24시간 평균 대비", lambda x: "?" if x["btc_ma"] is None else "평균 아래" if x["btc_ma"] < 0
             else "평균 위", ["평균 아래", "평균 위", "?"])):
        L.append(f"■ {title}")
        for b in order:
            xs = [x for x in trades if key(x) == b]
            if xs:
                L.append(f"   {b:<8} {stats(xs)}")
    ts = sorted(trades, key=lambda x: x["buy_time"])
    half = ts[len(ts) // 2]["buy_time"]
    L.append(f"■ 규칙 시뮬레이션 — 쉬었으면 빠졌을 매매 (+면 손해 막음 / 앞·뒤 절반 기준 {half:%m-%d})")
    for name, cond in RULES:
        sk = [x for x in trades if cond(x)]
        if not sk:
            L.append(f"   {name}: 해당 0건")
            continue
        a = -sum(x["krw"] for x in sk if x["buy_time"] < half)
        b = -sum(x["krw"] for x in sk if x["buy_time"] >= half)
        same = "✅ 앞뒤 같은 방향" if a > 0 and b > 0 else "❌ 앞뒤 엇갈림" if a * b < 0 else "➖"
        L.append(f"   {name}: 빠지는 {len(sk)}건 (손절 {sum(x['loss_exit'] for x in sk)}) · 효과 "
                 f"{-sum(x['krw'] for x in sk) / 1e4:+,.1f}만 (앞 {a / 1e4:+.1f} · 뒤 {b / 1e4:+.1f}) {same}")
    L.append("   ※ 건수가 적으면(구간당 20건 미만) 우연일 수 있음 — 규칙은 앞뒤 같은 방향 + 효과 큰 것만 후보")
    return "\n".join(L)


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 90
    trades = load_trades(_find("cbot_trade_history.db"), days)
    start = min((x["buy_time"] for x in trades), default=datetime.datetime.now())
    print(report(enrich(trades, BtcTicks(_find("coin_price_ticks.db"), start)), days))
