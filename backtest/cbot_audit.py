"""
cbot_audit.py — cbot 전면 재점검 (2026-10-17, 대장: "손실이 너무 커진다 — 다시 만드는 한이 있더라도")
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

"고칠까, 다시 만들까"를 숫자로 정하려고 cbot 매매 전체를 한 번에 뜯어본다. 핵심 질문은 하나:
  ▶ 매수가 틀렸나(사고 나서 오르지도 않음) 매도가 틀렸나(올랐는데 못 먹고 손실로 끝남)?
cbot_trade_history.db(매매·눌림목) + coin_price_ticks.db(30초 시세)로:
  1. 전체 — 건수·승률·PF·평균 익/손·건당 기대값, 수수료(업비트 왕복 0.1%) 뺀 순손익
  2. 월별 흐름 — 나빠지는 중인가
  3. 청산 사유별 / 코인별(최악 5·최고 5) / 매수 시각대(야간 23~06시) / AI 점수 / 보유시간
  4. ★ 보유 중 최고(MFE)·최저(MAE) — 30초 시세로 매매마다 계산
     · 진 매매 중 한 번이라도 +2%·+3% 찍었던 것 = "매도 문제"(올랐는데 놓침)
     · 진 매매 중 +1%도 못 찍은 것 = "매수 문제"(사자마자 내려감)
     · 이긴 매매의 최저 — 손절선이 너무 좁/넓은지
  5. 같은 기간 비트코인 등락(그냥 들고 있었으면)
  6. 눌림목 주머니(dip_trades) 따로
수동매매(ai_score=-1)는 뺌. 주문·저장 없음.
실행:  python backtest/cbot_audit.py [최근 N일, 기본 120]   (stock_bot 폴더에서)
"""
import os
import sys
import sqlite3
import datetime
from bisect import bisect_left

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cbot_crash_review import _find, _dt   # noqa: E402

FEE = 0.001          # 업비트 수수료 0.05% × 매수·매도
NIGHT = (23, 6)


def reason_group(r: str) -> str:
    r = r or ""
    for key, g in (("급락감지", "급락감지"), ("손절", "손절"), ("보호선", "보호선(올린 손절)"), ("본절", "본절보호"),
                   ("트레일", "트레일링"), ("익절", "익절"), ("목표", "익절"), ("정체", "정체교체"),
                   ("BTC", "BTC중단"), ("기한", "보유기한"), ("수동", "수동")):
        if key in r:
            return g
    return "기타"


def load(trade_db: str, days: int) -> list:
    """매수 1건 = (market, buy_time) — 분할매도 합침, 다 팔린 것만, 수동 제외."""
    since = (datetime.datetime.now() - datetime.timedelta(days=days)).isoformat()
    conn = sqlite3.connect(trade_db)
    try:
        rows = conn.execute("""SELECT market, buy_time, buy_price, qty, sell_time, sell_price, profit_krw, sell_reason,
                                      ai_score FROM trades WHERE buy_time >= ? ORDER BY buy_time, id""",
                            (since,)).fetchall()
    finally:
        conn.close()
    g = {}
    for m, bt, bp, q, st, sp, pk, why, ai in rows:
        if ai == -1 or not bp:
            continue
        x = g.setdefault((m, bt), {"market": m, "buy": _dt(bt), "bp": bp, "cost": 0.0, "krw": 0.0, "ai": ai,
                                   "open": False, "sells": []})
        x["cost"] += bp * (q or 0)
        if st is None:
            x["open"] = True
            continue
        x["krw"] += pk if pk is not None else ((sp or bp) - bp) * (q or 0)
        x["sells"].append((_dt(st), why or ""))
    out = []
    for x in g.values():
        if x["open"] or not x["sells"]:
            continue
        x["sells"].sort()
        x["sell"] = x["sells"][-1][0]
        x["reason"] = reason_group(x["sells"][-1][1])
        x["partial"] = len(x["sells"]) > 1
        x["net"] = x["krw"] - x["cost"] * FEE
        x["rate"] = x["krw"] / x["cost"] * 100 if x["cost"] else 0.0
        x["hours"] = (x["sell"] - x["buy"]).total_seconds() / 3600
        out.append(x)
    return sorted(out, key=lambda x: x["buy"])


def load_dip(trade_db: str, days: int) -> list:
    since = (datetime.datetime.now() - datetime.timedelta(days=days)).isoformat()
    conn = sqlite3.connect(trade_db)
    try:
        return conn.execute("SELECT market, profit_krw, profit_rate, amount, sell_reason FROM dip_trades "
                            "WHERE sell_time IS NOT NULL AND buy_time >= ?", (since,)).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


