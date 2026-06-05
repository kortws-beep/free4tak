"""
sbot_db.py — 스윙봇 매매이력 DB
"""
import sqlite3
import datetime

TRADE_HIST_DB = "sbot_trade_history.db"


class SwingDB:

    def init_db(self):
        try:
            conn = sqlite3.connect(TRADE_HIST_DB, timeout=15)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT NOT NULL, stock_name TEXT,
                    buy_price REAL NOT NULL, buy_time TEXT NOT NULL,
                    sell_price REAL, sell_time TEXT,
                    qty INTEGER NOT NULL, profit_rate REAL,
                    sell_reason TEXT, ai_score INTEGER, ai_reason TEXT,
                    hold_days INTEGER
                )
            """)
            conn.commit(); conn.close()
            print(f"✅ 스윙 매매이력 DB ({TRADE_HIST_DB})")
        except Exception as e:
            print(f"❌ 스윙 DB 오류: {e}")

    def save_buy(self, code, buy_price, qty, ai_score, ai_reason, stock_name=""):
        try:
            now  = datetime.datetime.now().isoformat(timespec="seconds")
            conn = sqlite3.connect(TRADE_HIST_DB, timeout=15)
            conn.execute("""
                INSERT INTO trades (code, stock_name, buy_price, buy_time, qty, ai_score, ai_reason)
                VALUES (?,?,?,?,?,?,?)
            """, (code, stock_name, buy_price, now, qty, ai_score, ai_reason))
            conn.commit(); conn.close()
        except Exception as e:
            print(f"⚠️ 매수이력 저장 오류: {e}")

    def save_sell(self, code, sell_price, sell_reason):
        try:
            now  = datetime.datetime.now().isoformat(timespec="seconds")
            conn = sqlite3.connect(TRADE_HIST_DB, timeout=15)
            row  = conn.execute("""
                SELECT id, buy_price, buy_time FROM trades
                WHERE code=? AND sell_price IS NULL ORDER BY id DESC LIMIT 1
            """, (code,)).fetchone()
            if not row: conn.close(); return
            trade_id, buy_price, buy_time = row
            profit_rate = (sell_price - buy_price) / buy_price * 100 if buy_price else 0
            hold_days   = (datetime.datetime.now() - datetime.datetime.fromisoformat(buy_time)).days
            conn.execute("""
                UPDATE trades SET sell_price=?, sell_time=?, profit_rate=?, sell_reason=?, hold_days=?
                WHERE id=?
            """, (sell_price, now, round(profit_rate, 2), sell_reason, hold_days, trade_id))
            conn.commit(); conn.close()
            emoji = "✅" if profit_rate >= 0 else "❌"
            print(f"   {emoji} {code} | {profit_rate:+.2f}% | 보유{hold_days}일 | {sell_reason}")
        except Exception as e:
            print(f"⚠️ 매도이력 저장 오류: {e}")

    def get_today_realized(self, today: str = None) -> int:
        if not today:
            today = datetime.datetime.now().strftime("%Y-%m-%d")
        try:
            conn = sqlite3.connect(TRADE_HIST_DB, timeout=15)
            rows = conn.execute("""
                SELECT buy_price, sell_price, qty FROM trades
                WHERE sell_price IS NOT NULL AND sell_time >= ?
            """, (today,)).fetchall()
            conn.close()
            return sum(int((sp - bp) * qty) for bp, sp, qty in rows)
        except Exception:
            return 0
