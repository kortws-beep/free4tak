"""
cbot_month_autopsy.py — cbot 한 달 매매 해부: 요즘 넣은 장치가 그때 있었다면? (2026-10-21)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
대장: "cbot이 계속 손절만 가져간다 — 고친 장치가 진짜 원인을 겨냥한 건지, 다른 구멍이 있는지 확인."
→ 이번 달 실제 매매를 한 건씩 다시 보며, 요즘 넣은 세 장치가 그때 있었다면 어땠을지 따짐:
   ① 비트 24시간 필터(10-16~): 살 때 비트코인 24시간 등락 < -1%면 매수 안 함
   ② 펀딩비 필터(10-18~): 바이낸스 BTC 직전 펀딩비 < 0.003%/8h면 매수 안 함 (backtestc/coin_global.db)
   ③ 보호선(10-18~): +3% 찍은 뒤 +0.3%로 내려오면 전량 매도 (coin_price_ticks.db 30초 시세로 경로 재현)
그리고 손실이 어디서 나왔는지(매도 사유·시간대·보유 시간·AI 점수·고정 4종목 vs 알트) 쪼개 봄.
⚠️ 한 건을 막으면 그 슬롯·돈으로 다른 걸 샀을 수 있음(경로 의존) — '막은 건의 손익 합'은 어림값.
실행: python backtest/cbot_month_autopsy.py [YYYY-MM]   (기본: 이번 달)
"""
import os
import sys
import bisect
import sqlite3
import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRADES_DB = os.path.join(BASE, "cbot_trade_history.db")
TICKS_DB = os.path.join(BASE, "coin_price_ticks.db")
GLOBAL_DB = os.path.join(BASE, "backtestc", "coin_global.db")
BTC_24H_MIN, FUNDING_MIN = -1.0, 0.003          # cbot.py BTC_24H_MIN / BTC_FUNDING_MIN 과 같은 값
PROTECT_ARM, PROTECT_FLOOR = 3.0, 0.3           # cbot.py PROTECT_ARM_RATE / PROTECT_FLOOR_RATE (%)
FIXED = {"KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL"}
LIVE_SINCE = {"btc24": "2026-10-16T11:28", "funding": "2026-10-18T05:45", "protect": "2026-10-18T04:34"}


def load_trades(month: str, db: str = TRADES_DB) -> list:
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute("SELECT market, buy_price, buy_time, sell_price, sell_time, profit_rate, profit_krw, "
                            "sell_reason, ai_score FROM trades WHERE sell_time LIKE ? ORDER BY buy_time",
                            (month + "%",)).fetchall()
    finally:
        conn.close()
    keys = ("market", "buy", "buy_time", "sell", "sell_time", "rate", "krw", "reason", "ai")
    return [dict(zip(keys, r)) for r in rows]


class Ticks:
    def __init__(self, db: str = TICKS_DB):
        self.conn = sqlite3.connect(db)

    def path(self, market: str, t0: str, t1: str) -> list:
        return self.conn.execute("SELECT ts, price FROM price_ticks WHERE market=? AND ts>=? AND ts<=? ORDER BY ts",
                                 (market, t0, t1)).fetchall()

    def at(self, market: str, t: str):
        """t 이전 마지막 시세(10분 안쪽만)."""
        r = self.conn.execute("SELECT ts, price FROM price_ticks WHERE market=? AND ts<=? ORDER BY ts DESC LIMIT 1",
                              (market, t)).fetchone()
        if not r:
            return None
        gap = datetime.datetime.fromisoformat(t) - datetime.datetime.fromisoformat(r[0])
        return r[1] if gap <= datetime.timedelta(minutes=10) else None


def btc_24h(ticks: Ticks, t: str):
    now = ticks.at("KRW-BTC", t)
    ago = ticks.at("KRW-BTC", (datetime.datetime.fromisoformat(t) - datetime.timedelta(hours=24)).isoformat())
    return (now / ago - 1) * 100 if now and ago else None


def load_funding(db: str = GLOBAL_DB) -> tuple:
    """([ts], [rate %]) BTCUSDT, 오래된→최신 (ts는 KST)."""
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute("SELECT ts, rate FROM funding WHERE symbol='BTCUSDT' ORDER BY ts").fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows], [r[1] * 100 for r in rows]


def funding_at(fund: tuple, t: str):
    i = bisect.bisect_right(fund[0], t) - 1
    return fund[1][i] if i >= 0 else None


def protect_exit(path: list, buy: float, sell_time: str):
    """보호선이 있었다면 판 (시각, 수익률%) — 실제 매도 전에 +3% 찍고 +0.3%로 내려온 첫 시점. 없으면 None."""
    armed = False
    for ts, p in path:
        if ts >= sell_time:
            break
        r = (p / buy - 1) * 100
        if r >= PROTECT_ARM:
            armed = True
        elif armed and r <= PROTECT_FLOOR:
            return ts, PROTECT_FLOOR
    return None


def analyze(trades: list, ticks: Ticks, fund: tuple) -> list:
    out = []
    for tr in trades:
        bt, st = tr["buy_time"], tr["sell_time"]
        path = ticks.path(tr["market"], bt, st)
        peak = max(((p / tr["buy"] - 1) * 100 for _, p in path), default=None)
        b24, f = btc_24h(ticks, bt), funding_at(fund, bt)
        pe = protect_exit(path, tr["buy"], st) if tr["rate"] < PROTECT_FLOOR else None
        held = (datetime.datetime.fromisoformat(st) - datetime.datetime.fromisoformat(bt)).total_seconds() / 3600
        out.append(dict(tr, peak=peak, btc24=b24, funding=f, protect=pe, hours=held,
                        block_btc=b24 is not None and b24 < BTC_24H_MIN,
                        block_fund=f is not None and f < FUNDING_MIN))
    return out


