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
# ★ 2026-10-06: 아래 write_state/update_state가 이 두 이름을 import 없이 써서
#   호출되면 NameError였음
from common_utils import write_state as _write_state_atomic
from common_utils import update_state as _update_state_atomic

# DB 경로 상수
_base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SBOT_HIST_DB   = os.path.join(_base, "sbot_trade_history.db")
SBO2_HIST_DB   = os.path.join(_base, "lina_bot", "sbo2_trades.db")
CBOT_HIST_DB   = os.path.join(_base, "cbot_trade_history.db")
DAYBOT_HIST_DB = os.path.join(_base, "daybot_trade_history.db")

BOT_STATE_FILES = {
    "sbot": "sbot_state.json",
    "sbo2": os.path.join("lina_bot", "sbo2_state.json"),
    "cbot": "cbot_state.json",
    "daybot": "daybot_state.json",
}

def read_state(bot: str = "sbot") -> dict:
    """봇 상태 파일 읽기 (없으면 기본값)"""
    fname = BOT_STATE_FILES.get(bot)
    if not fname:
        return {}
    return _read_state_atomic(fname, default={
        "paused":      False,
        "score_enter": 70,
        "pending_cmd": None,
        "cmd_result":  None,
        "last_status": None,
    })
def write_state(bot: str = "sbot", state: dict = None):
    """봇 상태 파일 쓰기 (★ atomic — 중간에 죽어도 안 깨짐)"""
    if state is None: state = {}
    fname = BOT_STATE_FILES.get(bot)
    if not fname:
        return
    _write_state_atomic(fname, state)
def update_state(bot: str = "sbot", **kwargs):
    """봇 상태 부분 업데이트"""
    fname = BOT_STATE_FILES.get(bot)
    if not fname:
        return
    _update_state_atomic(fname, **kwargs)
def get_active_bots() -> list:
    """현재 실행 중인(상태파일이 있는) 봇 목록.
    ★ 2026-10-01: sbo2는 은퇴했지만 sbo2_state.json 파일 자체는 남아있어
    "상태파일 존재=활성"이라는 이 함수의 원래 기준으로는 영원히 "활성"
    으로 잡힘 — !전체상태/!전체재시작 직후 자동상태출력에서 sbo2의
    DB 잔존기록(34건 전부 미청산 상태로 남아있음) 전체가 매번 끼어나와
    화면이 정신없어지는 문제 발견(대장 지적). 개별 !sbo2상태/!sbo2매도
    명령은 전환기간 수동조작용으로 여전히 살려둘 거라 BOT_STATE_FILES
    자체에서 빼진 않고, 여기서만 명시적으로 제외."""
    active = []
    for name, fname in BOT_STATE_FILES.items():
        if name == "sbo2":
            continue
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
    db = db or SBOT_HIST_DB
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

def get_open_positions_from_db(bot: str = "sbot") -> list:
    """DB의 미청산 매수 건.
    ★ 2026-09-29: daybot 추가 — daybot_trade_history.db는 sbot_db.py와
    같은 "trades" 테이블 구조를 쓰지만 ai_score 컬럼이 없어(고정%/호가
    게이트만 쓰는 단순전략이라 AI스코어링 자체가 없음), 그 자리에
    buy_tag(진입 tier: tier1_overlap 등)를 대신 넣는다."""
    if bot == "sbo2":
        db, table, score_col = SBO2_HIST_DB, "sbo2_trades", "score"
    elif bot == "daybot":
        db, table, score_col = DAYBOT_HIST_DB, "trades", "buy_tag"
    else:
        db, table, score_col = SBOT_HIST_DB, "trades", "ai_score"
    try:
        conn = _ro_connect(db)
        rows = conn.execute(f"""
            SELECT code, buy_price, qty, {score_col}, buy_time
            FROM {table} WHERE sell_price IS NULL
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
    """오늘 실현손익 — 봇별 합산.
    ★ 2026-10-06: daybot이 빠져있어 !성과 합계/키키 컨텍스트에 단타 손익이
    전혀 안 잡혔음 — 추가(daybot DB도 trades/buy_price/sell_price/qty/sell_time)."""
    import sqlite3, datetime
    today  = datetime.date.today().strftime("%Y-%m-%d")
    result = {"sbot": 0, "sbo2": 0, "cbot": 0, "daybot": 0}
    dbs    = {
        "sbot": (os.path.join(_base, "sbot_trade_history.db"), "trades",
                 "buy_price", "sell_price", "qty", "sell_time"),
        "sbo2": (os.path.join(_base, "lina_bot", "sbo2_trades.db"), "sbo2_trades",
                 "buy_price", "sell_price", "qty", "sell_time"),
        "cbot": (os.path.join(_base, "cbot_trade_history.db"), "trades",
                 "buy_price", "sell_price", "qty", "sell_time"),
        "daybot": (DAYBOT_HIST_DB, "trades",
                   "buy_price", "sell_price", "qty", "sell_time"),
    }
    for bot, (db_path, table, buy_col, sell_col, qty_col, time_col) in dbs.items():
        if not os.path.exists(db_path):
            continue
        try:
            conn = sqlite3.connect(db_path, timeout=5)
            conn.execute("PRAGMA query_only = ON")
            rows = conn.execute(f"""
                SELECT {buy_col}, {sell_col}, {qty_col} FROM {table}
                WHERE {sell_col} IS NOT NULL
                  AND {sell_col} > 0
                  AND date({time_col}) = ?
            """, (today,)).fetchall()
            conn.close()
            result[bot] = int(sum((r[1]-r[0])*r[2] for r in rows))
        except Exception:
            pass
    return result