class Ticks:
    def __init__(self, tick_db: str):
        try:
            self.conn = sqlite3.connect(tick_db)
            self.conn.execute("SELECT 1 FROM price_ticks LIMIT 1")
        except sqlite3.Error:
            self.conn = None

    def range(self, market: str, a: datetime.datetime, b: datetime.datetime):
        """(최고, 최저, 틱 수) — 없으면 None."""
        if not self.conn:
            return None
        r = self.conn.execute("SELECT MAX(price), MIN(price), COUNT(*) FROM price_ticks WHERE market=? AND ts>=? AND ts<=?",
                              (market, a.isoformat(timespec="seconds"), b.isoformat(timespec="seconds"))).fetchone()
        return r if r and r[2] else None

    def price_at(self, market: str, t: datetime.datetime):
        if not self.conn:
            return None
        r = self.conn.execute("SELECT price FROM price_ticks WHERE market=? AND ts<=? ORDER BY ts DESC LIMIT 1",
                              (market, t.isoformat(timespec="seconds"))).fetchone()
        return r[0] if r else None


def add_excursion(trades: list, ticks: Ticks) -> list:
    for x in trades:
        r = ticks.range(x["market"], x["buy"], x["sell"])
        x["mfe"] = (r[0] / x["bp"] - 1) * 100 if r else None
        x["mae"] = (r[1] / x["bp"] - 1) * 100 if r else None
    return trades


def stat(xs: list) -> dict:
    n = len(xs)
    wins = [x["net"] for x in xs if x["net"] > 0]
    losses = [x["net"] for x in xs if x["net"] <= 0]
    gain, loss = sum(wins), -sum(losses)
    return {"n": n, "win": len(wins) / n * 100 if n else 0, "pf": gain / loss if loss else float("inf"),
            "net": gain - loss, "avg_w": gain / len(wins) if wins else 0, "avg_l": -loss / len(losses) if losses else 0}


def line(label: str, xs: list) -> str:
    if not xs:
        return f"   {label:<12} 0건"
    s = stat(xs)
    pf = "∞" if s["pf"] == float("inf") else f"{s['pf']:.2f}"
    return (f"   {label:<12} {s['n']:>3}건 · 승률 {s['win']:3.0f}% · PF {pf:>4} · 순손익 {s['net'] / 1e4:+7.1f}만 · "
            f"건당 {s['net'] / s['n'] / 1e4:+.2f}만")


def _pct(v):
    return f"{v:+.1f}%" if v is not None else "-"