def _sum(rows, key="krw"):
    return sum(r[key] for r in rows)


def report(rows: list, month: str) -> str:
    L = []
    tot = _sum(rows)
    wins = [r for r in rows if r["krw"] > 0]
    L.append(f"🔬 cbot {month} 매매 해부 — {len(rows)}건 · 실현 {tot:+,.0f}원 · 승 {len(wins)}건(+{_sum(wins):,.0f}) "
             f"패 {len(rows) - len(wins)}건({_sum([r for r in rows if r['krw'] <= 0]):+,.0f})")
    L.append("\n① 요즘 장치가 그때 있었다면 (막은 건 = 그 매매가 없었던 것으로, 경로 의존 무시한 어림값)")
    b1 = [r for r in rows if r["block_btc"]]
    b2 = [r for r in rows if r["block_fund"]]
    b12 = [r for r in rows if r["block_btc"] or r["block_fund"]]
    pr = [r for r in rows if r["protect"] and not (r["block_btc"] or r["block_fund"])]
    gain_pr = sum((PROTECT_FLOOR - r["rate"]) / 100 * r["buy"] * _qty(r) for r in pr)
    L.append(f"   비트 24시간 < {BTC_24H_MIN:g}%  : {len(b1)}건 막음 → 그 건들 손익 {_sum(b1):+,.0f}원 (막으면 이만큼 덜 잃음/못 범)")
    L.append(f"   펀딩비 < {FUNDING_MIN:g}%      : {len(b2)}건 막음 → {_sum(b2):+,.0f}원")
    L.append(f"   둘 중 하나라도         : {len(b12)}건 막음 → {_sum(b12):+,.0f}원")
    L.append(f"   보호선(+3% 뒤 +0.3%)   : 남은 건 중 {len(pr)}건 더 일찍 +0.3%로 나옴 → 약 {gain_pr:+,.0f}원 개선")
    after = tot - _sum(b12) + gain_pr
    L.append(f"   ⇒ 세 장치 다 있었다면 어림 {after:+,.0f}원 (실제 {tot:+,.0f}원)")
    live = [r for r in rows if r["buy_time"] >= LIVE_SINCE["funding"]]
    L.append(f"   (참고: 세 장치 다 켜진 10-18 이후 산 건 {len(live)}건 · {_sum(live):+,.0f}원)")
    L.append("\n② 막지 못하는 손실 — 세 필터 다 통과한 매매만")
    rest = [r for r in rows if not (r["block_btc"] or r["block_fund"])]
    L += _group(rest, "매도 사유", lambda r: _reason(r["reason"]))
    L += _group(rest, "종목", lambda r: "고정 4종목" if r["market"] in FIXED else "알트")
    L += _group(rest, "산 시각(KST)", lambda r: _hour(r["buy_time"]))
    L += _group(rest, "보유 시간", lambda r: "1시간 미만" if r["hours"] < 1 else "1~6시간" if r["hours"] < 6
                else "6~24시간" if r["hours"] < 24 else "하루 넘게")
    L += _group(rest, "AI 점수", lambda r: "70 미만" if (r["ai"] or 0) < 70 else "70~79" if r["ai"] < 80 else "80 이상")
    L += _group(rest, "산 뒤 최고 수익", lambda r: "-" if r["peak"] is None else "+1% 못 감" if r["peak"] < 1
                else "+1~3%" if r["peak"] < 3 else "+3~10%" if r["peak"] < 10 else "+10% 이상")
    L.append("\n③ 손실 큰 순 10건")
    for r in sorted(rows, key=lambda r: r["krw"])[:10]:
        flags = "".join(x for x, c in (("비트24", r["block_btc"]), (" 펀딩", r["block_fund"]),
                                       (" 보호선", bool(r["protect"]))) if c) or "-"
        pk = f"{r['peak']:+.1f}%" if r["peak"] is not None else "?"
        b24 = f"{r['btc24']:+.1f}%" if r["btc24"] is not None else "?"
        L.append(f"   {r['buy_time'][5:16]} {r['market']:<11} {r['krw']:>+9,.0f}원 {r['rate']:+6.2f}% {_reason(r['reason']):<6}"
                 f" 최고 {pk:>7} · 비트24 {b24:>6} · AI {r['ai']} · {r['hours']:.1f}h · 걸렸을 장치: {flags}")
    return "\n".join(L)


def _qty(r):
    return abs(r["krw"] / (r["sell"] - r["buy"])) if r["sell"] != r["buy"] else 0


def _reason(s: str) -> str:
    s = s or "-"
    for k in ("급락감지", "보호선", "손절", "트레일링", "익절", "목표", "수동", "정체"):
        if k in s:
            return k
    return s[:6]


def _hour(t: str) -> str:
    h = int(t[11:13])
    return "00~06시" if h < 6 else "06~12시" if h < 12 else "12~18시" if h < 18 else "18~24시"


def _group(rows: list, title: str, key) -> list:
    g = {}
    for r in rows:
        g.setdefault(key(r), []).append(r)
    out = [f"   [{title}]"]
    for k, rs in sorted(g.items(), key=lambda x: _sum(x[1])):
        w = sum(1 for r in rs if r["krw"] > 0)
        out.append(f"     {k:<12} {len(rs):>3}건 승 {w:>2} · 합계 {_sum(rs):>+10,.0f}원 · 평균 {sum(r['rate'] for r in rs) / len(rs):+.2f}%")
    return out


if __name__ == "__main__":
    month = next((a for a in sys.argv[1:] if len(a) == 7), datetime.date.today().strftime("%Y-%m"))
    rows = analyze(load_trades(month), Ticks(), load_funding())
    print(report(rows, month))
