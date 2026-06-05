"""
sbot_db.py — 스윙봇 매매이력 DB
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

스윙봇이 매수/매도한 기록을 별도 DB에 저장합니다.
단타봇과 DB를 분리해 충돌을 막고, 스윙 특유의 통계를 따로 집계합니다.

[주요 개선 사항]
1. WAL 모드 — 동시 접근 안정성
2. 인덱스 — code, sell_time 조회 속도 향상
3. 부분 매도 지원 — 분할 익절 시 새 행 추가, 잔량 갱신
================================================================
"""
import sqlite3
import datetime
from typing import Optional


SBOT_HIST_DB = "sbot_trade_history.db"


def _connect() -> sqlite3.Connection:
    """WAL 모드 연결"""
    conn = sqlite3.connect(SBOT_HIST_DB, timeout=15)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


class SwingDB:
    """스윙봇 매매이력 DB."""

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
                    ai_score    INTEGER,
                    ai_reason   TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sbot_code ON trades(code, sell_time)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sbot_sell ON trades(sell_time)")
            conn.commit(); conn.close()
            print(f"✅ 스윙 매매이력 DB ({SBOT_HIST_DB})")
        except Exception as e:
            print(f"❌ 스윙 DB 오류: {e}")

    def save_buy(self, code: str, buy_price: float, qty: int,
                 ai_score: int, ai_reason: str, stock_name: str = ""):
        try:
            now  = datetime.datetime.now().isoformat(timespec="seconds")
            conn = _connect()
            conn.execute("""
                INSERT INTO trades
                    (code, stock_name, buy_price, buy_time, qty, ai_score, ai_reason)
                VALUES (?,?,?,?,?,?,?)
            """, (code, stock_name, buy_price, now, qty, ai_score, ai_reason))
            conn.commit(); conn.close()
        except Exception as e:
            print(f"⚠️ 스윙 매수 저장 오류 {code}: {e}")

    def save_sell(self, code: str, sell_price: float, sell_reason: str,
                  sold_qty: int = 0):
        """전량/부분 매도 자동 처리"""
        try:
            now  = datetime.datetime.now().isoformat(timespec="seconds")
            conn = _connect()
            row  = conn.execute("""
                SELECT id, buy_price, qty FROM trades
                WHERE code=? AND sell_price IS NULL
                ORDER BY id DESC LIMIT 1
            """, (code,)).fetchone()
            if not row:
                conn.close(); return

            trade_id, buy_price, total_qty = row
            profit_rate = ((sell_price - buy_price) / buy_price * 100
                          if buy_price else 0)

            if sold_qty == 0 or sold_qty >= total_qty:
                conn.execute("""
                    UPDATE trades
                    SET sell_price=?, sell_time=?, profit_rate=?, sell_reason=?
                    WHERE id=?
                """, (sell_price, now, round(profit_rate, 2), sell_reason, trade_id))
            else:
                # 분할 매도: 매도된 수량만 새 행 추가
                conn.execute("""
                    INSERT INTO trades
                        (code, buy_price, buy_time, qty, sell_price, sell_time,
                         profit_rate, sell_reason, stock_name, ai_score, ai_reason)
                    SELECT code, buy_price, buy_time, ?, ?, ?, ?, ?, stock_name, ai_score, ai_reason
                    FROM trades WHERE id=?
                """, (sold_qty, sell_price, now, round(profit_rate, 2),
                      sell_reason, trade_id))
                conn.execute("UPDATE trades SET qty=? WHERE id=?",
                           (total_qty - sold_qty, trade_id))

            conn.commit(); conn.close()
            emoji = "✅" if profit_rate >= 0 else "❌"
            print(f"   {emoji} 스윙이력 {code} | {profit_rate:+.2f}% | {sell_reason}")
        except Exception as e:
            print(f"⚠️ 스윙 매도 저장 오류 {code}: {e}")

    def get_recent_performance(self, limit: int = 20) -> Optional[dict]:
        try:
            conn = _connect()
            rows = conn.execute("""
                SELECT profit_rate FROM trades
                WHERE sell_price IS NOT NULL
                ORDER BY id DESC LIMIT ?
            """, (limit,)).fetchall()
            conn.close()
            if not rows:
                return None
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