def report(trades: list, days: int, dip: list = None, btc=None) -> str:
    L = [f"🔧 cbot 전면 재점검 — 최근 {days}일, 끝난 봇 매수 {len(trades)}건 (수동 제외, 수수료 왕복 0.1% 뺀 순손익)"]
    if not trades:
        return L[0] + "\n   해당 매매 없음"
    s = stat(trades)
    gross = sum(x["krw"] for x in trades)
    L.append(f"■ 전체: 승률 {s['win']:.0f}% · PF {s['pf']:.2f} · 평균 이익 {s['avg_w'] / 1e4:+.2f}만 / 평균 손실 "
             f"{s['avg_l'] / 1e4:+.2f}만 (손익비 {abs(s['avg_w'] / s['avg_l']) if s['avg_l'] else 0:.2f}) · "
             f"순손익 {s['net'] / 1e4:+,.1f}만 (수수료 전 {gross / 1e4:+,.1f}만 · 수수료 {(gross - s['net']) / 1e4:,.1f}만)")
    if btc is not None:
        L.append(f"   같은 기간 비트코인 {btc:+.1f}% (그냥 들고 있었으면)")
    L.append("■ 월별")
    months = {}
    for x in trades:
        months.setdefault(x["buy"].strftime("%Y-%m"), []).append(x)
    for m in sorted(months):
        L.append(line(m, months[m]))
    L.append("■ 청산 사유별 (마지막 매도 기준)")
    groups = {}
    for x in trades:
        groups.setdefault(x["reason"], []).append(x)
    for g, xs in sorted(groups.items(), key=lambda kv: stat(kv[1])["net"]):
        L.append(line(g, xs))
    L.append("■ 코인 — 메이저(BTC·ETH) / 알트")
    L.append(line("메이저", [x for x in trades if x["market"] in ("KRW-BTC", "KRW-ETH")]))
    L.append(line("알트", [x for x in trades if x["market"] not in ("KRW-BTC", "KRW-ETH")]))
    coins = {}
    for x in trades:
        coins.setdefault(x["market"], []).append(x)
    ranked = sorted(coins.items(), key=lambda kv: stat(kv[1])["net"])
    L.append("   최악 5: " + " · ".join(f"{m[4:]} {len(xs)}건 {stat(xs)['net'] / 1e4:+.1f}만" for m, xs in ranked[:5]))
    L.append("   최고 5: " + " · ".join(f"{m[4:]} {len(xs)}건 {stat(xs)['net'] / 1e4:+.1f}만" for m, xs in ranked[::-1][:5]))
    L.append("■ 매수 시각대")
    night = lambda x: x["buy"].hour >= NIGHT[0] or x["buy"].hour < NIGHT[1]
    L.append(line("야간 23~06시", [x for x in trades if night(x)]))
    L.append(line("주간", [x for x in trades if not night(x)]))
    L.append("■ AI 점수")
    for lo, hi in ((0, 55), (55, 65), (65, 75), (75, 101)):
        L.append(line(f"{lo}~{hi - 1}", [x for x in trades if x["ai"] is not None and lo <= x["ai"] < hi]))
    L.append("■ 보유시간")
    for lo, hi, lab in ((0, 1, "1시간 안"), (1, 6, "1~6시간"), (6, 24, "6~24시간"), (24, 72, "1~3일"), (72, 1e9, "3일+")):
        L.append(line(lab, [x for x in trades if lo <= x["hours"] < hi]))
    ex = [x for x in trades if x.get("mfe") is not None]
    L.append(f"■ ★ 보유 중 최고(MFE)·최저(MAE) — 30초 시세 있는 {len(ex)}/{len(trades)}건")
    if ex:
        lose = [x for x in ex if x["net"] <= 0]
        win = [x for x in ex if x["net"] > 0]
        if lose:
            sell_p = [x for x in lose if x["mfe"] >= 2]
            buy_p = [x for x in lose if x["mfe"] < 1]
            L.append(f"   진 매매 {len(lose)}건: 한 번이라도 +2%↑ 찍었던 것 {len(sell_p)}건({len(sell_p) / len(lose) * 100:.0f}%,"
                     f" {sum(x['net'] for x in sell_p) / 1e4:+.1f}만) = 매도 문제(올랐는데 놓침)")
            L.append(f"      +1%도 못 찍은 것 {len(buy_p)}건({len(buy_p) / len(lose) * 100:.0f}%, "
                     f"{sum(x['net'] for x in buy_p) / 1e4:+.1f}만) = 매수 문제(사자마자 내려감)")
            L.append(f"      +3%↑ 찍고도 진 것 {sum(1 for x in lose if x['mfe'] >= 3)}건 · 진 매매 평균 최고 "
                     f"{_pct(sum(x['mfe'] for x in lose) / len(lose))} / 평균 최저 {_pct(sum(x['mae'] for x in lose) / len(lose))}")
        if win:
            L.append(f"   이긴 매매 {len(win)}건: 평균 최고 {_pct(sum(x['mfe'] for x in win) / len(win))} · 판 수익률 평균 "
                     f"{_pct(sum(x['rate'] for x in win) / len(win))} (최고 대비 남긴 몫) · 평균 최저 "
                     f"{_pct(sum(x['mae'] for x in win) / len(win))} · 중간에 -3%↓ 갔다 온 것 "
                     f"{sum(1 for x in win if x['mae'] <= -3)}건")
        for lo, hi, lab in ((-1e9, 1, "+1% 미만"), (1, 2, "+1~2%"), (2, 3, "+2~3%"), (3, 5, "+3~5%"), (5, 1e9, "+5%↑")):
            L.append(line(f"최고 {lab}", [x for x in ex if lo <= x["mfe"] < hi]))
    if dip:
        k = sum(r[1] or 0 for r in dip)
        L.append(f"■ 눌림목 주머니(별도 100만): 끝난 {len(dip)}건 · 승 {sum(1 for r in dip if (r[1] or 0) > 0)} · "
                 f"합계 {k / 1e4:+.1f}만")
    L.append("※ 판단: 매수 문제 비중이 크면 진입 신호부터 다시(재설계), 매도 문제 비중이 크면 청산 규칙 손질로 충분")
    return "\n".join(L)


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    tdb = _find("cbot_trade_history.db")
    trades = load(tdb, days)
    ticks = Ticks(_find("coin_price_ticks.db"))
    add_excursion(trades, ticks)
    btc = None
    if trades:
        a = ticks.price_at("KRW-BTC", trades[0]["buy"])
        b = ticks.price_at("KRW-BTC", datetime.datetime.now())
        btc = (b / a - 1) * 100 if a and b else None
    print(report(trades, days, load_dip(tdb, days), btc))
