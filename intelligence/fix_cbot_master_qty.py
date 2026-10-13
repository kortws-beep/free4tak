"""
fix_cbot_master_qty.py — cbot 전량매도가 master에 0주·0원으로 기록된 것 복구 (2026-10-13)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

10-13 성적표에 cbot "손절 5건인데 실현 +0원"이 나와서 찾은 버그: cbot은 전량매도를
sold_qty=0("전량" 표시)으로 처리하는데, master_trades에도 그 0을 수량으로 넘겨서
손익이 0원으로 들어갔다(cbot 자체 DB·일손실 한도는 정상). 그래서 성적표와 세 봇 합산
손실 자동중단(80만)에서 cbot 손실이 빠져 있었다. 코드는 고쳤고, 이 스크립트는 지난 기록을
cbot_trade_history.db(실제 수량)와 맞춰 채운다 — 같은 코인·같은 매도가·매도시각 10초 이내.
실행:  python intelligence/fix_cbot_master_qty.py          (무엇을 고칠지 보여주기만)
       python intelligence/fix_cbot_master_qty.py --apply  (실제로 고침)
"""
import os
import sys
import sqlite3
import datetime

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MASTER_DB = os.path.join(_BASE, "master_trades.db")
WINDOW_SEC = 10


def _find_cbot_db() -> str:
    for d in (_BASE, os.path.join(_BASE, "bots")):
        p = os.path.join(d, "cbot_trade_history.db")
        if os.path.exists(p):
            return p
    return ""


def _ts(s: str):
    try:
        return datetime.datetime.fromisoformat((s or "").replace(" ", "T")[:19])
    except ValueError:
        return None


def plan(master_db: str, cbot_db: str) -> list:
    """[(master id, market, sell_time, qty, profit_krw)] — 채울 행."""
    m = sqlite3.connect(master_db)
    c = sqlite3.connect(cbot_db)
    try:
        bad = m.execute("SELECT id, code, buy_price, sell_price, sell_time FROM master_trades "
                        "WHERE bot_type='cbot' AND (qty IS NULL OR qty=0)").fetchall()
        hist = c.execute("SELECT market, buy_price, sell_price, sell_time, qty FROM trades "
                         "WHERE sell_price IS NOT NULL AND qty > 0").fetchall()
    finally:
        m.close(); c.close()
    used, out = set(), []
    for mid, code, bp, sp, st in bad:
        t = _ts(st)
        if not t:
            continue
        best = None
        for i, (mk, hbp, hsp, hst, q) in enumerate(hist):
            if i in used or mk != code or abs((hsp or 0) - (sp or 0)) > 1e-9:
                continue
            ht = _ts(hst)
            if ht and abs((ht - t).total_seconds()) <= WINDOW_SEC:
                best = i
                break
        if best is None:
            continue
        used.add(best)
        q = hist[best][4]
        buy = bp or hist[best][1]
        out.append((mid, code, st, q, round((sp - buy) * q, 0)))
    return out


def apply(master_db: str, rows: list) -> None:
    conn = sqlite3.connect(master_db, timeout=10)
    try:
        conn.executemany("UPDATE master_trades SET qty=?, profit_krw=? WHERE id=?",
                         [(q, krw, mid) for mid, _, _, q, krw in rows])
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    cdb = _find_cbot_db()
    if not cdb or not os.path.exists(MASTER_DB):
        sys.exit("master_trades.db 또는 cbot_trade_history.db를 못 찾음")
    rows = plan(MASTER_DB, cdb)
    total = sum(r[4] for r in rows)
    print(f"cbot 0주 기록 중 맞춰진 것 {len(rows)}건 · 복구될 손익 합계 {total:+,.0f}원")
    for _, code, st, q, krw in rows[-15:]:
        print(f"   {st[:16]} {code:<10} {q:.6g}개 {krw:+,.0f}원")
    if "--apply" in sys.argv:
        apply(MASTER_DB, rows)
        print("✅ 고쳤어 — 성적표·합산 한도에 바로 반영")
    elif rows:
        print("→ 맞으면 --apply 붙여서 다시 실행")
