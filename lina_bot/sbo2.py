"""
sbo2.py — 리나 관리 스윙봇 (3단 콤보 연동 버전)
================================================================
[설계 원칙]
- 후보 소스  : swing_master.py S/A급 종목만
- 시드머니   : 500만원 / 1종목 기본 150만원 / 최대 4종목
- 매수금액   : 점수 비례 (150만 기준 ±조정)
- 매도 기준  : ATR 자동 (swing_analyzer 계산값)
- S급        : 무조건 매수
- A급        : 점수 상위 70%만 매수
- 봇 타입    : master_db 'sbo2' 구분
- 알림       : 리나 디스코드 채널

[모듈 구조]
  sbo2.py          ← 메인 루프 (이 파일)
  swing_master.py  ← S/A급 후보 추출
  swing_analyzer.py← ATR 손절/목표가
  kis_api.py       ← 한투 API (공유)
  master_db.py     ← 통합 이력 (공유)
  sbo2_db.py       ← sbo2 전용 DB
  notifier.py      ← 디스코드 알림
================================================================
"""

import os
import sys
import time
import pathlib

# ── Heartbeat 설정 ────────────────────────────────────
HB_FILE      = "/tmp/hb_sbo2"          # heartbeat 파일
API_FAIL_MAX = 3                         # API 연속 실패 허용 횟수
import json
import datetime
import sqlite3

from dotenv import load_dotenv

# ── 경로 설정 ─────────────────────────────────────────────────
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
STOCK_BOT  = os.path.dirname(BASE_DIR)   # ~/k-bot/stock_bot

# .env 우선순위: lina_bot/.env → stock_bot/.env
_env1 = os.path.join(BASE_DIR, '.env')
_env2 = os.path.join(STOCK_BOT, '.env')
if os.path.exists(_env1):
    load_dotenv(_env1)
elif os.path.exists(_env2):
    load_dotenv(_env2)
    print(f"✅ .env 로드: {_env2}")

# ── sys.path 설정 (core/kis_api.py 등 공유 모듈 사용) ──────────
_STOCK_BOT = os.path.dirname(os.path.abspath(__file__))
_STOCK_BOT = os.path.dirname(_STOCK_BOT)
for _d in ["core", "interface", "bots", ""]:
    _p = os.path.join(_STOCK_BOT, _d)
    if os.path.exists(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

# ── 의존 모듈 ─────────────────────────────────────────────────
from kis_api       import KisAPI
from swing_master        import get_master_report, _get_catalyst_stocks, _extract_names_from_report
from tele_swing_analyzer import get_tele_swing_picks
from swing_analyzer import get_swing_picks
from trend_analyzer import get_trend_picks

try:
    from master_db import (
        record_trade    as _master_record,
        upsert_position as _master_upsert,
        remove_position as _master_remove,
        get_all_positions,
    )
    print("✅ master_db 연결 완료")
except ImportError:
    _master_record = _master_upsert = _master_remove = None
    print("⚠️ master_db 없음 → 통합 이력 비활성")

try:
    import requests as _req
    DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL", "")
    def _notify(msg: str, critical: bool = False):
        if not DISCORD_WEBHOOK:
            print(f"[알림] {msg}")
            return
        prefix = "🚨 " if critical else "📢 "
        try:
            _req.post(DISCORD_WEBHOOK, json={"content": f"{prefix}{msg}"}, timeout=5)
        except Exception as e:
            print(f"⚠️ 디스코드 알림 오류: {e}")
except Exception:
    def _notify(msg, critical=False): print(f"[알림] {msg}")


# ============================================================
# 상수 (튜닝 포인트)
# ============================================================
SEED_MONEY       = 5_000_000   # 시드머니 500만원
BASE_BUY_AMT     = 1_500_000   # 1종목 기본 매수금액 150만원
MAX_POSITIONS    = 4            # 최대 보유 종목 (교집합1+스윙1+추세1+텔레1)
A_GRADE_RATIO    = 0.7          # A급 상위 70%만 매수

# 슬롯 전략 구분
SLOT_INTER  = "inter"   # 교집합 (VCP+추세+촉매)
SLOT_SWING  = "swing"   # 스윙 (VCP only)
SLOT_TREND  = "trend"   # 추세 (trend only)
SLOT_TELE   = "tele"    # 텔레스윙

SLOT_LABEL = {
    SLOT_INTER: "교집합",
    SLOT_SWING: "스윙",
    SLOT_TREND: "추세",
    SLOT_TELE:  "텔레",
    "실계좌":   "실계좌",
    "S":        "S급",
    "A":        "A급",
}
LOOP_SLEEP       = 30           # 루프 간격 (초)
BUY_START_TIME   = "0910"       # 매수 시작
BUY_END_TIME     = "1520"       # 매수 마감
SELL_START_TIME  = "0800"       # 프리장부터 매도 체크
SELL_END_TIME    = "2000"       # 애프터장까지 매도 체크
CANDIDATE_REFRESH= 86400        # 후보 갱신 주기 (하루 1회)

MIN_PRICE        = 3_000        # 최소 주가
MAX_PRICE        = 3_000_000    # 최대 주가
MIN_BUY_CHECK_CASH = 200_000     # 주문가능금액이 이 밑이면 후보 전수 조회(현재가+MA40) 자체를 건너뜀

BOT_STATE_FILE   = os.path.join(BASE_DIR, "sbo2_state.json")
SBO2_DB_PATH     = os.path.join(BASE_DIR, "sbo2_trades.db")


# ============================================================
# KST 시간 헬퍼
# ============================================================
KST = datetime.timezone(datetime.timedelta(hours=9))

def now_kst() -> datetime.datetime:
    return datetime.datetime.now(KST)

def now_hhmm() -> str:
    return now_kst().strftime("%H%M")

def now_hms() -> str:
    return now_kst().strftime("%H:%M:%S")

def today_str() -> str:
    return now_kst().strftime("%Y-%m-%d")

def now_full_ts() -> str:
    """★ 완전한 타임스탬프 (날짜+시각) — buy_time/sell_time 통일용
    (기존에는 buy_time이 시각만(now_hms) 또는 날짜만(today_str)으로
    저장처가 갈려서 hold_days 계산이 틀어지는 버그가 있었음, 2026-06-27 수정)"""
    return now_kst().strftime("%Y-%m-%d %H:%M:%S")

def is_weekend() -> bool:
    return now_kst().weekday() >= 5


# ============================================================
# sbo2 전용 DB
# ============================================================
def init_sbo2_db():
    """sbo2 전용 DB 초기화"""
    conn = sqlite3.connect(SBO2_DB_PATH, timeout=15)
    conn.execute("PRAGMA journal_mode=WAL")

    # ── 후보 이력 (매번 스캔 결과 저장) ─────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sbo2_candidates (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_date   TEXT    NOT NULL,
            scan_time   TEXT    NOT NULL,
            stock_name  TEXT    NOT NULL,
            grade       TEXT    NOT NULL,   -- inter/swing/trend/tele/실계좌
            score       INTEGER DEFAULT 0,
            vcp_hit     INTEGER DEFAULT 0,  -- VCP 해당 여부
            trend_hit   INTEGER DEFAULT 0,  -- 추세 해당 여부
            catalyst_hit INTEGER DEFAULT 0, -- 촉매 해당 여부
            curr_price  REAL    DEFAULT 0,
            stop_price  REAL    DEFAULT 0,
            tgt_price   REAL    DEFAULT 0,
            rr_ratio    REAL    DEFAULT 0,
            bought      INTEGER DEFAULT 0,  -- 실제 매수 여부
            skip_reason TEXT    DEFAULT ''
        )
    """)

    # ── 매매 이력 ─────────────────────────────────────────────
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sbo2_trades (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            code         TEXT    NOT NULL,
            stock_name   TEXT    DEFAULT '',
            grade        TEXT    DEFAULT '',   -- inter/swing/trend/tele/실계좌
            vcp_hit      INTEGER DEFAULT 0,
            trend_hit    INTEGER DEFAULT 0,
            catalyst_hit INTEGER DEFAULT 0,
            buy_price    REAL    NOT NULL,
            buy_time     TEXT    NOT NULL,
            buy_amount   REAL    DEFAULT 0,
            qty          INTEGER NOT NULL,
            score        INTEGER DEFAULT 0,
            stop_price   REAL    DEFAULT 0,
            tgt_price    REAL    DEFAULT 0,
            rr_ratio     REAL    DEFAULT 0,
            sell_price   REAL,
            sell_time    TEXT,
            sell_reason  TEXT,
            profit_rate  REAL,
            profit_krw   REAL,
            hold_days    INTEGER DEFAULT 0
        )
    """)

    conn.execute("CREATE INDEX IF NOT EXISTS idx_sbo2_code ON sbo2_trades(code, sell_time)")

    # ── ★ 사후검증용 컬럼 추가 (기존 DB에도 안전하게 적용, 2026-06-27) ──
    #   stage_reached    : 매도 시점까지 도달한 단계 (0=목표1 미달성, 1+=단계익절 진행)
    #   atr_val_at_entry : 진입 시점 ATR 절대값 (백테스트/검증 시 손절·목표 재현용)
    for col, coltype in [("stage_reached", "INTEGER DEFAULT 0"),
                          ("atr_val_at_entry", "REAL DEFAULT 0")]:
        try:
            conn.execute(f"ALTER TABLE sbo2_trades ADD COLUMN {col} {coltype}")
        except sqlite3.OperationalError:
            pass  # 이미 컬럼이 존재하면 무시 (재실행 시 정상)

    conn.commit()
    conn.close()
    print(f"✅ sbo2 DB 초기화 완료: {SBO2_DB_PATH}")


