"""
daybot_db.py — 단타봇 매매이력 DB
================================================================
core/sbot_db.py의 SwingDB 패턴을 그대로 따르되, 단타봇은 ATR/AI점수
같은 스윙 전용 필드가 없어 스키마를 그만큼 덜어냈다. 당일청산 원칙이라
hold_days는 항상 0에 가깝지만, 다른 봇들과 동일한 DB 관례를 유지하기
위해 컬럼은 남겨둔다.
================================================================
"""
import sqlite3
import datetime
from typing import Optional


DAYBOT_HIST_DB = "daybot_trade_history.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DAYBOT_HIST_DB, timeout=15)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


class DayTradeDB:
    """단타봇 매매이력 DB."""

    def init_db(self):
        try:
            conn = _connect()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    code        TEXT    NOT NULL,
                    stock_name  TEXT,
                    buy_price   REAL    NOT NULL,
                    buy_time    TEXT    NOT NULL,
                    sell_price  REAL,
                    sell_time   TEXT,
                    qty         INTEGER NOT NULL,
                    profit_rate REAL,
                    sell_reason TEXT,
                    buy_tag     TEXT    DEFAULT '',  -- 소스 tier: tier1_overlap/tier2_danta000/tier3_fallback
                    hold_days   INTEGER DEFAULT 0
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_daybot_code ON trades(code, sell_time)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_daybot_sell ON trades(sell_time)")
            conn.commit(); conn.close()
            print(f"✅ 단타 매매이력 DB ({DAYBOT_HIST_DB})")
        except Exception as e:
            print(f"❌ 단타 DB 오류: {e}")

    def save_buy(self, code: str, buy_price: float, qty: int,
                 stock_name: str = "", buy_tag: str = ""):
        try:
            now  = datetime.datetime.now().isoformat(timespec="seconds")
            conn = _connect()
            conn.execute("""
                INSERT INTO trades
                    (code, stock_name, buy_price, buy_time, qty, buy_tag)
                VALUES (?,?,?,?,?,?)
            """, (code, stock_name, buy_price, now, qty, buy_tag))
            conn.commit(); conn.close()
        except Exception as e:
            print(f"⚠️ 단타 매수 저장 오류 {code}: {e}")

    def save_sell(self, code: str, sell_price: float, sell_reason: str,
                  sold_qty: int = 0):
        """전량/부분 매도 자동 처리 (부분매도는 v1에선 원칙적으로 없지만
        구조는 sbot_db.py와 동일하게 대비해둔다)."""
        try:
            now  = datetime.datetime.now().isoformat(timespec="seconds")
            conn = _connect()
            row  = conn.execute("""
                SELECT id, buy_price, qty, buy_time FROM trades
                WHERE code=? AND sell_price IS NULL
                ORDER BY id DESC LIMIT 1
            """, (code,)).fetchone()
            if not row:
                conn.close(); return

            trade_id, buy_price, total_qty, buy_time = row
            profit_rate = ((sell_price - buy_price) / buy_price * 100
                          if buy_price else 0)
            hold_days = 0
            try:
                bt = datetime.datetime.fromisoformat(buy_time)
                hold_days = (datetime.datetime.now().date() - bt.date()).days
            except Exception:
                pass

            if sold_qty == 0 or sold_qty >= total_qty:
                conn.execute("""
                    UPDATE trades
                    SET sell_price=?, sell_time=?, profit_rate=?, sell_reason=?, hold_days=?
                    WHERE id=?
                """, (sell_price, now, round(profit_rate, 2), sell_reason, hold_days, trade_id))
            else:
                conn.execute("""
                    INSERT INTO trades
                        (code, buy_price, buy_time, qty, sell_price, sell_time,
                         profit_rate, sell_reason, stock_name, buy_tag, hold_days)
                    SELECT code, buy_price, buy_time, ?, ?, ?, ?, ?, stock_name, buy_tag, ?
                    FROM trades WHERE id=?
                """, (sold_qty, sell_price, now, round(profit_rate, 2),
                      sell_reason, hold_days, trade_id))
                conn.execute("UPDATE trades SET qty=? WHERE id=?",
                           (total_qty - sold_qty, trade_id))

            conn.commit(); conn.close()
            emoji = "✅" if profit_rate >= 0 else "❌"
            print(f"   {emoji} 단타이력 {code} | {profit_rate:+.2f}% | {sell_reason}")
        except Exception as e:
            print(f"⚠️ 단타 매도 저장 오류 {code}: {e}")

    def get_today_realized(self, today: str = None) -> int:
        if not today:
            today = datetime.datetime.now().strftime("%Y-%m-%d")
        try:
            conn = _connect()
            rows = conn.execute("""
                SELECT buy_price, sell_price, qty FROM trades
                WHERE sell_price IS NOT NULL AND sell_time >= ?
            """, (today,)).fetchall()
            conn.close()
            return sum(int((sp - bp) * qty) for bp, sp, qty in rows
                       if sp is not None and bp is not None)
        except Exception:
            return 0

    def get_recent_performance(self, days: int = 7) -> Optional[dict]:
        try:
            conn = _connect()
            cutoff = (datetime.datetime.now()
                      - datetime.timedelta(days=days)).isoformat(timespec="seconds")
            rows = conn.execute("""
                SELECT profit_rate FROM trades
                WHERE sell_price IS NOT NULL AND sell_time >= ?
                ORDER BY id DESC
            """, (cutoff,)).fetchall()
            conn.close()
            profits = [r[0] for r in rows if r[0] is not None]
            if not profits:
                return None
            wins = [p for p in profits if p >= 0]
            return {
                "total":      len(profits),
                "win_rate":   round(len(wins) / len(profits) * 100, 1),
                "avg_profit": round(sum(profits) / len(profits), 2),
            }
        except Exception:
            return None
