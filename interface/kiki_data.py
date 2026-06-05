"""
kiki_data.py — KiKi 데이터 조회 모듈
================================================================
봇 상태/DB 조회 공통 함수들
모든 kiki 모듈에서 import해서 사용
"""
import os
import sys
import sqlite3
import datetime
import requests

_here = os.path.dirname(os.path.abspath(__file__))
_base = os.path.dirname(_here)
for _d in ["core", "intelligence", "interface", "bots", ""]:
    _p = os.path.join(_base, _d)
    if _p not in sys.path:
        sys.path.insert(0, _p)

for _ep in [os.path.join(_here, ".env"), os.path.join(_base, ".env")]:
    if os.path.exists(_ep):
        from dotenv import load_dotenv
        load_dotenv(_ep, override=True)
        break

from common_utils import now_kst, today_str, now_hms, fmt_won, safe_float, safe_int, read_state, write_state, update_state
from common_utils import read_state as _read_state_atomic

# DB 경로 상수
_base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRADE_HIST_DB = os.path.join(_base, "trade_history.db")
SBOT_HIST_DB  = os.path.join(_base, "sbot_trade_history.db")
CBOT_HIST_DB  = os.path.join(_base, "cbot_trade_history.db")

BOT_STATE_FILES = {
    "nbot": "bot_state.json",
    "sbot": "sbot_state.json",
    "cbot": "cbot_state.json",
}

def read_state(bot: str = "nbot") -> dict:
    """봇 상태 파일 읽기 (없으면 기본값)"""
    fname = BOT_STATE_FILES.get(bot, "bot_state.json")
    return _read_state_atomic(fname, default={
        "paused":      False,
        "score_enter": 55,
        "pending_cmd": None,
        "cmd_result":  None,
        "last_status": None,
    })

def write_state(bot: str = "nbot", state: dict = None):
    """봇 상태 파일 쓰기 (★ atomic — 중간에 죽어도 안 깨짐)"""
    if state is None: state = {}
    fname = BOT_STATE_FILES.get(bot, "bot_state.json")
    _write_state_atomic(fname, state)

def update_state(bot: str = "nbot", **kwargs):
    """봇 상태 부분 업데이트"""
    fname = BOT_STATE_FILES.get(bot, "bot_state.json")
    _update_state_atomic(fname, **kwargs)

def get_active_bots() -> list:
    """현재 실행 중인(상태파일이 있는) 봇 목록"""
    active = []
    for name, fname in BOT_STATE_FILES.items():
        if os.path.exists(fname):
            state = read_state(name)
            last  = state.get("last_update", "")
            active.append((name, last))
    return active


# ============================================================
# DB 조회 헬퍼 (★ WAL 호환 — read-only 모드)
# ============================================================
def _ro_connect(db_file: str) -> sqlite3.Connection:
    """읽기 전용 SQLite 연결 (WAL 모드 봇이 쓰는 동안 안전하게 읽기)"""
    conn = sqlite3.connect(db_file, timeout=10)
    conn.execute("PRAGMA query_only = ON")
    return conn

def get_recent_performance(limit: int = 20, db: str = None) -> list:
    """최근 매매 성과 (단타/스윙)"""
    db = db or TRADE_HIST_DB
    try:
        conn = _ro_connect(db)
        rows = conn.execute("""
            SELECT profit_rate, sell_reason, ai_score, code,
                   buy_price, sell_price, buy_time, sell_time
            FROM trades WHERE sell_price IS NOT NULL
            ORDER BY id DESC LIMIT ?
        """, (limit,)).fetchall()
        conn.close()
        return rows
    except Exception:
        return []

def get_open_positions_from_db(bot: str = "nbot") -> list:
    """DB의 미청산 매수 건"""
    db = TRADE_HIST_DB if bot == "nbot" else SBOT_HIST_DB
    try:
        conn = _ro_connect(db)
        rows = conn.execute("""
            SELECT code, buy_price, qty, ai_score, buy_time
            FROM trades WHERE sell_price IS NULL
            ORDER BY buy_time DESC
        """).fetchall()
        conn.close()
        return rows
    except Exception:
        return []

def get_coin_performance(limit: int = 20) -> list:
    """코인봇 매매 성과"""
    try:
        conn = _ro_connect(CBOT_HIST_DB)
        rows = conn.execute("""
            SELECT profit_rate, sell_reason, ai_score, market,
                   buy_price, sell_price, buy_time, sell_time
            FROM trades WHERE sell_price IS NOT NULL
            ORDER BY id DESC LIMIT ?
        """, (limit,)).fetchall()
        conn.close()
        return rows
    except Exception:
        return []


def get_today_realized_all() -> dict:
    """오늘 실현손익 — 봇별 합산"""
    import sqlite3, datetime
    today  = datetime.date.today().strftime("%Y-%m-%d")
    result = {"nbot": 0, "sbot": 0, "cbot": 0}
    dbs    = {
        "nbot": os.path.join(_base, "trade_history.db"),
        "sbot": os.path.join(_base, "sbot_trade_history.db"),
        "cbot": os.path.join(_base, "cbot_trade_history.db"),
    }
    for bot, db_path in dbs.items():
        if not os.path.exists(db_path):
            continue
        try:
            conn = sqlite3.connect(db_path, timeout=5)
            conn.execute("PRAGMA query_only = ON")
            rows = conn.execute("""
                SELECT buy_price, sell_price, qty FROM trades
                WHERE sell_price IS NOT NULL
                  AND sell_price > 0
                  AND date(sell_time) = ?
            """, (today,)).fetchall()
            conn.close()
            result[bot] = sum((r[1]-r[0])*r[2] for r in rows)
        except Exception:
            pass
    return result
