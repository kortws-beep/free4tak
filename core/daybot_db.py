"""
daybot_db.py — 단타봇 매매이력 DB
================================================================
core/sbot_db.py의 SwingDB 패턴을 그대로 따르되, 단타봇은 ATR/AI점수
같은 스윙 전용 필드가 없어 스키마를 그만큼 덜어냈다. 당일청산 원칙이라
hold_days는 항상 0에 가깝지만, 다른 봇들과 동일한 DB 관례를 유지하기
위해 컬럼은 남겨둔다.
================================================================
"""
import os
import sqlite3
import json
import datetime
from typing import Optional


DAYBOT_HIST_DB = "daybot_trade_history.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DAYBOT_HIST_DB, timeout=15)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


# ★ 2026-10-07: 키움 조건검색 원본 결과 기록 — 파이썬판 검색식(리나)과 날마다
#   자동 대조(intelligence/scan_compare.py)하려고. 리나 검색식 기록과 같은 DB에
#   쓴다(경로는 이 파일 기준 절대경로 — daybot 작업폴더와 무관).
SCAN_LOG_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "lina_bot", "three_month_leader_log.db")


def log_kiwoom_hits(code_multi_tag_map: dict, code_name_map: dict = None,
                    db_path: str = None) -> None:
    """스캔 1회분 {code: [검색식명, ...]} 저장 + 스캔했다는 표시(__scan__) 1줄.
    결과가 0개인 스캔도 '그 시각엔 키움이 봤는데 없었다'를 알 수 있게.
    실패해도 조용히 넘어감(매매 로직에 영향 X)."""
    try:
        now = datetime.datetime.now()
        d, t = now.strftime("%Y-%m-%d"), now.strftime("%H:%M:%S")
        rows = [(d, t, "", "", "__scan__")]
        for code, tags in (code_multi_tag_map or {}).items():
            for tag in set(tags):
                rows.append((d, t, code, (code_name_map or {}).get(code, ""), tag))
        conn = sqlite3.connect(db_path or SCAN_LOG_DB, timeout=10)
        try:
            conn.execute("""CREATE TABLE IF NOT EXISTS kiwoom_cond_log (
                date TEXT, time TEXT, code TEXT, name TEXT, tag TEXT)""")
            conn.executemany("INSERT INTO kiwoom_cond_log VALUES (?,?,?,?,?)", rows)
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        print(f"⚠️ 키움 조건검색 기록 실패: {e}")


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

            # ★ 2026-10-03 대장 지정 — 키움은 종목코드+종목명만 주고(스크리닝
            #   전용), 실제 가격/거래량/호가 판단은 전부 한투(KIS)에서 가져옴.
            #   매수로 이어졌든 아니든 "한투에서 조회한 시점의 데이터"를
            #   전부 남겨두면 나중에 백테스터/다른 단타 전략의 진짜 소스가
            #   될 수 있다는 대장 아이디어 — 매 후보 검토마다 기록.
            #   raw_market_data/raw_hoga_data는 get_market_data()/get_hoga()
            #   원본을 JSON 그대로 보관(지금 안 쓰는 필드라도 나중에 필요해지면
            #   스키마 변경 없이 꺼내 쓸 수 있게).
            conn.execute("""
                CREATE TABLE IF NOT EXISTS candidate_log (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts             TEXT    NOT NULL,
                    code           TEXT    NOT NULL,
                    stock_name     TEXT,
                    source_tier    TEXT,
                    price          REAL,
                    change_rate    REAL,
                    ask_bid_ratio  REAL,
                    bought         INTEGER DEFAULT 0,
                    skip_reason    TEXT,
                    raw_market_data TEXT,
                    raw_hoga_data   TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cand_ts ON candidate_log(ts)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cand_code ON candidate_log(code, ts)")

            conn.commit(); conn.close()
            print(f"✅ 단타 매매이력 DB ({DAYBOT_HIST_DB})")
        except Exception as e:
            print(f"❌ 단타 DB 오류: {e}")

    def log_candidate(self, code: str, stock_name: str, source_tier: str,
                       price: float = 0.0, change_rate: float = 0.0,
                       ask_bid_ratio: Optional[float] = None,
                       bought: bool = False, skip_reason: str = "",
                       raw_market_data: Optional[dict] = None,
                       raw_hoga_data: Optional[dict] = None):
        try:
            now = datetime.datetime.now().isoformat(timespec="seconds")
            conn = _connect()
            conn.execute("""
                INSERT INTO candidate_log
                    (ts, code, stock_name, source_tier, price, change_rate,
                     ask_bid_ratio, bought, skip_reason, raw_market_data, raw_hoga_data)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (now, code, stock_name, source_tier, price, change_rate,
                  ask_bid_ratio, 1 if bought else 0, skip_reason,
                  json.dumps(raw_market_data, ensure_ascii=False) if raw_market_data else None,
                  json.dumps(raw_hoga_data, ensure_ascii=False) if raw_hoga_data else None))
            conn.commit(); conn.close()
        except Exception as e:
            print(f"⚠️ 단타 후보로그 저장 오류 {code}: {e}")

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

    def save_manual_trade(self, code: str, stock_name: str, buy_price: float,
                           sell_price: float, qty: int, sell_reason: str,
                           buy_tag: str = "수동"):
        """★ 2026-10-01: 대장이 HTS/MTS로 daybot 보유종목을 직접 매도한
        경우(sbot_db.py의 동일 메서드 그대로 이식) — save_buy/save_sell은
        봇이 만든 매수 행이 있어야 매도를 매칭하는데, 이 경로는 매수/매도를
        한 번에 완결 기록한다."""
        try:
            now = datetime.datetime.now().isoformat(timespec="seconds")
            profit_rate = ((sell_price - buy_price) / buy_price * 100
                           if buy_price else 0)
            conn = _connect()
            conn.execute("""
                INSERT INTO trades
                    (code, stock_name, buy_price, buy_time, qty,
                     sell_price, sell_time, profit_rate, sell_reason, buy_tag)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (code, stock_name, buy_price, now, qty,
                  sell_price, now, round(profit_rate, 2), sell_reason, buy_tag))
            conn.commit(); conn.close()
            emoji = "✅" if profit_rate >= 0 else "❌"
            print(f"   {emoji} 단타 수동거래 기록 {code} | {profit_rate:+.2f}% | {sell_reason}")
        except Exception as e:
            print(f"⚠️ 단타 수동거래 저장 오류 {code}: {e}")

    def void_buy(self, code: str):
        """★ 2026-09-30: 주문은 냈지만 체결 전에 취소된 매수를 DB에서
        완전히 지운다. save_buy()는 주문 시점에 낙관적으로 먼저 기록하는데,
        30초 내 미체결로 판명돼 취소되면(daybot.py:_check_pending_orders())
        실제로는 산 적이 없는 거래라 매도기록 없는 유령 row로 영구히 남는
        문제(0035S0/빅웨이브로보틱스 실사례로 발견) — 해당 row 자체를
        삭제해 실현손익 통계 왜곡을 막는다."""
        try:
            conn = _connect()
            conn.execute("""
                DELETE FROM trades WHERE id = (
                    SELECT id FROM trades WHERE code=? AND sell_price IS NULL
                    ORDER BY id DESC LIMIT 1
                )
            """, (code,))
            conn.commit(); conn.close()
        except Exception as e:
            print(f"⚠️ 단타 매수취소 정리 오류 {code}: {e}")

    def update_open_buy(self, code: str, qty: int, buy_price: float = None):
        """★ 2026-10-06: 미체결 취소 후 일부만 체결된 매수의 수량(및 실제
        평단)을 보정한다 — void_buy()로 통째로 지우면 실제로 산 부분체결분이
        장부에서 사라졌음(daybot.py:_reconcile_cancelled_buys() 참고)."""
        try:
            conn = _connect()
            if buy_price:
                conn.execute("""
                    UPDATE trades SET qty=?, buy_price=? WHERE id = (
                        SELECT id FROM trades WHERE code=? AND sell_price IS NULL
                        ORDER BY id DESC LIMIT 1
                    )
                """, (qty, buy_price, code))
            else:
                conn.execute("""
                    UPDATE trades SET qty=? WHERE id = (
                        SELECT id FROM trades WHERE code=? AND sell_price IS NULL
                        ORDER BY id DESC LIMIT 1
                    )
                """, (qty, code))
            conn.commit(); conn.close()
        except Exception as e:
            print(f"⚠️ 단타 매수수량 보정 오류 {code}: {e}")

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