def save_candidate(name: str, grade: str, score: int,
                   vcp: bool, trend: bool, catalyst: bool,
                   curr: float, stop: float, tgt: float, rr: float,
                   bought: bool = False, skip_reason: str = ""):
    """후보 이력 저장"""
    try:
        conn = sqlite3.connect(SBO2_DB_PATH, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            INSERT INTO sbo2_candidates
                (scan_date, scan_time, stock_name, grade, score,
                 vcp_hit, trend_hit, catalyst_hit,
                 curr_price, stop_price, tgt_price, rr_ratio,
                 bought, skip_reason)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            today_str(), now_hms(), name, grade, score,
            int(vcp), int(trend), int(catalyst),
            curr, stop, tgt, rr,
            int(bought), skip_reason
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"⚠️ 후보 저장 오류: {e}")


def save_buy_trade(code: str, name: str, grade: str,
                   vcp: bool, trend: bool, catalyst: bool,
                   buy_price: float, qty: int, amount: float,
                   score: int, stop: float, tgt: float, rr: float,
                   atr_val: float = 0):
    """매수 이력 저장"""
    try:
        conn = sqlite3.connect(SBO2_DB_PATH, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            INSERT INTO sbo2_trades
                (code, stock_name, grade, vcp_hit, trend_hit, catalyst_hit,
                 buy_price, buy_time, buy_amount, qty, score,
                 stop_price, tgt_price, rr_ratio, atr_val_at_entry)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            code, name, grade, int(vcp), int(trend), int(catalyst),
            buy_price, now_full_ts(), amount, qty, score,
            stop, tgt, rr, atr_val
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"⚠️ 매수 저장 오류: {e}")


