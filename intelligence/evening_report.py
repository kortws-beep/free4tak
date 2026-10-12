"""
evening_report.py — 저녁 봇 성적표 (2026-10-12)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

대장: "개입 없이 돌려도 되나" 점검 3번 — 매일 장 마감 뒤 세 봇 성적을 한 장으로.
스크립트를 안 돌려도 리나가 알아서 보낸다(16:00, 거래일). 수동: 리나에서 !성적표
  · 시장 국면(내일 매수가 절반인지)
  · 봇별 오늘 매도 건수·승패·손절 수·실현손익, 지금 보유 수, 이번 달 누적
    (master_trades.db — 세 봇이 매도할 때마다 기록, cbot은 오늘 0시부터)
  · cbot 눌림목 주머니(보유·오늘 청산)
  · 일손실 한도 대비 오늘 손익, 세 봇 합산 자동중단 상태, 이상 징후(멈춤 등)
실행:  python intelligence/evening_report.py [YYYY-MM-DD]
"""
import os
import sys
import json
import sqlite3
import datetime

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("core", "intelligence"):
    _p = os.path.join(_BASE, _d)
    if _p not in sys.path:
        sys.path.insert(0, _p)

MASTER_DB = os.path.join(_BASE, "master_trades.db")
BOTS = (("daybot", "데이봇"), ("sbot", "sbot"), ("cbot", "cbot"))
# 봇별 일손실 한도(2026-10-12 대장 결정, 시드의 5%) — 각 봇 코드의 값과 같게
LIMITS = {"daybot": -150_000, "sbot": -500_000, "cbot": -150_000}
STOP_WORDS = ("손절", "급락")


def _find(name: str) -> str:
    for d in (_BASE, os.path.join(_BASE, "bots")):
        p = os.path.join(d, name)
        if os.path.exists(p):
            return p
    return ""


def _q(db: str, sql: str, *a) -> list:
    if not db or not os.path.exists(db):
        return []
    try:
        conn = sqlite3.connect(db, timeout=5)
        try:
            return conn.execute(sql, a).fetchall()
        finally:
            conn.close()
    except sqlite3.OperationalError:
        return []


def bot_day(date: str, db: str = MASTER_DB) -> dict:
    """{bot: {n, win, loss, stops, krw, best, worst}} — 그날 매도분(분할매도 포함)."""
    out = {b: {"n": 0, "win": 0, "loss": 0, "stops": 0, "krw": 0.0, "best": None, "worst": None}
           for b, _ in BOTS}
    for bot, name, code, krw, rate, reason in _q(
            db, "SELECT bot_type, stock_name, code, profit_krw, profit_rate, sell_reason FROM master_trades "
                "WHERE date(sell_time)=?", date):
        if bot not in out:
            continue
        o = out[bot]
        krw = krw or 0.0
        o["n"] += 1
        o["krw"] += krw
        o["win" if krw > 0 else "loss"] += 1
        if any(w in (reason or "") for w in STOP_WORDS):
            o["stops"] += 1
        label = (name or code, rate or 0.0)
        if o["best"] is None or (rate or 0) > o["best"][1]:
            o["best"] = label
        if o["worst"] is None or (rate or 0) < o["worst"][1]:
            o["worst"] = label
    return out


def bot_month(date: str, db: str = MASTER_DB) -> dict:
    first = date[:8] + "01"
    rows = _q(db, "SELECT bot_type, SUM(profit_krw), COUNT(*) FROM master_trades "
                  "WHERE date(sell_time) BETWEEN ? AND ? GROUP BY bot_type", first, date)
    return {b: (s or 0.0, n) for b, s, n in rows}


def open_positions(db: str = MASTER_DB) -> dict:
    out = {}
    for (bot,) in _q(db, "SELECT bot_type FROM master_positions"):
        out[bot] = out.get(bot, 0) + 1
    return out


