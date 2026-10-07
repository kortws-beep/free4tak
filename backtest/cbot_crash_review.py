"""
cbot_crash_review.py — cbot 급락감지/손절 매도의 "그 뒤" 분석 (2026-10-07)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

대장 문제제기: "순간 폭락으로 털리고(급락감지·손절) 일손실 -15만 한도에 걸려
멈춘다." 정말 그런지, 얼마나 자주인지 숫자로 본다.

cbot_trade_history.db(매매이력) + coin_price_ticks.db(30초 시세)로
  1. 급락감지/손절로 판 뒤 10분·30분·1시간·2시간·4시간 후 가격(매도가 대비 %)
     → 바닥에서 판 건지(곧 회복) 진짜 하락이었는지
  2. 같은 15분 안에 여러 코인이 같이 털렸는지(시장 전체 급락 = 군집)
  3. 일손실 한도(-15만)에 걸린 날과 그 시각, 그날 남은 시간
실행:  python backtest/cbot_crash_review.py [최근 N일, 기본 60]
       (stock_bot 폴더에서 — DB 파일은 cbot 작업폴더 기준으로 찾음)
"""
import os
import sys
import sqlite3
import datetime
from bisect import bisect_left

HORIZONS_MIN = (10, 30, 60, 120, 240)
CLUSTER_MIN = 15
DAILY_LOSS_LIMIT = -150_000
REASONS = ("급락감지", "손절")


def _find(name: str) -> str:
    """cbot 작업폴더가 루트일 수도 bots일 수도 있어 둘 다 찾아본다."""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for d in (base, os.path.join(base, "bots"), os.getcwd()):
        p = os.path.join(d, name)
        if os.path.exists(p):
            return p
    return os.path.join(base, name)


