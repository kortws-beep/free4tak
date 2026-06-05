"""
db_manager.py — 매매이력 / AI캐시 DB 관리
"""
import sqlite3
import datetime

AI_CACHE_DB   = "ai_cache.db"
TRADE_HIST_DB = "trade_history.db"
AI_CACHE_DAYS = 7


class DBManager:

    # ============================================================
    # AI 캐시 DB
    # ============================================================
    def init_ai_db(self):
        try:
            conn = sqlite3.connect(AI_CACHE_DB, timeout=15)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ai_analysis (
                    code TEXT PRIMARY KEY, score INTEGER NOT NULL,
                    reason TEXT, analyzed_at TEXT NOT NULL
                )
            """)
            conn.commit(); conn.close()
            print(f"✅ AI DB 초기화 완료 ({AI_CACHE_DB})")
        except Exception as e:
            print(f"❌ AI DB 초기화 오류: {e}")

    def get_ai_cache(self, code):
        try:
            conn   = sqlite3.connect(AI_CACHE_DB, timeout=15)
            cursor = conn.execute(
                "SELECT score, reason, analyzed_at FROM ai_analysis WHERE code = ?", (code,)
            )
            row = cursor.fetchone(); conn.close()
            if not row: return None
            score, reason, analyzed_at = row
            age = (datetime.datetime.now() - datetime.datetime.fromisoformat(analyzed_at)).days
            if age >= AI_CACHE_DAYS: return None
            return {"score": score, "reason": reason, "analyzed_at": analyzed_at}
        except Exception as e:
            print(f"⚠️ AI DB 조회 오류 {code}: {e}")
            return None

    def save_ai_cache(self, code, score, reason):
        try:
            now  = datetime.datetime.now().isoformat(timespec="seconds")
            conn = sqlite3.connect(AI_CACHE_DB, timeout=15)
            conn.execute("""
                INSERT INTO ai_analysis (code, score, reason, analyzed_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(code) DO UPDATE SET
                    score=excluded.score, reason=excluded.reason, analyzed_at=excluded.analyzed_at
            """, (code, score, reason, now))
            conn.commit(); conn.close()
        except Exception as e:
            print(f"⚠️ AI DB 저장 오류 {code}: {e}")

    def clean_ai_db(self):
        try:
            today = datetime.datetime.now().strftime("%Y-%m-%d")
            conn  = sqlite3.connect(AI_CACHE_DB, timeout=15)
            cur   = conn.execute("DELETE FROM ai_analysis WHERE analyzed_at < ?", (today,))
            deleted = cur.rowcount
            conn.commit(); conn.close()
            if deleted:
                print(f"🗑️ AI DB 전일 캐시 {deleted}개 삭제")
        except Exception as e:
            print(f"⚠️ AI DB 정리 오류: {e}")

    # ============================================================
    # 매매 이력 DB
    # ============================================================
    def init_trade_db(self):
        try:
            conn = sqlite3.connect(TRADE_HIST_DB, timeout=15)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    code           TEXT    NOT NULL,
                    stock_name     TEXT,
                    buy_price      REAL    NOT NULL,
                    buy_time       TEXT    NOT NULL,
                    sell_price     REAL,
                    sell_time      TEXT,
                    qty            INTEGER NOT NULL,
                    profit_rate    REAL,
                    sell_reason    TEXT,
                    ai_score       INTEGER,
                    ai_reason      TEXT,
                    change_rate    REAL,
                    volume_ratio   REAL,
                    rsi            REAL,
                    ma_aligned     INTEGER,
                    foreign_5d     REAL,
                    institution_5d REAL,
                    buy_tag        TEXT
                )
            """)
            try:
                conn.execute("ALTER TABLE trades ADD COLUMN buy_tag TEXT")
            except Exception:
                pass
            conn.execute("""
                CREATE TABLE IF NOT EXISTS market_daily (
                    date TEXT PRIMARY KEY, kospi_change REAL,
                    kosdaq_change REAL, created_at TEXT NOT NULL
                )
            """)
            conn.commit(); conn.close()
            print(f"✅ 매매이력 DB 초기화 완료 ({TRADE_HIST_DB})")
        except Exception as e:
            print(f"❌ 매매이력 DB 초기화 오류: {e}")

    def save_buy_history(self, code, buy_price, qty, ai_score,
                         ai_reason, indicators, stock_name="", buy_tag=""):
        try:
            now  = datetime.datetime.now().isoformat(timespec="seconds")
            conn = sqlite3.connect(TRADE_HIST_DB, timeout=15)
            conn.execute("""
                INSERT INTO trades
                    (code, stock_name, buy_price, buy_time, qty, ai_score, ai_reason,
                     change_rate, volume_ratio, rsi, ma_aligned,
                     foreign_5d, institution_5d, buy_tag)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                code, stock_name, buy_price, now, qty, ai_score, ai_reason,
                indicators.get("change_rate",    0),
                indicators.get("volume_ratio",   0),
                indicators.get("rsi",            50),
                1 if indicators.get("ma5", 0) > indicators.get("ma20", 0) > indicators.get("ma60", 0) else 0,
                indicators.get("foreign_5d",     0),
                indicators.get("institution_5d", 0),
                buy_tag,
            ))
            conn.commit(); conn.close()
        except Exception as e:
            print(f"⚠️ 매수이력 저장 오류 {code}: {e}")

    def save_sell_history(self, code, sell_price, sell_reason):
        try:
            now  = datetime.datetime.now().isoformat(timespec="seconds")
            conn = sqlite3.connect(TRADE_HIST_DB, timeout=15)
            row  = conn.execute("""
                SELECT id, buy_price FROM trades
                WHERE code=? AND sell_price IS NULL
                ORDER BY id DESC LIMIT 1
            """, (code,)).fetchone()
            if not row: conn.close(); return
            trade_id, buy_price = row
            profit_rate = (sell_price - buy_price) / buy_price * 100 if buy_price else 0
            conn.execute("""
                UPDATE trades SET sell_price=?, sell_time=?, profit_rate=?, sell_reason=?
                WHERE id=?
            """, (sell_price, now, round(profit_rate, 2), sell_reason, trade_id))
            conn.commit(); conn.close()
            emoji = "✅" if profit_rate >= 0 else "❌"
            print(f"   {emoji} 이력저장 {code} | {profit_rate:+.2f}% | {sell_reason}")
        except Exception as e:
            print(f"⚠️ 매도이력 저장 오류 {code}: {e}")

    def get_trade_history(self, code, limit=10):
        try:
            conn = sqlite3.connect(TRADE_HIST_DB, timeout=15)
            rows = conn.execute("""
                SELECT buy_time, sell_time, buy_price, sell_price,
                       profit_rate, sell_reason, ai_score, ai_reason
                FROM trades WHERE code=? AND sell_price IS NOT NULL
                ORDER BY id DESC LIMIT ?
            """, (code, limit)).fetchall()
            conn.close(); return rows
        except Exception as e:
            print(f"⚠️ 이력 조회 오류 {code}: {e}"); return []

    def get_recent_performance(self, limit=20):
        try:
            conn = sqlite3.connect(TRADE_HIST_DB, timeout=15)
            rows = conn.execute("""
                SELECT profit_rate, sell_reason, ai_score FROM trades
                WHERE sell_price IS NOT NULL ORDER BY id DESC LIMIT ?
            """, (limit,)).fetchall()
            conn.close()
            if not rows: return None
            profits = [r[0] for r in rows]
            wins    = [p for p in profits if p >= 0]
            return {
                "total":      len(profits),
                "win_rate":   round(len(wins)/len(profits)*100, 1),
                "avg_profit": round(sum(profits)/len(profits), 2),
                "best":       round(max(profits), 2),
                "worst":      round(min(profits), 2),
            }
        except Exception as e:
            print(f"⚠️ 성과 조회 오류: {e}"); return None

    def get_today_realized(self, today: str = None) -> int:
        """오늘 실현손익 합산 (매도 완료 건만)"""
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