def dip_status(date: str) -> dict:
    """cbot 눌림목 주머니 — 보유(상태파일), 오늘 청산(dip_trades)."""
    held = []
    sp = _find("cbot_state.json")
    if sp:
        try:
            with open(sp, encoding="utf-8") as f:
                dip = (json.load(f) or {}).get("dip") or {}
            held = [m.replace("KRW-", "") for m, st in dip.items() if isinstance(st, dict) and st.get("held")]
        except (OSError, ValueError):
            pass
    closed = _q(_find("cbot_trade_history.db"),
                "SELECT market, profit_rate, profit_krw, sell_reason FROM dip_trades WHERE date(sell_time)=?", date)
    return {"held": held, "closed": closed}


def cbot_paused() -> str:
    sp = _find("cbot_state.json")
    if not sp:
        return ""
    try:
        with open(sp, encoding="utf-8") as f:
            st = json.load(f) or {}
    except (OSError, ValueError):
        return ""
    if not st.get("paused"):
        return ""
    return "일손실 한도로 멈춤(4시간 점검 중)" if st.get("pause_reason") == "loss_limit" else "수동 일시중단 중"


def _won(x: float) -> str:
    return f"{x:+,.0f}원"


def _man(x: float) -> str:
    return f"{x / 10000:+.1f}만"


def build(date: str = None, db: str = MASTER_DB) -> str:
    date = date or datetime.date.today().isoformat()
    dt = datetime.date.fromisoformat(date)
    wd = "월화수목금토일"[dt.weekday()]
    day, month, pos = bot_day(date, db), bot_month(date, db), open_positions(db)
    L = [f"🌙 **봇 성적표 — {dt:%m-%d}({wd})**"]
    try:
        import market_regime as mr
        cur = mr.latest()
        if cur:
            L.append(f"📏 국면 {cur[1]:.0f}% ({cur[0][5:]} 종가 기준) → "
                     + ("내일 정상 매수" if mr.is_strong(cur[1]) else "내일 데이봇·sbot 매수금 절반"))
    except Exception:
        pass
    total = 0.0
    for b, label in BOTS:
        d = day[b]
        total += d["krw"]
        mkrw, mn = month.get(b, (0.0, 0))
        line = (f"■ {label:<4} 매도 {d['n']}건 {d['win']}승{d['loss']}패 · 손절 {d['stops']} · "
                f"실현 {_won(d['krw'])} · 보유 {pos.get(b, 0)} · 이달 {_man(mkrw)}({mn}건)")
        L.append(line)
        if d["n"]:
            L.append(f"      최고 {d['best'][0]} {d['best'][1]:+.1f}% · 최저 {d['worst'][0]} {d['worst'][1]:+.1f}%")
    dip = dip_status(date)
    dip_krw = sum(k or 0 for _, _, k, _ in dip["closed"])
    L.append(f"■ 눌림목 보유 {len(dip['held'])}" + (f"({'·'.join(dip['held'])})" if dip["held"] else "")
             + (f" · 오늘 청산 {len(dip['closed'])}건 {_won(dip_krw)}" if dip["closed"] else ""))
    L.append(f"■ 합계 오늘 실현 {_won(total + dip_krw)} · 이달 {_man(sum(v[0] for v in month.values()))}")

    lim = " · ".join(f"{label} {_man(day[b]['krw'])}/{_man(LIMITS[b])}" for b, label in BOTS)
    L.append(f"🛡️ 일손실 한도 대비 {lim}")
    warn = []
    for b, label in BOTS:
        if day[b]["krw"] <= LIMITS[b]:
            warn.append(f"{label} 일손실 한도 도달(오늘 신규매수 멈춤)")
    cp = cbot_paused()
    if cp:
        warn.append(f"cbot {cp}")
    try:
        import master_db
        rs = master_db.get_risk_state(db)
        if master_db.is_paused_all(db):
            warn.append("세 봇 합산 한도 → 전봇 매수 중단 중")
        elif rs.get("risk_level") == "warning" and rs.get("date") == date:
            warn.append(f"합산 손실 경고({rs.get('total_loss_krw', 0):,.0f}원)")
    except Exception:
        pass
    L.append("⚠️ " + " / ".join(warn) if warn else "✅ 이상 없음")
    return "\n".join(L)


if __name__ == "__main__":
    print(build(sys.argv[1] if len(sys.argv) > 1 else None))