def save_sell_trade(code: str, sell_price: float, reason: str,
                    entry_price: float, qty: int, buy_time: str,
                    stock_name: str = "", grade: str = "", stage: int = 0):
    """매도 이력 업데이트 (매수 기록 없으면 INSERT)"""
    try:
        profit_rate = (sell_price - entry_price) / entry_price * 100 if entry_price else 0
        profit_krw  = (sell_price - entry_price) * qty

        def _calc_hold_days(buy_ts: str) -> int:
            """완전한 타임스탬프('YYYY-MM-DD HH:MM:SS') 또는 날짜만 있는
            과거 데이터('YYYY-MM-DD') 모두 안전하게 처리"""
            if not buy_ts:
                return 0
            date_part = buy_ts[:10]
            try:
                bd = datetime.datetime.strptime(date_part, "%Y-%m-%d").date()
                return (datetime.date.today() - bd).days
            except Exception:
                return 0

        sell_ts = now_full_ts()

        conn = sqlite3.connect(SBO2_DB_PATH, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")

        # 매수 기록 확인 — buy_time도 같이 가져와 DB에 실제 저장된 값으로
        # hold_days를 계산한다 (인자로 받은 buy_time은 메모리상의 값이라
        # 포맷이 다를 수 있어 신뢰하지 않음, 2026-06-27 수정)
        row = conn.execute("""
            SELECT id, buy_time, qty FROM sbo2_trades
            WHERE code = ? AND sell_time IS NULL
            ORDER BY id DESC LIMIT 1
        """, (code,)).fetchone()

        if row:
            row_id, row_buy_time, row_qty = row
            hold_days = _calc_hold_days(row_buy_time)
            # ★ 부분매도 처리 (2026-06-29 수정) — 목표가1 달성 시 50%만 매도
            #   하는 경우, DB 원본 행의 qty(매수 시 전체 수량)와 이번에 판
            #   qty가 다름. 과거엔 qty 컬럼을 건드리지 않아 부분매도인데도
            #   DB에는 전체 수량이 매도완료로 찍히는 버그가 있었음 (씨이랩
            #   9주 매수 후 1주만 팔았는데 DB엔 qty=9로 "목표1익절50%"가
            #   찍힌 사고). row_qty와 인자 qty가 다르면 행을 분할:
            #   - 원본 행은 "판 수량(qty)"만큼으로 줄여서 매도완료 처리
            #   - 남은 수량(row_qty - qty)은 새 행으로 분리해 계속 보유 상태 유지
            if row_qty and row_qty > qty:
                remain_qty = row_qty - qty
                conn.execute("""
                    UPDATE sbo2_trades
                    SET qty            = ?,
                        sell_price     = ?,
                        sell_time      = ?,
                        sell_reason    = ?,
                        profit_rate    = ?,
                        profit_krw     = ?,
                        hold_days      = ?,
                        stage_reached  = ?
                    WHERE id = ?
                """, (
                    qty, sell_price, sell_ts, reason,
                    round(profit_rate, 2), round(profit_krw, 0),
                    hold_days, stage, row_id
                ))
                # 잔여 수량은 새 행으로 분리 (계속 보유 — sell_time NULL)
                # stage_reached는 분리 시점에는 0(잔여분은 아직 추가 익절
                # 안 됨)이 맞아 컬럼 생략(DEFAULT 0), atr_val_at_entry는
                # 원본 값을 그대로 이어받아야 손절/목표 재계산 시 정확함
                conn.execute("""
                    INSERT INTO sbo2_trades
                        (code, stock_name, grade, buy_price, buy_time,
                         buy_amount, qty, score, stop_price, tgt_price,
                         rr_ratio, atr_val_at_entry)
                    SELECT code, stock_name, grade, buy_price, buy_time,
                           buy_amount, ?, score, stop_price, tgt_price,
                           rr_ratio, atr_val_at_entry
                    FROM sbo2_trades WHERE id = ?
                """, (remain_qty, row_id))
                print(f"   💾 부분매도 저장: {stock_name or code} {profit_rate:+.2f}% "
                      f"({qty}주 매도, {remain_qty}주 잔여)")
            else:
                # 전량매도 — 기존과 동일하게 원본 행 그대로 매도완료 처리
                conn.execute("""
                    UPDATE sbo2_trades
                    SET sell_price     = ?,
                        sell_time      = ?,
                        sell_reason    = ?,
                        profit_rate    = ?,
                        profit_krw     = ?,
                        hold_days      = ?,
                        stage_reached  = ?
                    WHERE id = ?
                """, (
                    sell_price, sell_ts, reason,
                    round(profit_rate, 2), round(profit_krw, 0),
                    hold_days, stage, row_id
                ))
                print(f"   💾 매도 저장: {stock_name or code} {profit_rate:+.2f}%")
        else:
            # 수동매수 등 매수 기록 없는 경우 INSERT
            # (buy_time 인자가 날짜만(today_str, "YYYY-MM-DD")일 수도 있고,
            #  과거 버그처럼 다른 포맷일 수도 있어 정규식으로 정확히 검증 후 보정)
            import re as _re
            if buy_time and _re.match(r'^\d{4}-\d{2}-\d{2}', buy_time):
                buy_ts = buy_time if len(buy_time) > 10 else f"{buy_time} 00:00:00"
            else:
                buy_ts = now_full_ts()  # 형식이 안 맞으면 안전하게 현재 시각으로
            hold_days = _calc_hold_days(buy_ts)
            conn.execute("""
                INSERT INTO sbo2_trades
                    (code, stock_name, grade, buy_price, buy_time, qty,
                     sell_price, sell_time, sell_reason,
                     profit_rate, profit_krw, hold_days, stage_reached)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                code, stock_name, grade or "실계좌",
                entry_price, buy_ts, qty,
                sell_price, sell_ts, reason,
                round(profit_rate, 2), round(profit_krw, 0), hold_days, stage
            ))
            print(f"   💾 매도 저장(신규): {stock_name or code} {profit_rate:+.2f}%")

        conn.commit()
        conn.close()
    except Exception as e:
        print(f"⚠️ 매도 저장 오류: {e}")


def get_trade_review(days: int = 30) -> str:
    """최근 N일 매매 리뷰"""
    try:
        since = (datetime.date.today() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
        conn  = sqlite3.connect(SBO2_DB_PATH, timeout=5)

        rows = conn.execute("""
            SELECT stock_name, grade, score,
                   vcp_hit, trend_hit, catalyst_hit,
                   buy_price, sell_price, profit_rate, profit_krw,
                   sell_reason, hold_days
            FROM sbo2_trades
            WHERE sell_time IS NOT NULL
              AND buy_time >= ?
            ORDER BY buy_time DESC
        """, (since,)).fetchall()
        conn.close()

        if not rows:
            return f"최근 {days}일 완료 거래 없어."

        total     = len(rows)
        wins      = sum(1 for r in rows if (r[8] or 0) > 0)
        total_krw = sum(r[9] or 0 for r in rows)
        win_rate  = wins / total * 100 if total else 0

        lines  = [f"📊 **[sbo2 매매 리뷰 — 최근 {days}일]**"]
        lines += [f"   총 {total}건 | 승률 {win_rate:.1f}% | 손익 {int(total_krw):,}원\n"]

        for r in rows:
            name, grade, score, vcp, trend, cat, bp, sp, prate, pkrw, reason, hdays = r
            tags = []
            if vcp:     tags.append("VCP")
            if trend:   tags.append("추세")
            if cat:     tags.append("촉매")
            emoji = "✅" if (prate or 0) > 0 else "❌"
            lines.append(
                f"  {emoji} {name}({grade}급/{score}점) "
                f"[{'/'.join(tags)}] "
                f"{prate:+.1f}% ({int(pkrw or 0):,}원) "
                f"| {reason} | {hdays}일 보유"
            )

        return "\n".join(lines)
    except Exception as e:
        return f"⚠️ 리뷰 조회 오류: {e}"


# ============================================================
# 상태 파일 헬퍼
# ============================================================
def _read_state() -> dict:
    try:
        if os.path.exists(BOT_STATE_FILE):
            with open(BOT_STATE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return {"paused": False, "positions": {}, "sold_today": {}, "sold_today_date": ""}

def _write_state(state: dict):
    try:
        with open(BOT_STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ 상태 저장 오류: {e}")

def _update_state(**kwargs):
    st = _read_state()
    st.update(kwargs)
    _write_state(st)

def _save_cand_date(date: str):
    """후보 갱신 날짜 상태파일에 저장"""
    st = _read_state()
    st["cand_date"] = date
    _write_state(st)


# ============================================================
# 점수 비례 매수금액 계산
# ============================================================
def calc_buy_amount(grade: str, psbl_cash: int, score: int = 0) -> int:
    """
    전략별 매수금액:
    - inter (교집합) → 150만원 (100%) 기준
    - swing/trend    → 125만원 (83%) 기준
    - tele (텔레)    → 100만원 (67%) 기준
    - 점수 보정: 80점 이상 +20%, 50점 미만 -20%, 그 사이는 기준 그대로
      (★ 2026-06-29 추가 — 기존엔 docstring에 "매수금액: 점수 비례"라고
      적혀 있었으나 실제로는 슬롯 등급으로만 고정금액이 정해져 점수가
      매수금액에 전혀 반영되지 않던 불일치를 해소)
    - 주문가능금액 초과 시 조정
    """
    if grade == SLOT_INTER:
        amount = BASE_BUY_AMT               # 150만원
    elif grade in (SLOT_SWING, SLOT_TREND):
        amount = int(BASE_BUY_AMT * 0.83)   # 125만원
    else:                                    # tele
        amount = int(BASE_BUY_AMT * 0.67)   # 100만원

    if score >= 80:
        amount = int(amount * 1.2)
    elif score < 50:
        amount = int(amount * 0.8)

    amount = min(amount, psbl_cash)
    return amount


# ============================================================
# swing_master 결과 파싱 (후보 상세 추출)
# ============================================================
def get_candidates() -> list:
    """
    4슬롯 전략별 후보 반환
    - inter (교집합): VCP + 추세 + 촉매 동시 통과 → 슬롯1 최우선
    - swing        : VCP only                     → 슬롯2
    - trend        : 추세 only                    → 슬롯3
    - tele         : 텔레스윙 (07:50/14:40)       → 슬롯4
    """
    from swing_analyzer import get_swing_data
    from trend_analyzer import get_trend_data

    catalyst_set = _get_catalyst_stocks()
    swing_data   = get_swing_data(top_n=20)
    trend_data   = get_trend_data(top_n=20)

    swing_names  = {d["name"] for d in swing_data}
    trend_names  = {d["name"] for d in trend_data}

    # 상세 데이터 맵 (name → dict)
    detail_map = {}
    for d in swing_data:
        detail_map[d["name"]] = d
    for d in trend_data:
        if d["name"] not in detail_map:
            detail_map[d["name"]] = d

    candidates = []

    # ── 슬롯1: 교집합 (VCP + 추세 + 촉매) ──────────────────────
    inter_names = swing_names & trend_names & catalyst_set
    for name in inter_names:
        d = detail_map.get(name, {})
        candidates.append({
            "name":     name,
            "grade":    SLOT_INTER,
            "score":    d.get("score", 100),  # 교집합 최고 우선순위
            "vcp":      True,
            "trend":    True,
            "catalyst": True,
            "curr":     d.get("curr_price", 0),
            "stop":     d.get("stop_price", 0),
            "tgt":      d.get("tgt_price", 0),
            "rr":       d.get("rr_ratio", 0),
            "themes":   d.get("themes", []),
        })

    # ── 슬롯2: 스윙 (VCP only, 교집합 제외) ────────────────────
    swing_only = swing_names - trend_names
    swing_list = []
    for name in swing_only:
        d = detail_map.get(name, {})
        swing_list.append({
            "name":     name,
            "grade":    SLOT_SWING,
            "score":    d.get("score", 0),
            "vcp":      True,
            "trend":    False,
            "catalyst": name in catalyst_set,
            "curr":     d.get("curr_price", 0),
            "stop":     d.get("stop_price", 0),
            "tgt":      d.get("tgt_price", 0),
            "rr":       d.get("rr_ratio", 0),
            "themes":   d.get("themes", []),
        })
    swing_list.sort(key=lambda x: x["score"], reverse=True)
    candidates += swing_list[:3]  # 상위 3개만 후보

    # ── 슬롯3: 추세 (trend only, 교집합 제외) ──────────────────
    trend_only = trend_names - swing_names
    trend_list = []
    for name in trend_only:
        d = detail_map.get(name, {})
        trend_list.append({
            "name":     name,
            "grade":    SLOT_TREND,
            "score":    d.get("score", 0),
            "vcp":      False,
            "trend":    True,
            "catalyst": name in catalyst_set,
            "curr":     d.get("curr_price", 0),
            "stop":     d.get("stop_price", 0),
            "tgt":      d.get("tgt_price", 0),
            "rr":       d.get("rr_ratio", 0),
            "themes":   d.get("themes", []),
        })
    trend_list.sort(key=lambda x: x["score"], reverse=True)
    candidates += trend_list[:3]  # 상위 3개만 후보

    return candidates


def get_tele_candidates() -> list:
    """
    슬롯4: 텔레스윙 후보 반환 (07:50, 14:40 갱신용)
    """
    try:
        tele_data = get_tele_swing_picks(top_n=5)
        result = []
        for d in tele_data:
            result.append({
                "name":     d.get("name", ""),
                "grade":    SLOT_TELE,
                "score":    d.get("score", 0),
                "vcp":      False,
                "trend":    False,
                "catalyst": False,
                "curr":     d.get("curr_price", 0),
                "stop":     d.get("stop_price", 0),
                "tgt":      d.get("tgt_price", 0),
                "rr":       d.get("rr_ratio", 0),
                "themes":   [],
            })
        return result
    except Exception as e:
        print(f"⚠️ 텔레스윙 후보 오류: {e}")
        return []


# ============================================================
# 종목코드 조회 (이름 → 코드)
# ============================================================
def get_stock_name(code: str) -> str:
    """코드 → 한글 종목명 조회 (kr_theme_stocks DB)"""
    import re
    try:
        db  = os.path.join(BASE_DIR, "kr_theme_finance.db")
        conn = sqlite3.connect(db, timeout=5)
        row  = conn.execute("""
            SELECT stock_name FROM kr_theme_stocks
            WHERE stock_name LIKE ? LIMIT 1
        """, (f"%{code}%",)).fetchone()
        conn.close()
        if row:
            return re.sub(r'(KOSPI|KOSDAQ).*|\d{6}', '', row[0]).strip()
    except Exception:
        pass
    return code


def get_stock_code(name: str) -> str:
    """kr_theme_finance.db 에서 종목명으로 코드 조회"""
    import re
    try:
        db = os.path.join(BASE_DIR, "kr_theme_finance.db")
        conn = sqlite3.connect(db, timeout=5)

        # 1. kr_theme_stocks 에서 조회 (코드 포함된 경우 많음)
        row = conn.execute("""
            SELECT DISTINCT stock_name FROM kr_theme_stocks
            WHERE stock_name LIKE ?
            LIMIT 1
        """, (f"%{name}%",)).fetchone()

        if row:
            m = re.search(r'(\d{6})', row[0])
            if m:
                conn.close()
                return m.group(1)

        # 2. kr_stock_daily_data 에서 폴백
        row = conn.execute("""
            SELECT stock_name FROM kr_stock_daily_data
            WHERE stock_name LIKE ?
            LIMIT 1
        """, (f"%{name}%",)).fetchone()
        conn.close()

        if row:
            m = re.search(r'(\d{6})', row[0])
            if m:
                return m.group(1)

    except Exception as e:
        print(f"⚠️ 코드 조회 오류 {name}: {e}")
    return ""


# ============================================================
# 메인 봇 클래스
# ============================================================
class Sbo2:

    def __init__(self):
        self.api        = KisAPI()
        self.positions  = {}       # {code: {entry, qty, stop, tgt, name, grade, buy_time}}
        self.sold_today = {}       # {code: time}
        self.candidates  = []       # 현재 후보 리스트
        self.api_fail_count = 0         # API 연속 실패 카운터
        self.atr_cache      = {}         # {code: (atr_rate, ts)}
        self._last_sell_prices = {}      # {code: curr} — _check_sell에서 조회한 현재가, 상태출력에서 재사용
        self._cand_ts    = 0        # 후보 마지막 갱신 시각
        self._cand_date  = ""       # 후보 마지막 갱신 날짜
        self._pending_orders = {}   # 미체결 주문 {code: (orgno, odno, qty)}
        # ★ 매수 직후 qty 동기화 보호 (2026-06-29 추가)
        #   한투 API가 매수 체결을 즉시 잔고에 반영하지 못하는 경우가 있어,
        #   _sync_real_positions()가 매수 직후 곧바로 실행되면 아직 일부만
        #   반영된 잔고로 qty를 잘못 덮어쓰는 레이스컨디션이 있었음
        #   (씨이랩 9주 매수 직후 목표가1 달성 시 half_qty가 9//2=4가 아닌
        #   1로 계산된 사고 — qty가 일시적으로 2~3으로 잘못 동기화됐던 것).
        #   이 딕셔너리에 매수 시각을 기록해두고, BUY_SYNC_GUARD_SEC 동안은
        #   해당 종목의 qty를 실계좌 동기화로 덮어쓰지 않음.
        self._buy_sync_guard = {}   # {code: 매수시각(epoch)}


        # 상태 복원
        st = _read_state()
        self.positions  = st.get("positions", {})
        self.sold_today = st.get("sold_today", {})
        self.candidates = st.get("candidates", [])
        self._cand_date = ""   # 재시작시 무조건 재스캔
        self._tele_refreshed_am = False  # 07:50 텔레스윙 갱신 플래그
        self._tele_refreshed_pm = False  # 14:40 텔레스윙 갱신 플래그
        if st.get("sold_today_date") != today_str():
            self.sold_today = {}

        init_sbo2_db()

        # 실계좌 포지션 동기화
        self._sync_real_positions()

        print("✅ [sbo2] 초기화 완료")
        print(f"   보유 포지션: {list(self.positions.keys())}")

    def _save_state(self):
        # 기존 pending_cmd/cmd_result는 보존 (덮어쓰기 방지)
        _existing = _read_state()
        _write_state({
            "positions":       self.positions,
            "sold_today":      self.sold_today,
            "sold_today_date": today_str(),
            "candidates":      self.candidates,
            "cand_date":       getattr(self, "_cand_date", ""),
            "pending_cmd":     _existing.get("pending_cmd"),
            "cmd_result":      _existing.get("cmd_result"),
            "paused":          _existing.get("paused", False),
        })

    def _handle_pending_command(self):
        """디스코드(키키/리나)에서 들어온 매도/정지 명령 처리"""
        st = _read_state()
        pending = st.get("pending_cmd")
        if not pending:
            return

        cmd_type = pending.get("type")

        if cmd_type == "sell":
            sell_code = pending.get("code", "")
            if sell_code in self.positions:
                pos = self.positions[sell_code]
                mdata = self.api.get_market_data(sell_code)
                curr  = float(mdata.get("stck_prpr", 0)) if mdata else pos["entry_price"]
                qty   = pos["qty"]
                ok = self.api.sell(sell_code, qty, price=int(curr))
                if ok:
                    rate = (curr - pos["entry_price"]) / pos["entry_price"] * 100
                    save_sell_trade(
                        code=sell_code, sell_price=curr, reason="즉시매도(AI비서)",
                        entry_price=pos["entry_price"], qty=qty,
                        buy_time=pos.get("buy_time", ""),
                        stock_name=pos.get("name", sell_code), grade=pos.get("grade", ""),
                        stage=pos.get("stage", 0),
                    )
                    if _master_record:
                        _master_record(
                            bot_type="sbo2", code=sell_code, stock_name=pos.get("name", sell_code),
                            buy_price=pos["entry_price"], sell_price=curr, qty=qty,
                            sell_reason="즉시매도(AI비서)", buy_tag=pos.get("grade", ""),
                            ai_score=pos.get("score", 0),
                        )
                    if _master_remove:
                        _master_remove("sbo2", sell_code)
                    del self.positions[sell_code]
                    _update_state(
                        cmd_result=f"✅ [sbo2] {sell_code} 즉시매도 완료 ({rate:+.1f}%)",
                        pending_cmd=None,
                    )
                    self._save_state()
                else:
                    _update_state(
                        cmd_result=f"❌ [sbo2] {sell_code} 매도 실패",
                        pending_cmd=None,
                    )
            else:
                _update_state(
                    cmd_result=f"⚠️ [sbo2] {sell_code} 보유 중이 아님",
                    pending_cmd=None,
                )

        elif cmd_type == "pause":
            _update_state(paused=True, cmd_result="⏸️ [sbo2] 일시중단", pending_cmd=None)
        elif cmd_type == "resume":
            _update_state(paused=False, cmd_result="▶️ [sbo2] 재개", pending_cmd=None)

    def _name(self, code: str) -> str:
        for pos in self.positions.values():
            if pos.get("code") == code:
                return pos.get("name", code)
        return code

    # ── 후보 갱신 ─────────────────────────────────────────────
    def _refresh_candidates(self):
        now   = time.time()
        today = today_str()
        now_t = now_hhmm()

        held_codes = set(self.positions.keys())
        held_names = {p.get("name") for p in self.positions.values()}

        def _filter(cands):
            result = [c for c in cands
                      if get_stock_code(c["name"]) not in held_codes
                      and c["name"] not in held_names]
            excluded = [c["name"] for c in cands if c not in result]
            if excluded:
                print(f"   ⏭️ 보유중 제외: {', '.join(excluded)}")
            return result

        # ── 07:50 텔레스윙 갱신 ────────────────────────────────
        if now_t >= "0750" and not getattr(self, '_tele_refreshed_am', False):
            print(f"\n📡 [sbo2] 07:50 텔레스윙 갱신...")
            try:
                tele_cands = _filter(get_tele_candidates())
                # 기존 tele 슬롯 교체
                self.candidates = [c for c in self.candidates if c["grade"] != SLOT_TELE]
                self.candidates += tele_cands
                self._tele_refreshed_am = True
                print(f"   텔레스윙: {len(tele_cands)}개")
            except Exception as e:
                print(f"⚠️ 텔레스윙 갱신 오류: {e}")

        # ── 14:40 텔레스윙 재갱신 ──────────────────────────────
        if now_t >= "1440" and not getattr(self, '_tele_refreshed_pm', False):
            print(f"\n📡 [sbo2] 14:40 텔레스윙 재갱신...")
            try:
                tele_cands = _filter(get_tele_candidates())
                self.candidates = [c for c in self.candidates if c["grade"] != SLOT_TELE]
                self.candidates += tele_cands
                self._tele_refreshed_pm = True
                print(f"   텔레스윙: {len(tele_cands)}개")
            except Exception as e:
                print(f"⚠️ 텔레스윙 재갱신 오류: {e}")

        # ── 전체 갱신 (하루 1회, 날짜 바뀌거나 처음 실행 시) ──
        if hasattr(self, '_cand_date') and self._cand_date == today:
            return
        # ★ 2026-06-29 수정: 재시작 직후 첫 갱신인지(_cand_date가 원래
        #   ""였던 경우) vs 진짜 날짜가 바뀐 갱신인지 구분.
        #   재시작 직후라면 위에서 이미 텔레스윙 갱신을 막 끝냈을 수 있는데,
        #   아래에서 무조건 _tele_refreshed_am/pm을 False로 리셋해버리면
        #   바로 다음 루프에서 텔레스윙이 불필요하게 한 번 더 실행되는
        #   버그가 있었음 (날짜 바뀐 경우엔 리셋이 맞지만, 재시작 직후
        #   첫 갱신에서는 막 처리한 결과를 그대로 유지해야 함).
        _is_restart_first_run = (self._cand_date == "")
        print(f"\n🔄 [sbo2] 후보 전체 갱신 중...")
        try:
            all_cands = _filter(get_candidates())
            # tele 슬롯은 유지하고 나머지만 교체
            tele_slot = [c for c in self.candidates if c["grade"] == SLOT_TELE]
            self.candidates = all_cands + tele_slot
            self._cand_date = today
            if not _is_restart_first_run:
                self._tele_refreshed_am = False
                self._tele_refreshed_pm = False
            _save_cand_date(self._cand_date)
        except Exception as e:
            print(f"⚠️ 후보 갱신 오류: {e}")

        inter = sum(1 for c in self.candidates if c["grade"] == SLOT_INTER)
        swing = sum(1 for c in self.candidates if c["grade"] == SLOT_SWING)
        trend = sum(1 for c in self.candidates if c["grade"] == SLOT_TREND)
        tele  = sum(1 for c in self.candidates if c["grade"] == SLOT_TELE)
        print(f"   교집합:{inter}개 스윙:{swing}개 추세:{trend}개 텔레:{tele}개")
        for c in self.candidates:
            save_candidate(
                name=c["name"], grade=c["grade"], score=c["score"],
                vcp=c["vcp"], trend=c["trend"], catalyst=c["catalyst"],
                curr=c["curr"], stop=c["stop"], tgt=c["tgt"], rr=c["rr"],
            )


    # ── 매수 체크 ─────────────────────────────────────────────
    def _check_buy(self):
        now_t = now_hhmm()
        if not (BUY_START_TIME <= now_t <= BUY_END_TIME):
            return

        # ★ 1차 익절 후 슬롯 반환 (주문가능금액 100만원 이상일 때만)
        익절중 = sum(1 for p in self.positions.values() if p.get("stage", 0) >= 1)
        _psbl_check = self.api.get_psbl_order_cash("005930")
        보너스 = 익절중 if _psbl_check >= 1_000_000 else 0
        slots = MAX_POSITIONS - len(self.positions) - len(self._pending_orders) + 보너스
        if slots <= 0:
            print("📦 [sbo2] 포지션 FULL")
            return

        # 텔레스윙은 _refresh_candidates에서 처리

        # 주문가능금액 — 위에서 이미 조회한 값 재사용 (중복 API 호출 방지)
        psbl_cash = _psbl_check
        # API 조회 실패시 예수금 직접 조회
        if psbl_cash <= 0:
            try:
                import requests as _rq
                url = f"{self.api.base_url}/uapi/domestic-stock/v1/trading/inquire-balance"
                headers = {"authorization": f"Bearer {self.api.token}",
                           "appkey": self.api.appkey, "appsecret": self.api.secret,
                           "tr_id": "TTTC8434R"}
                params = {"CANO": self.api.cano, "ACNT_PRDT_CD": self.api.acnt,
                          "AFHR_FLPR_YN": "N", "OFL_YN": "", "INQR_DVSN": "02",
                          "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N",
                          "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "00",
                          "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""}
                res = _rq.get(url, headers=headers, params=params, timeout=5).json()
                o2 = res.get("output2", [{}])
                if o2:
                    psbl_cash = int(float(o2[0].get("ord_psbl_cash") or
                                         o2[0].get("prvs_rcdl_excc_amt") or 0))
                    print(f"   💰 주문가능(폴백): {psbl_cash:,}원")
            except Exception as e:
                print(f"⚠️ 예수금 폴백 조회 오류: {e}")

        print(f"   💰 주문가능: {psbl_cash:,}원")
        if psbl_cash <= 0:
            print("⚠️ [sbo2] 주문가능금액 없음 — 매수 스킵")
            return
        if psbl_cash < MIN_BUY_CHECK_CASH:
            # ★ 2026-07-02: 슬롯은 남았지만 실제로 살 돈이 없으면 후보 전체를
            #   순회하며 현재가+MA40을 조회할 필요가 없음. 이 조회가 후보수 ×
            #   2번씩 매 루프(30초)마다 반복돼 KIS API 초당 호출제한에 자주
            #   걸리는 원인이었음 — 슬롯이 안 찼어도 예산이 없으면 여기서 컷.
            print(f"   💰 주문가능({psbl_cash:,}원) < 최소기준({MIN_BUY_CHECK_CASH:,}원) — 후보 조회 스킵")
            return

        # ── 4슬롯 전략별 매수 후보 구성 ────────────────────────
        held_codes = set(self.positions.keys())
        held_names = {p.get("name") for p in self.positions.values()}
        held_grades = {p.get("grade", "") for p in self.positions.values()}

        def _buyable(grade):
            return [c for c in self.candidates
                    if c["grade"] == grade
                    and c["name"] not in held_names
                    and get_stock_code(c["name"]) not in held_codes]

        # 슬롯별 이미 보유 여부 확인
        has_inter = SLOT_INTER in held_grades
        has_swing = SLOT_SWING in held_grades
        has_trend = SLOT_TREND in held_grades
        has_tele  = SLOT_TELE  in held_grades

        # 우선순위: 교집합 → 점수 높은 순 (스윙/추세/텔레)
        buyable = []
        if not has_inter:
            buyable += sorted(_buyable(SLOT_INTER), key=lambda x: x["score"], reverse=True)
        if not has_swing:
            buyable += sorted(_buyable(SLOT_SWING), key=lambda x: x["score"], reverse=True)
        if not has_trend:
            buyable += sorted(_buyable(SLOT_TREND), key=lambda x: x["score"], reverse=True)
        if not has_tele:
            buyable += sorted(_buyable(SLOT_TELE),  key=lambda x: x["score"], reverse=True)

        print(f"   매수후보: 교집합{len(_buyable(SLOT_INTER))} 스윙{len(_buyable(SLOT_SWING))} "
              f"추세{len(_buyable(SLOT_TREND))} 텔레{len(_buyable(SLOT_TELE))}")

        for cand in buyable:
            if slots <= 0:
                break

            name = cand["name"]
            code = get_stock_code(name)
            if not code:
                print(f"⚠️ 코드 조회 실패: {name}")
                continue

            if code in self.positions:
                continue

            # 코드 or 종목명으로 이중 체크
            already_held = (
                code in self.positions or
                any(p.get("name") == name for p in self.positions.values())
            )
            if already_held:
                print(f"⏭️ 이미 보유중: {name}({code}) - 스킵")
                continue

            # ★ sbot 교차 보유 방지
            try:
                import os as _os, json as _json
                _sbot_state_path = _os.path.join(
                    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                    "sbot_state.json")
                sbot_pos = set()
                if _os.path.exists(_sbot_state_path):
                    with open(_sbot_state_path, "r", encoding="utf-8") as _f:
                        _sbot_st = _json.load(_f)
                    sbot_pos = set(_sbot_st.get("last_status", {})
                                       .get("positions_detail", {}).keys())
                if code in sbot_pos:
                    print(f"⛔ {name}({code}) sbot 보유 중 — sbo2 매수 제외")
                    continue
            except Exception as _e:
                print(f"⚠️ sbot 포지션 조회 오류: {_e}")
            if code in self.sold_today:
                print(f"🚫 재매수 금지: {name}")
                save_candidate(name=name, grade=cand["grade"], score=cand["score"],
                               vcp=cand["vcp"], trend=cand["trend"], catalyst=cand["catalyst"],
                               curr=cand["curr"], stop=cand["stop"], tgt=cand["tgt"], rr=cand["rr"],
                               bought=False, skip_reason="재매수금지")
                continue

            # 현재가 조회
            mdata = self.api.get_market_data(code)
            if not mdata:
                continue
            curr_price = float(mdata.get("stck_prpr", 0))
            if not (MIN_PRICE <= curr_price <= MAX_PRICE):
                continue

            # ★ MA40 아래에서는 매수 금지 (매수 즉시 MA40이탈로 청산되는 헛매매 방지, 2026-06-19)
            try:
                _tech = self.api.get_technical_indicators(code, {})
                _ma40 = float(_tech.get("ma40", 0) or 0)
                if _ma40 > 0 and curr_price < _ma40:
                    print(f"⏭️ {name} 패스 — MA40({_ma40:,.0f}) 아래 (현재:{curr_price:,.0f})")
                    save_candidate(name=name, grade=cand["grade"], score=cand["score"],
                                   vcp=cand["vcp"], trend=cand["trend"], catalyst=cand["catalyst"],
                                   curr=curr_price, stop=cand["stop"], tgt=cand["tgt"], rr=cand["rr"],
                                   bought=False, skip_reason="MA40하단")
                    continue
            except Exception as _e:
                print(f"⚠️ MA40 조회 오류 {name}: {_e}")

            # 매수금액 계산 — 예수금 부족시 있는 만큼 매수
            # ★ score 전달 — 점수 기반 매수금액 보정 적용 (2026-06-29)
            amount = calc_buy_amount(cand["grade"], psbl_cash, score=cand.get("score", 0))

            # 예수금이 기본금액보다 적으면 있는 만큼으로 조정
            if psbl_cash < amount:
                amount = psbl_cash
                print(f"💡 {name} 예산 조정: {amount:,}원 (예수금 부족)")

            # 1주도 못 사면 패스
            if amount < curr_price:
                print(f"⏭️ {name} 패스 — 예산({amount:,}) < 주가({curr_price:,})")
                save_candidate(name=name, grade=cand["grade"], score=cand["score"],
                               vcp=cand["vcp"], trend=cand["trend"], catalyst=cand["catalyst"],
                               curr=curr_price, stop=cand["stop"], tgt=cand["tgt"], rr=cand["rr"],
                               bought=False, skip_reason="예산부족")
                continue

            # 매수 실행
            ok, orgno, odno, qty = self.api.buy(code, curr_price, amount, {code: name})
            if not ok or qty <= 0:
                continue

            # ★ 2026-06-29: qty는 buy()가 반환한 실제 주문 수량을 그대로 사용.
            #   기존엔 amount/curr_price로 따로 추정했는데, buy() 내부의
            #   실제 계산(호가단위 보정 + 수수료 반영)과 어긋날 수 있어
            #   씨이랩(189330) 사고의 근본 원인 중 하나였을 것으로 추정됨
            #   (_buy_sync_guard는 그 다음 루프의 잘못된 재동기화만 막을 뿐,
            #   최초 추정 자체의 오차는 막지 못함).

            # 미체결 추적 등록
            self._pending_orders[code] = (orgno or "", odno or "", qty)
            # ★ qty 동기화 보호 시작 — 한투 체결반영 지연 동안 _sync_real_positions()가
            #   잘못된(아직 부분반영된) 잔고로 qty를 덮어쓰는 것 방지
            self._buy_sync_guard[code] = time.time()

            # ★ ATR 기반 손절/목표가 계산 (추세추종 방식)
            #   최소 ATR 비율 1% 강제 — 더존비즈온처럼 변동성이 극단적으로
            #   낮은 종목은 ATR이 0에 가까워 목표/손절폭이 너무 좁아지고
            #   매수 직후 즉시 목표달성/손절이 동시에 발동하는 문제 방지 (2026-06-23)
            MIN_ATR_RATE_FOR_BUY = 0.01
            _atr_rate = self._get_atr_rate(code)
            if _atr_rate > 0 and _atr_rate < MIN_ATR_RATE_FOR_BUY:
                print(f"   ⚠️ {name} ATR 비율 {_atr_rate:.3%} 너무 낮음 → 최소값 {MIN_ATR_RATE_FOR_BUY:.0%} 적용")
                _atr_rate = MIN_ATR_RATE_FOR_BUY
            if _atr_rate > 0:
                _atr_val  = curr_price * _atr_rate
                _stop     = round(curr_price - _atr_val * 2.0, 0)
                # ★ 목표가1 상한 캡 (2026-06-23) — ATR×3과 +20% 중 작은 값
                #   고변동성 종목의 1차 목표가 사실상 도달불가능해지는 문제 방지
                _tgt_atr  = curr_price + _atr_val * 3.0
                _tgt_cap  = curr_price * 1.20
                _tgt      = round(min(_tgt_atr, _tgt_cap), 0)
            else:
                # ATR 없을 때 후보에서 제공한 값 or 폴백
                _stop = cand["stop"] or round(curr_price * 0.93, 0)
                _tgt  = cand["tgt"]  or round(curr_price * 1.12, 0)
                _atr_val = 0

            # 포지션 등록
            self.positions[code] = {
                "code":        code,
                "name":        name,
                "grade":       cand["grade"],
                "entry_price": curr_price,
                "qty":         qty,
                "buy_time":    today_str(),
                "stop_price":  _stop,
                "tgt_price":   _tgt,
                "target_next": _tgt,       # ★ 다음 목표가 (상향 추적)
                "atr_val":     _atr_val,   # ★ ATR 절대값 (목표가 상향 시 사용)
                "peak_price":  curr_price, # ★ 고점 추적 (트레일링용)
                "stage":       0,          # ★ 0=초기, 1=목표1달성, 2+=계속
                "score":       cand["score"],
                "vcp":         cand["vcp"],
                "trend":       cand["trend"],
                "catalyst":    cand["catalyst"],
            }
            self._save_state()

            # DB 저장
            save_buy_trade(
                code=code, name=name, grade=cand["grade"],
                vcp=cand["vcp"], trend=cand["trend"], catalyst=cand["catalyst"],
                buy_price=curr_price, qty=qty, amount=amount,
                score=cand["score"],
                stop=self.positions[code]["stop_price"],
                tgt=self.positions[code]["tgt_price"],
                rr=cand["rr"],
                atr_val=_atr_val,
            )
            save_candidate(name=name, grade=cand["grade"], score=cand["score"],
                           vcp=cand["vcp"], trend=cand["trend"], catalyst=cand["catalyst"],
                           curr=curr_price, stop=cand["stop"], tgt=cand["tgt"], rr=cand["rr"],
                           bought=True)

            # master_db 등록
            if _master_upsert:
                _master_upsert(
                    bot_type="sbo2", code=code, stock_name=name,
                    entry_price=curr_price, current_price=curr_price,
                    qty=qty, buy_tag=cand["grade"], ai_score=cand["score"],
                )

            tags = []
            if cand["vcp"]:     tags.append("VCP")
            if cand["trend"]:   tags.append("추세")
            if cand["catalyst"]: tags.append("촉매")
            _notify(
                f"🚀 [sbo2] 매수 {name}({code})\n"
                f"   {cand['grade']}급/{cand['score']}점 [{'/'.join(tags)}]\n"
                f"   {curr_price:,}원 × {qty}주 = {int(amount):,}원\n"
                f"   🎯 목표 {self.positions[code]['tgt_price']:,.0f}원 "
                f"🛑 손절 {self.positions[code]['stop_price']:,.0f}원",
                critical=True
            )
            psbl_cash -= amount
            slots -= 1
            time.sleep(1)

    # ── 매도 체크 ─────────────────────────────────────────────
    def _get_atr_rate(self, code: str) -> float:
        """ATR/현재가 비율 (30분 캐시)"""
        import time as _time
        now_ts = _time.time()
        if code in self.atr_cache:
            cached_rate, ts = self.atr_cache[code]
            if now_ts - ts < 1800:
                return cached_rate
        try:
            ohlc = self.api.get_daily_ohlc(code, days=20) if hasattr(self.api, 'get_daily_ohlc') else []
            if not ohlc:
                self.atr_cache[code] = (0.0, now_ts)
                return 0.0
            # ATR 계산 (14일)
            highs  = [float(o.get("stck_hgpr", 0)) for o in ohlc]
            lows   = [float(o.get("stck_lwpr", 0)) for o in ohlc]
            closes = [float(o.get("stck_clpr", 0)) for o in ohlc]
            trs = []
            for i in range(1, min(15, len(ohlc))):
                tr = max(highs[i] - lows[i],
                         abs(highs[i] - closes[i-1]),
                         abs(lows[i]  - closes[i-1]))
                trs.append(tr)
            atr     = sum(trs) / len(trs) if trs else 0
            cur_p   = closes[0] if closes else 1
            atr_rate = atr / cur_p if cur_p > 0 else 0
            self.atr_cache[code] = (atr_rate, now_ts)
            return atr_rate
        except Exception as e:
            print(f"⚠️ ATR 조회 오류 {code}: {e}")
            self.atr_cache[code] = (0.0, now_ts)
            return 0.0

    def _check_sell(self):
        now_t = now_hhmm()
        if not (SELL_START_TIME <= now_t <= SELL_END_TIME):
            return

        for code, pos in list(self.positions.items()):
            mdata = self.api.get_market_data(code)
            if not mdata:
                continue

            curr  = float(mdata.get("stck_prpr", 0))
            self._last_sell_prices[code] = curr  # ★ run() 상태출력에서 재사용 (중복 API 호출 방지)
            entry = pos["entry_price"]
            qty   = pos["qty"]
            stop  = pos["stop_price"]
            name  = pos.get("name", code)
            atr_val    = pos.get("atr_val", 0)
            peak_price = pos.get("peak_price", curr)
            stage      = pos.get("stage", 0)
            target_next = pos.get("target_next", pos.get("tgt_price", 0))

            if curr <= 0 or entry <= 0 or qty <= 0:
                continue

            rate = (curr - entry) / entry * 100

            # 고점 갱신
            if curr > peak_price:
                pos["peak_price"] = curr
                peak_price = curr

            reason = None

            # ⓪ 보유기한 초과 (stage==0, 목표가1 미달성 종목만 — 25일)
            if stage == 0:
                try:
                    import datetime as _dt
                    buy_date_str = pos.get("buy_time", "")
                    if buy_date_str:
                        buy_date  = _dt.datetime.strptime(buy_date_str, "%Y-%m-%d").date()
                        held_days = (_dt.date.today() - buy_date).days
                        if held_days >= 25:
                            reason = f"기한초과({rate:+.1f}%)"
                            print(f"⏰ 기한초과 {code} | 보유{held_days}일 | {rate:+.1f}%")
                except Exception:
                    pass

            # ② 손절가 이탈
            if not reason and stop > 0 and curr <= stop:
                reason = f"손절({rate:+.1f}%)"

            # ③ 트레일링 스탑 (목표가1 달성 이후)
            if not reason and stage >= 1 and atr_val > 0:
                trail_stop = peak_price - atr_val * 1.5
                if curr <= trail_stop:
                    reason = f"트레일링({rate:+.1f}%)"
                    print(f"🔻 트레일링 {code} | 고점:{peak_price:,.0f} → "
                          f"트레일:{trail_stop:,.0f} | 현재:{curr:,.0f}")
            elif not reason and stage >= 1 and atr_val == 0:
                # ATR 없을 때 폴백 트레일링 (고점 -5%)
                if curr <= peak_price * 0.95:
                    reason = f"트레일링({rate:+.1f}%)"

            # ④ 목표가 달성 → 손절/목표가 상향 (매도 안 함)
            if not reason and target_next > 0 and curr >= target_next:
                if stage == 0:
                    # ★ 목표가1 달성 → 50% 매도(수익실현)
                    half_qty = qty if qty <= 1 else qty // 2
                    if half_qty > 0:
                        ok_half = self.api.sell(code, half_qty, price=int(curr))
                        if ok_half:
                            save_sell_trade(
                                code=code, sell_price=curr, reason=f"목표1익절50%({rate:+.1f}%)",
                                entry_price=entry, qty=half_qty, buy_time=pos.get("buy_time", ""),
                                stock_name=name, grade=pos.get("grade", ""),
                                stage=stage,
                            )
                            if _master_record:
                                _master_record(
                                    bot_type="sbo2", code=code, stock_name=name,
                                    buy_price=entry, sell_price=curr, qty=half_qty,
                                    sell_reason=f"목표1익절50%({rate:+.1f}%)",
                                    buy_tag=pos.get("grade", ""), ai_score=pos.get("score", 0),
                                )
                            pos["qty"] = qty - half_qty
                            print(f"💰 목표1 50%매도 {code} | {half_qty}주 @ {curr:,.0f}원")
                    new_stop   = round(entry + atr_val * 1.0, 0) if atr_val > 0 else round(entry * 1.02, 0)
                    new_target = round(curr + atr_val * 3.0, 0) if atr_val > 0 else round(curr * 1.10, 0)
                    pos["stop_price"]  = new_stop
                    pos["tgt_price"]   = new_target
                    pos["target_next"] = new_target
                    pos["stage"]       = 1
                    self._save_state()
                    print(f"🎯 목표가1 달성 {code} ({rate:+.1f}%) | "
                          f"손절↑:{new_stop:,.0f} | 새목표:{new_target:,.0f}")
                    _notify(
                        f"🎯 [sbo2] 목표가1 달성 {name}({code}) — 50%매도\n"
                        f"   {rate:+.1f}% | 손절↑:{new_stop:,.0f} | 새목표:{new_target:,.0f}",
                        critical=False
                    )
                else:
                    new_stop   = target_next
                    new_target = round(curr + atr_val * 3.0, 0) if atr_val > 0 else round(curr * 1.10, 0)
                    pos["stop_price"]  = new_stop
                    pos["tgt_price"]   = new_target
                    pos["target_next"] = new_target
                    pos["stage"]       = stage + 1
                    self._save_state()
                    print(f"🎯 목표가{stage+1} 달성 {code} ({rate:+.1f}%) | "
                          f"손절↑:{new_stop:,.0f} | 새목표:{new_target:,.0f}")
                    _notify(
                        f"🎯 [sbo2] 목표가{stage+1} 달성 {name}({code})\n"
                        f"   {rate:+.1f}% | 손절↑:{new_stop:,.0f} | 새목표:{new_target:,.0f}",
                        critical=False
                    )
                continue  # 목표가 달성은 매도 안 함

            if not reason:
                continue

            # ── 매도 실행 ─────────────────────────────────────
            ok = self.api.sell(code, qty, price=int(curr))
            if not ok:
                continue

            # DB 저장
            save_sell_trade(
                code=code, sell_price=curr, reason=reason,
                entry_price=entry, qty=qty, buy_time=pos.get("buy_time", ""),
                stock_name=name, grade=pos.get("grade", ""),
                stage=stage,
            )

            # master_db 기록
            if _master_record:
                _master_record(
                    bot_type="sbo2", code=code, stock_name=name,
                    buy_price=entry, sell_price=curr, qty=qty,
                    sell_reason=reason, buy_tag=pos.get("grade", ""),
                    ai_score=pos.get("score", 0),
                )
            if _master_remove:
                _master_remove("sbo2", code)

            # ★ 매도 사유 무관하게 모든 매도는 당일 재매수 금지
            #   (이전: "손절"만 등록 → MA20이탈 등은 재매수 금지가 안 걸려
            #    매수↔매도 무한 반복 버그 발생. 2026-06-19 확인)
            self.sold_today[code] = now_hms()

            del self.positions[code]
            self._save_state()

            emoji = "💰" if rate > 0 else "💔"
            _notify(
                f"{emoji} [sbo2] 매도 {name}({code})\n"
                f"   {reason}\n"
                f"   {entry:,}원 → {curr:,}원 ({rate:+.1f}%)\n"
                f"   손익: {int((curr-entry)*qty):,}원",
                critical=True
            )

    # ── 메인 루프 ─────────────────────────────────────────────
    def _get_pending_orders(self) -> list:
        """미체결 매수 주문 조회"""
        try:
            import requests as _rq
            url = f"{self.api.base_url}/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl"
            headers = {
                "authorization": f"Bearer {self.api.token}",
                "appkey": self.api.appkey, "appsecret": self.api.secret,
                "tr_id": "TTTC8036R"
            }
            params = {
                "CANO": self.api.cano, "ACNT_PRDT_CD": self.api.acnt,
                "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
                "INQR_DVSN_1": "0", "INQR_DVSN_2": "0"
            }
            res = _rq.get(url, headers=headers, params=params, timeout=5).json()
            orders = []
            for item in res.get("output", []):
                # 매수 미체결만
                if item.get("sll_buy_dvsn_cd") != "02":  # 02=매수
                    continue
                rmn_qty = int(item.get("rmn_qty", 0))
                if rmn_qty <= 0:
                    continue
                orders.append({
                    "code":  item.get("pdno", ""),
                    "name":  item.get("prdt_name", ""),
                    "odno":  item.get("odno", ""),
                    "orgno": item.get("krx_fwdg_ord_orgno", ""),
                    "qty":   rmn_qty,
                    "price": float(item.get("ord_unpr", 0)),
                })
            return orders
        except Exception as e:
            print(f"⚠️ 미체결 조회 오류: {e}")
            return []

    def _cancel_stale_orders(self):
        """1루프 이상 미체결 주문 취소 (sbot 방식)"""
        # 1. 체결 완료된 종목 pending에서 제거
        for code in list(self.positions.keys()):
            self._pending_orders.pop(code, None)

        # 2. 남은 pending = 미체결 → 취소
        for code, (orgno, odno, qty) in list(self._pending_orders.items()):
            name = get_stock_name(code)
            print(f"   🚫 미체결 취소: {name}({code}) odno:{odno}")
            ok = self.api.cancel_order(orgno, odno, code, qty)
            if ok:
                _notify(f"🚫 [sbo2] 미체결 취소 {name}({code}) → 자금 반환 / 재매수 방지 등록")
            # 재매수 방지
            self.sold_today[code] = now_hms()
            self._pending_orders.pop(code, None)
            self._save_state()

    def _check_api_health(self, success: bool):
        """API 호출 성공/실패 추적 — 연속 실패 시 재시작"""
        if success:
            self.api_fail_count = 0
        else:
            self.api_fail_count += 1
            print(f"⚠️ [sbo2] API 실패 {self.api_fail_count}/{API_FAIL_MAX}회")
            if self.api_fail_count >= API_FAIL_MAX:
                print(f"🚨 [sbo2] API 연속 {API_FAIL_MAX}회 실패 → 재시작")
                _notify("🚨 [sbo2] API 연속 실패 → 자동 재시작", critical=True)
                import sys; sys.exit(1)  # systemd Restart=on-failure 트리거

    def _sync_real_positions(self):
        """
        실계좌 잔고 기준 포지션 동기화 (sbot 방식)
        매 루프 실행 — 실계좌가 진실
        """
        try:
            new_pos = self.api.get_current_positions()
            # ★ None = 진짜 API 조회 실패 / {} = 정상응답인데 보유종목 0개(구분 필요!)
            if new_pos is None:
                # 캐시 무효화 후 재시도
                if hasattr(self.api, '_pos_cache'):
                    self.api._pos_cache = {}
                    self.api._pos_cache_ts = 0
                new_pos = self.api.get_current_positions()
            if new_pos is None:
                print("⚠️ 실계좌 잔고 조회 실패 — 동기화 스킵 (캐시 유지)")
                self._check_api_health(False)   # ★ API 실패 카운트
                return
            self._check_api_health(True)        # ★ API 정상
            # new_pos가 {} (빈 딕셔너리)인 경우 → 진짜로 보유종목 0개. 정상 진행.

            # ── 수동매도/손절 감지 ─────────────────────────────
            for code in list(self.positions.keys()):
                if code not in new_pos and code not in self.sold_today:
                    self.sold_today[code] = now_hms()
                    print(f"   🔍 수동매도 감지: {code} → sold_today 추가")
                    if _master_remove:
                        _master_remove("sbo2", code)

            # ── 실계좌 기준으로 포지션 갱신 ───────────────────
            # 기존 포지션 메타(손절/목표/등급) 보존하면서 수량/평단 갱신
            # ★ 매수 직후 BUY_SYNC_GUARD_SEC 동안은 qty 동기화 스킵
            #   (한투 체결반영 지연으로 잘못된 qty가 들어와 50% 익절 수량
            #   계산이 망가지는 레이스컨디션 방지 — 2026-06-29)
            BUY_SYNC_GUARD_SEC = 90
            now_ts = time.time()
            updated = {}
            for code, rdata in new_pos.items():
                if code in self.positions:
                    # 기존 포지션 메타 유지 + 수량/평단 갱신
                    existing = self.positions[code]
                    guard_until = self._buy_sync_guard.get(code, 0) + BUY_SYNC_GUARD_SEC
                    if now_ts < guard_until:
                        # 보호 시간 내 — 메모리상 qty/entry_price 그대로 유지
                        print(f"   🛡️ {existing.get('name', code)}({code}) "
                              f"매수직후 동기화 보호 중 — qty 유지 "
                              f"(실계좌:{rdata['qty']}, 메모리:{existing['qty']})")
                    else:
                        existing["qty"]         = rdata["qty"]
                        existing["entry_price"] = rdata["entry_price"]
                    # 종목명이 코드 그대로면 DB에서 다시 조회
                    cur_name = existing.get("name", code)
                    if cur_name == code:
                        cur_name = get_stock_name(code)
                    existing["name"] = cur_name
                    updated[code] = existing
                else:
                    # 신규 (수동매수 또는 새로 잡힌 종목)
                    entry = rdata["entry_price"]
                    # 종목명: KIS API → DB 조회 → 코드 순으로 폴백
                    _name = rdata.get("name", "") or get_stock_name(code)
                    updated[code] = {
                        "code":        code,
                        "name":        _name,
                        "grade":       "실계좌",
                        "entry_price": entry,
                        "qty":         rdata["qty"],
                        "buy_time":    today_str(),
                        "stop_price":  round(entry * 0.90, 0),
                        "tgt_price":   round(entry * 1.15, 0),
                        "score":       0,
                        "vcp":         False,
                        "trend":       False,
                        "catalyst":    False,
                    }
                    print(f"   📥 신규: {rdata.get('name', code)}({code}) "
                          f"{entry:,}원 × {rdata['qty']}주")

            self.positions = updated
            # ★ 더 이상 보유하지 않는 종목의 가드 기록 정리 (메모리 누적 방지)
            for _code in list(self._buy_sync_guard.keys()):
                if _code not in updated:
                    self._buy_sync_guard.pop(_code, None)
            self._save_state()

        except Exception as e:
            print(f"⚠️ 실계좌 동기화 오류: {e}")

    def run(self):
        _notify("🤖 [sbo2] 리나 스윙봇 시작!", critical=True)
        print("\n" + "=" * 50)
        print("🤖 [sbo2] 리나 스윙봇 시작")
        print(f"   시드: {SEED_MONEY:,}원 | 1종목: {BASE_BUY_AMT:,}원 | 최대: {MAX_POSITIONS}종목")
        print("=" * 50)

        while True:
            try:
                now_t = now_hhmm()

                if is_weekend():
                    print(f"💤 주말 — 대기 중")
                    time.sleep(3600)
                    continue

                # 장외 시간
                if now_t < "0800" or now_t > "2000":
                    time.sleep(300)
                    continue

                print(f"\n⏰ [{now_hms()}] 루프 실행")

                # ── Heartbeat 기록 ────────────────────────
                pathlib.Path(HB_FILE).touch()

                # ★ 실계좌 동기화 (매 루프) — sbot 방식과 동일
                self._sync_real_positions()

                # ★ 디스코드(키키/리나) 명령 처리
                self._handle_pending_command()

                # ★ 미체결 주문 취소 (1루프 이상 경과된 미체결)
                self._cancel_stale_orders()

                # 후보 갱신 (30분마다)
                self._refresh_candidates()

                # 매도 체크 (항상)
                if self.positions:
                    self._check_sell()

                # 매수 체크
                self._check_buy()

                # 상태 출력
                print(f"   보유: {len(self.positions)}종목 | 후보: {len(self.candidates)}개")
                for code, pos in self.positions.items():
                    # ★ 2026-07-02: _check_sell()이 이미 조회한 현재가를 재사용 —
                    #   같은 루프에서 종목당 현재가를 두 번 조회하던 중복 제거.
                    #   캐시에 없으면(예: 매도체크 시간대 밖, 방금 신규매수 등) 새로 조회.
                    curr = self._last_sell_prices.get(code)
                    if curr is None:
                        mdata = self.api.get_market_data(code)
                        curr  = float(mdata.get("stck_prpr", 0)) if mdata else pos["entry_price"]
                    rate  = (curr - pos["entry_price"]) / pos["entry_price"] * 100
                    grade = pos.get('grade', '')
                    label = SLOT_LABEL.get(grade, grade)
                    print(f"   💼 {pos.get('name', code)}({label}) "
                          f"{rate:+.1f}% | 현재:{int(curr):,} | "
                          f"손절:{pos['stop_price']:,.0f} 목표:{pos['tgt_price']:,.0f}")

            except KeyboardInterrupt:
                print("\n⛔ [sbo2] 중단")
                _notify("⛔ [sbo2] 봇 중단", critical=True)
                break
            except Exception as e:
                print(f"❌ [sbo2] 루프 오류: {e}")
                time.sleep(60)

            time.sleep(LOOP_SLEEP)


# ============================================================
# 진입점
# ============================================================
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="sbo2 — 리나 스윙봇")
    parser.add_argument("--review", action="store_true", help="매매 리뷰 출력")
    parser.add_argument("--days",   type=int, default=30, help="리뷰 기간 (일)")
    args = parser.parse_args()

    if args.review:
        print(get_trade_review(args.days))
    else:
        bot = Sbo2()
        bot.run()