def _dt(s: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(s.replace("T", " ")[:19])


def load_sells(trade_db: str, days: int) -> list:
    since = (datetime.datetime.now() - datetime.timedelta(days=days)).isoformat()
    conn = sqlite3.connect(trade_db)
    try:
        rows = conn.execute("""SELECT market, sell_time, sell_price, profit_rate, profit_krw, sell_reason
            FROM trades WHERE sell_time IS NOT NULL AND sell_time >= ? ORDER BY sell_time""",
                            (since,)).fetchall()
    finally:
        conn.close()
    return [{"market": m, "time": _dt(t), "price": p, "rate": r or 0.0, "krw": k or 0.0,
             "reason": reason or ""} for m, t, p, r, k, reason in rows if p]


class Ticks:
    def __init__(self, tick_db: str):
        self.conn = sqlite3.connect(tick_db)

    def series(self, market: str, start: datetime.datetime, end: datetime.datetime):
        # cbot은 ts를 isoformat('T' 구분)으로 저장 — 같은 형식으로 비교해야 문자열 범위가 맞음
        rows = self.conn.execute(
            "SELECT ts, price FROM price_ticks WHERE market=? AND ts>=? AND ts<=? ORDER BY ts",
            (market, start.isoformat(timespec="seconds"), end.isoformat(timespec="seconds"))).fetchall()
        return [(_dt(t), p) for t, p in rows]


def after(series: list, t: datetime.datetime):
    times = [x[0] for x in series]
    i = bisect_left(times, t)
    return series[i][1] if i < len(series) else None


def analyze(sells: list, ticks: Ticks) -> list:
    crash = [s for s in sells if any(k in s["reason"] for k in REASONS)]
    out = []
    for s in crash:
        ser = ticks.series(s["market"], s["time"], s["time"] + datetime.timedelta(minutes=max(HORIZONS_MIN)))
        res = {}
        for h in HORIZONS_MIN:
            p = after(ser, s["time"] + datetime.timedelta(minutes=h))
            res[h] = (p / s["price"] - 1) * 100 if p else None
        window = [p for _, p in ser]
        cluster = [o for o in crash if o is not s
                   and abs((o["time"] - s["time"]).total_seconds()) <= CLUSTER_MIN * 60]
        out.append(dict(s, after=res,
                        max4h=(max(window) / s["price"] - 1) * 100 if window else None,
                        min4h=(min(window) / s["price"] - 1) * 100 if window else None,
                        cluster=len(cluster)))
    return out


def loss_limit_days(sells: list) -> list:
    """누적 실현손익이 -15만 이하로 내려간 날·시각과 그 원인 매도들."""
    days = {}
    for s in sells:
        days.setdefault(s["time"].date(), []).append(s)
    hits = []
    for d, ss in sorted(days.items()):
        cum = 0.0
        for s in ss:
            cum += s["krw"]
            if cum <= DAILY_LOSS_LIMIT:
                hits.append({"date": d, "time": s["time"], "cum": cum,
                             "trigger": [x for x in ss if x["time"] <= s["time"]][-4:]})
                break
    return hits


def _avg(xs):
    xs = [x for x in xs if x is not None]
    return (sum(xs) / len(xs), len(xs)) if xs else (None, 0)


def report(rows: list, hits: list, days: int) -> str:
    L = [f"📉 cbot 급락감지/손절 매도 그 뒤 — 최근 {days}일, {len(rows)}건"]
    if not rows:
        L.append("   해당 매도 없음(또는 시세틱 기록 없음)")
    for label, grp in (("전체", rows), ("군집(15분 안 다른 코인도 털림)", [r for r in rows if r["cluster"]]),
                       ("단독", [r for r in rows if not r["cluster"]])):
        if not grp:
            continue
        parts = []
        for h in HORIZONS_MIN:
            a, n = _avg([r["after"][h] for r in grp])
            parts.append(f"{h}분 {a:+.2f}%" if a is not None else f"{h}분 -")
        rec = [r for r in grp if r["max4h"] is not None]
        bounced = sum(1 for r in rec if r["max4h"] >= 3.0)
        L.append(f"■ {label} {len(grp)}건 — 매도가 대비 평균: " + " · ".join(parts))
        if rec:
            L.append(f"   4시간 안에 매도가보다 +3% 이상 다시 오른 경우 {bounced}/{len(rec)}건 "
                     f"({bounced / len(rec) * 100:.0f}%) | 4시간 최저 평균 "
                     f"{_avg([r['min4h'] for r in rec])[0]:+.2f}%")
    L.append("")
    L.append("■ 건별 (매도가 대비 30분/1시간/4시간 후, 4시간 최고)")
    for r in rows[-25:]:
        a = r["after"]
        f = lambda v: f"{v:+.1f}%" if v is not None else "-"
        L.append(f"   {r['time']:%m-%d %H:%M} {r['market']:<12} {r['reason'][:14]:<14} 실현 {r['rate']:+.1f}% "
                 f"→ 30분 {f(a[30])} 1시간 {f(a[60])} 4시간 {f(a[240])} 최고 {f(r['max4h'])}"
                 + (f"  [군집 {r['cluster'] + 1}]" if r["cluster"] else ""))
    L.append("")
    L.append(f"■ 일손실 한도({DAILY_LOSS_LIMIT:,}원) 도달한 날 {len(hits)}일")
    for h in hits:
        left = 24 - h["time"].hour
        L.append(f"   {h['date']} {h['time']:%H:%M} 누적 {h['cum']:+,.0f}원 (그날 남은 약 {left}시간 매수 중단) — "
                 + ", ".join(f"{x['market']}({x['reason'][:6]} {x['krw']:+,.0f})" for x in h["trigger"]))
    return "\n".join(L)


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 60
    tdb, kdb = _find("cbot_trade_history.db"), _find("coin_price_ticks.db")
    sells = load_sells(tdb, n)
    print(report(analyze(sells, Ticks(kdb)), loss_limit_days(sells), n))
