"""
fix_manual_master.py — 수동매도가 master_trades에 빠진 것 채우기 (2026-10-13)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

10-13 성적표 daybot 실현이 -5.3만으로 나왔는데 대장 기억은 "가온전선 팔기 전에 -6만대".
찾아보니 세 봇 모두 대장이 HTS/앱으로 직접 판 것(수동매도·수동일부매도)은 각 봇 DB에만
적고 master_trades엔 안 넣고 있었다 → 저녁 성적표·세 봇 합산 손실 자동중단에서 빠짐.
코드는 고쳤고, 이 스크립트는 지난 기록을 봇 DB에서 찾아 master_trades에 넣는다.
  · 대상: 봇 DB에서 매도사유에 '수동'이 든 매도 완료 행
  · master_trades에 같은 봇·같은 종목·매도시각 2분 이내 기록이 이미 있으면 건너뜀(중복 방지)
실행:  python intelligence/fix_manual_master.py           (무엇을 넣을지 보여주기만)
       python intelligence/fix_manual_master.py --apply   (실제로 넣음)
"""
import os
import sys
import sqlite3
import datetime

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MASTER_DB = os.path.join(_BASE, "master_trades.db")
WINDOW_SEC = 120
# 봇: (DB 파일, 종목 컬럼, 이름 컬럼 또는 None)
BOT_DBS = {"daybot": ("daybot_trade_history.db", "code", "stock_name"),
           "sbot": ("sbot_trade_history.db", "code", "stock_name"),
           "cbot": ("cbot_trade_history.db", "market", None)}


def _find(name: str) -> str:
    for d in (_BASE, os.path.join(_BASE, "bots")):
        p = os.path.join(d, name)
        if os.path.exists(p):
            return p
    return ""


def _ts(s: str):
    try:
        return datetime.datetime.fromisoformat((s or "").replace(" ", "T")[:19])
    except ValueError:
        return None


def plan(master_db: str, bot_dbs: dict) -> list:
    """[dict(bot, code, name, buy_price, buy_time, sell_price, sell_time, qty, reason, krw)] — 넣을 행."""
    m = sqlite3.connect(master_db)
    try:
        have = {}
        for bot, code, st in m.execute("SELECT bot_type, code, sell_time FROM master_trades"):
            t = _ts(st)
            if t:
                have.setdefault((bot, code), []).append(t)
    finally:
        m.close()
    out = []
    for bot, (path, ccol, ncol) in bot_dbs.items():
        if not path or not os.path.exists(path):
            continue
        c = sqlite3.connect(path)
        try:
            rows = c.execute(f"SELECT {ccol}, {ncol or ccol}, buy_price, buy_time, sell_price, sell_time, qty, "
                             f"sell_reason FROM trades WHERE sell_price IS NOT NULL AND sell_reason LIKE '%수동%' "
                             f"ORDER BY sell_time").fetchall()
        except sqlite3.OperationalError:
            rows = []
        finally:
            c.close()
        for code, name, bp, bt, sp, st, q, reason in rows:
            t = _ts(st)
            if not t or not q or not bp or not sp:
                continue
            if any(abs((x - t).total_seconds()) <= WINDOW_SEC for x in have.get((bot, code), [])):
                continue
            out.append({"bot": bot, "code": code, "name": name or code, "buy_price": bp, "buy_time": bt or "",
                        "sell_price": sp, "sell_time": t.strftime("%Y-%m-%d %H:%M:%S"), "qty": q,
                        "reason": reason, "krw": round((sp - bp) * q, 0)})
    return out


def apply(master_db: str, rows: list) -> None:
    conn = sqlite3.connect(master_db, timeout=10)
    try:
        conn.executemany(
            "INSERT INTO master_trades (bot_type, code, stock_name, buy_price, buy_time, sell_price, sell_time, "
            "qty, profit_rate, profit_krw, sell_reason, is_partial) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [(r["bot"], r["code"], r["name"], r["buy_price"], r["buy_time"], r["sell_price"], r["sell_time"],
              r["qty"], round((r["sell_price"] / r["buy_price"] - 1) * 100, 2), r["krw"], r["reason"],
              1 if "일부" in r["reason"] else 0) for r in rows])
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    if not os.path.exists(MASTER_DB):
        sys.exit("master_trades.db를 못 찾음")
    rows = plan(MASTER_DB, {b: (_find(f), cc, nc) for b, (f, cc, nc) in BOT_DBS.items()})
    month = datetime.date.today().strftime("%Y-%m")
    print(f"master에 빠진 수동매도 {len(rows)}건")
    for bot in BOT_DBS:
        rs = [r for r in rows if r["bot"] == bot]
        if rs:
            mk = sum(r["krw"] for r in rs if r["sell_time"].startswith(month))
            print(f"■ {bot} {len(rs)}건 · 합계 {sum(r['krw'] for r in rs):+,.0f}원 · 이번 달 {mk:+,.0f}원")
            for r in rs[-8:]:
                print(f"   {r['sell_time'][:16]} {r['name']:<12} {r['qty']:g}주 {r['krw']:+,.0f}원 · {r['reason']}")
    if "--apply" in sys.argv:
        apply(MASTER_DB, rows)
        print("✅ 넣었어 — 성적표·합산 한도에 바로 반영")
    elif rows:
        print("→ 맞으면 --apply 붙여서 다시 실행")
