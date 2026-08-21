"""
sbo2.py — 리나 관리 스윙봇 (3단 콤보 연동 버전)
================================================================
[설계 원칙]
- 후보 소스  : swing_master.py S/A급 종목만
- 시드머니   : 500만원 / 1종목 기본 150만원 / 최대 5종목 (2026-07-14: 완화트랙 추가로 4→5)
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
SEED_MONEY       = 6_000_000   # 시드머니 600만원 (2026-08-10 재투입분 반영)
BASE_BUY_AMT     = 1_500_000   # 1종목 기본 매수금액 150만원
# ★ 2026-08-10: 7→4로 축소(사용자 지적 — "6종목까지 가고 어떤 종목은
#   1주만 사고 그러고 있더라" — 슬롯이 너무 많이 늘어져 있었음). 4×150만
#   =600만원 시드와 딱 맞음. 익절중(stage>=1) 보너스 로직은 그대로 둬서
#   이미 1차 익절해 트레일링스탑 넘어간 종목은 슬롯 카운트에서 빠지고
#   그만큼 신규 슬롯이 열림 — 그래서 상황에 따라 4종목보다 많아질 수 있음.
MAX_POSITIONS    = 4
A_GRADE_RATIO    = 0.7          # A급 상위 70%만 매수

# 슬롯 전략 구분
SLOT_INTER  = "inter"   # 교집합 (VCP+추세+촉매)
SLOT_TREND  = "trend"   # 추세 (trend only)
SLOT_MOMENTUM = "momentum"  # ★ 2026-08-15 추가 — VCP(SLOT_SWING) 대체.
                        # VCP는 백테스트 퍼널 진단 결과 7개월간 1건만
                        # 나올 정도로 사실상 죽은 소스였음(30일 신고가
                        # 돌파+거래량서지 동시조건이 너무 희귀 — 사용자
                        # 판단으로 과감히 제외). 대신 07-10부터 관찰전용
                        # 으로 돌던 AI 모멘텀 스캐너(lina_bot.py 08:55/
                        # 14:35, ai_momentum_db 저장)가 60일 61.3% 적중률
                        # (31건 판정)로 검증됐다고 보고 실거래에 투입.
                        # 테마추출+매핑 로직은 lina_bot.py에 그대로 두고,
                        # sbo2는 당일 저장된 결과만 읽어 자체 게이트
                        # (MA40/시총/거래량/ATR 손절목표)를 거쳐 매수.
SLOT_TELE   = "tele"    # 텔레스윙 (매수 소스 제외, 라벨만 유지)
SLOT_LIGHT  = "light"   # 완화트랙 (★ 2026-07-14 추가) — 촉매종목 중 VCP/추세
                        # 정식조건은 못 채웠지만 차트가 안 망가진 종목. 7/7
                        # VCP 돌파확인 필터 추가 이후 쏠림장에서 VCP/추세가
                        # 7거래일 연속 0개를 내는 문제 발견(사용자 지적) —
                        # 모멘텀 스캐너의 완화트랙(_check_light_chart_health)과
                        # 동일 로직을 실거래 후보에도 최하위 우선순위로 도입.
SLOT_WATCHLIST = "watchlist"  # 한투 관심그룹 'new' (★ 2026-07-17 추가) — sbot과
                        # 동일하게, 사용자가 직접 계속 갱신하는 한투 'new'
                        # 관심그룹을 전체 후보가 적거나 없을 때만 보조로 사용.
                        # 사용자가 직접 큐레이션한 목록이라 완전 무필터는
                        # 아니고 완화트랙과 동일한 최소 안전장치만 적용.
SLOT_POOL   = "pool"    # 키움풀 최소게이트 (★ 2026-07-18 추가) — 키움
                        # 조건검색(눌림목/VCP/상승추세)이 이미 기술적
                        # 패턴을 검증했다는 전제로, VCP/추세 엄격조건
                        # 통과 못 한 풀 종목엔 최소게이트(_check_minimal_gate)
                        # 만 적용하고 텔레그램/한경컨센서스/MBN뉴스/촉매
                        # 겹침 점수(_calc_overlap_boost)로 순위를 매긴다.

SLOT_LABEL = {
    SLOT_INTER: "교집합",
    SLOT_MOMENTUM: "모멘텀",
    SLOT_TREND: "추세",
    SLOT_TELE:  "텔레",
    SLOT_LIGHT: "완화",
    SLOT_WATCHLIST: "관심종목",
    SLOT_POOL:  "키움풀",
    "실계좌":   "실계좌",
    "S":        "S급",
    "A":        "A급",
}
LOOP_SLEEP       = 30           # 루프 간격 (초)
# ★ 2026-08-21: 09:10→09:20 — sbot과 동일 사유(intelligence/
#   market_safety_stop.py의 쏠림 안전check가 09:19까지 시장폭 데이터를
#   모아 판단하므로, 그 전엔 매수를 시작하지 않도록 늦춤)
BUY_START_TIME   = "0920"       # 매수 시작
BUY_END_TIME     = "1520"       # 매수 마감
SELL_START_TIME  = "0800"       # 프리장부터 매도 체크
SELL_END_TIME    = "2000"       # 애프터장까지 매도 체크
CANDIDATE_REFRESH= 86400        # 후보 갱신 주기 (하루 1회)

MIN_PRICE        = 3_000        # 최소 주가
MAX_PRICE        = 3_000_000    # 최대 주가
MIN_BUY_CHECK_CASH = 200_000     # 주문가능금액이 이 밑이면 후보 전수 조회(현재가+MA40) 자체를 건너뜀
CANDIDATE_CAP_PER_SLOT = 3       # 슬롯당 후보 상위 N개만 유지 (교집합도 동일 적용, 2026-07-02)

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
    - inter (교집합)        → 150만원 (100%) 기준
    - momentum/trend       → 125만원 (83%) 기준
    - 그 외(레거시 tele)   → 100만원 (67%) 기준 — 텔레스윙은 2026-07-06
      매수 소스에서 제외되어 신규 후보엔 더 이상 생성되지 않음. 이 분기는
      제거 전에 매수된 기존 보유 종목의 grade가 여전히 "tele"인 경우를
      위한 폴백일 뿐, 실제로는 도달하지 않음 (calc_buy_amount는 신규
      후보 매수 시에만 호출되고, 기존 포지션 재계산엔 안 쓰임).
    - 점수 보정: 80점 이상 +20%, 50점 미만 -20%, 그 사이는 기준 그대로
      (★ 2026-06-29 추가 — 기존엔 docstring에 "매수금액: 점수 비례"라고
      적혀 있었으나 실제로는 슬롯 등급으로만 고정금액이 정해져 점수가
      매수금액에 전혀 반영되지 않던 불일치를 해소)
    - 주문가능금액 초과 시 조정
    """
    if grade == SLOT_INTER:
        amount = BASE_BUY_AMT               # 150만원
    elif grade in (SLOT_MOMENTUM, SLOT_TREND):
        amount = int(BASE_BUY_AMT * 0.83)   # 125만원
    elif grade == SLOT_LIGHT:
        amount = int(BASE_BUY_AMT * 0.67)   # 100만원 — 완화트랙(2026-07-14), 정식조건 미충족이라 최소 사이즈
    elif grade == SLOT_WATCHLIST:
        amount = int(BASE_BUY_AMT * 0.67)   # 100만원 — 한투 관심그룹(2026-07-17), 완화트랙과 동일 사이즈
    elif grade == SLOT_POOL:
        amount = int(BASE_BUY_AMT * 0.67)   # 100만원 — 키움풀 최소게이트(2026-07-18), 완화트랙과 동일 사이즈
    else:                                    # 레거시 tele 폴백 (도달 안 함)
        amount = int(BASE_BUY_AMT * 0.67)   # 100만원

    if score >= 80:
        amount = int(amount * 1.2)
    elif score < 50:
        amount = int(amount * 0.8)

    amount = min(amount, psbl_cash)
    return amount


def _check_light_chart_health(stock_name: str, conn: sqlite3.Connection, api=None) -> dict:
    """
    완화트랙 (★ 2026-07-14 추가, lina_bot.py 모멘텀 스캐너의 동일 로직 포팅) —
    VCP/추세의 다단계 조건 대신 "차트가 완전히 망가지지 않았다" 수준만
    가볍게 확인. 세 패턴 중 하나만 만족하면 통과:
    (A) 하락 전환: 최근 저점이 2~7일 전(너무 오래된 저점 제외)에 찍혔고
        현재가가 그 저점보다 3% 이상 위(★ 2026-08-07 강화 — lina_bot.py
        모멘텀 스캐너에서 발견된 문제(같은 종목이 며칠씩 연속 픽되는
        현상, 사용자 지적)를 여기도 동일하게 반영 — 저점 유효기간 상한 +
        반등폭 최소 기준 추가)
    (B) 박스권 상단 돌파 임박: 최근 15일 변동폭이 좁고(≤15%) 현재가가
        그 구간 상단 근처(3% 이내)이거나 이미 돌파
    (C) 거래량 서지: 200일선 위 + 52주 고점 대비 -20% 이내인 종목 중,
        당일 거래량이 최근 20일 평균 대비 300%+ 이고 양봉(현재가>시가)이며
        윗꼬리가 길지 않은 경우(고가 대비 3% 이내) — 방향 확인 없이
        거래량만 보면 폭락도 서지로 오판될 수 있어 양봉+윗꼬리 조건 필수.
    + 최소 안전장치: 60일선 대비 15% 이상 못 빠져있어야 함(완전 붕괴 배제).
    통과 시 {"pattern": ..., "curr_price", "stop_price", "tgt_price"} 반환, 아니면 {}.
    """
    rows = conn.execute("""
        SELECT close_price, volume FROM kr_stock_daily_data
        WHERE stock_name = ? ORDER BY date DESC LIMIT 260
    """, (stock_name,)).fetchall()
    closes  = [r[0] for r in rows if r[0] and r[0] > 0]
    volumes = [r[1] for r in rows if r[1] and r[1] > 0]
    if len(closes) < 30:
        return {}

    curr = closes[0]
    ma60 = sum(closes[:60]) / len(closes[:60]) if len(closes) >= 30 else 0
    if ma60 > 0 and curr < ma60 * 0.85:
        return {}  # 60일선 대비 15%+ 이탈 — 완전 붕괴, 완화트랙도 배제

    window = closes[0:15]
    lo, hi = min(window), max(window)

    pattern = None
    # (A) 하락 전환 — 저점이 2~7일 전(상한 있음)이고, 저점 대비 3%+ 반등
    idx_lo = window.index(lo)
    if 2 <= idx_lo <= 7 and lo > 0 and curr >= lo * 1.03:
        pattern = "하락전환"
    elif lo > 0 and (hi - lo) / lo <= 0.15 and curr >= hi * 0.97:
        pattern = "박스돌파임박"

    if not pattern and api and len(closes) >= 200 and len(volumes) >= 20:
        ma200 = sum(closes[:200]) / 200
        week52_high = max(closes[:252]) if len(closes) >= 252 else max(closes)
        if curr > ma200 and curr >= week52_high * 0.8:
            try:
                code = get_stock_code(stock_name)
                mdata = api.get_market_data(code) if code else None
                if mdata:
                    acml_vol = float(mdata.get("acml_vol", 0) or 0)
                    avg_vol20 = sum(volumes[:20]) / 20
                    day_open = float(mdata.get("stck_oprc", 0) or 0)
                    day_high = float(mdata.get("stck_hgpr", 0) or 0)
                    is_bullish   = day_open > 0 and curr > day_open
                    no_long_wick = day_high <= 0 or (day_high - curr) / curr <= 0.03
                    if (avg_vol20 > 0 and acml_vol >= avg_vol20 * 3.0
                            and is_bullish and no_long_wick):
                        pattern = "거래량서지"
            except Exception as e:
                print(f"⚠️ [sbo2] {stock_name} 거래량서지 조회 오류: {e}")

    if not pattern:
        return {}

    return {
        "pattern": pattern,
        "curr_price": curr,
        "stop_price": round(curr * 0.93, 0),
        "tgt_price":  round(curr * 1.12, 0),
    }


def _check_minimal_gate(stock_name: str, conn: sqlite3.Connection) -> dict:
    """
    ★ 2026-07-18 추가 — 키움 조건검색 풀 전용 최소 게이트.
    사용자 지적: "키움 검색식이 이미 많은 걸 체크해서 나온 종목인데,
    우리 내부에서 VCP/추세의 20일선밴드+VCP수축+거래량마름+돌파확인
    같은 무거운 조건을 또 걸면 이중필터링이 된다" — 키움 풀 종목엔
    "완전히 망가지지 않았는지"만 최소로 확인하고, 대신 텔레그램/한경
    컨센서스/MBN뉴스/촉매 겹침으로 점수를 매긴다(_calc_overlap_boost).
    체크: 200일선 위 + 60일선 -15%+ 붕괴 배제 + 최소 거래대금(잡주 방지)
    """
    rows = conn.execute("""
        SELECT close_price, volume FROM kr_stock_daily_data
        WHERE stock_name = ? ORDER BY date DESC LIMIT 220
    """, (stock_name,)).fetchall()
    closes  = [r[0] for r in rows if r[0] and r[0] > 0]
    volumes = [r[1] for r in rows if r[1] and r[1] > 0]
    if len(closes) < 60:
        return {}

    curr = closes[0]
    ma60 = sum(closes[:60]) / 60
    if curr < ma60 * 0.85:
        return {}  # 완전 붕괴 배제

    if len(closes) >= 200:
        ma200 = sum(closes[:200]) / 200
        if curr < ma200:
            return {}  # 장기추세 최소확인

    if len(volumes) >= 5:
        vol_avg5 = sum(volumes[:5]) / 5
        trading_value_eok = (curr * vol_avg5) / 100_000_000
        if trading_value_eok < 50:
            return {}  # 잡주 배제 (swing_analyzer MIN_TRADING_VALUE_EOK와 동일 기준)

    if len(closes) < 15:
        return {}
    atr = sum(abs(closes[i] - closes[i+1]) for i in range(14)) / 14 if len(closes) >= 15 else 0
    stop_price = round(curr - atr * 1.5, 0)
    tgt_price  = round(curr + atr * 3.0, 0)
    if stop_price <= 0 or atr <= 0:
        return {}

    return {"curr_price": curr, "stop_price": stop_price, "tgt_price": tgt_price, "atr_val": atr}


def _get_mbn_news_names() -> set:
    """
    ★ 2026-07-18 추가 — MBN골드 뉴스(service_id=10001)에서 종목명 매칭.
    lina_bot.py의 fetch_mbngold_async()와 동일 로그인/크롤링 흐름을
    동기 버전으로 축소 포팅(lina_bot.py 전체를 import하면 디스코드
    클라이언트 등 무거운 부작용이 있어 자체 구현).
    """
    names = set()
    try:
        import requests as _req
        from bs4 import BeautifulSoup as _BS

        base_url = "https://www.mbngold.com"
        headers  = {"User-Agent": "Mozilla/5.0", "Referer": f"{base_url}/mg/mypage/login.php"}
        sess = _req.Session()
        sess.post(f"{base_url}/mg/mypage/login_action.php", headers=headers, data={
            "mode": "login", "rURL": f"{base_url}/mg/news/",
            "mID": os.getenv("MBNGOLD_ID", ""), "mPWD": os.getenv("MBNGOLD_PW", ""),
        }, timeout=10)

        list_url = f"{base_url}/mg/news/index.php?news_service_id=10001"
        res  = sess.get(list_url, headers=headers, timeout=10)
        soup = _BS(res.content.decode("utf-8", errors="ignore"), "html.parser")

        titles = []
        for a in soup.find_all("a", href=True):
            if "view.php" in a["href"] and "news_no=MM" in a["href"]:
                t = a.get_text(strip=True)
                if t:
                    titles.append(t)
            if len(titles) >= 15:
                break

        if not titles:
            return names

        conn = sqlite3.connect(os.path.join(BASE_DIR, "kr_theme_finance.db"), timeout=5)
        cur  = conn.execute("SELECT DISTINCT stock_name FROM kr_stock_daily_data")
        stock_names = set()
        for (sname,) in cur.fetchall():
            import re as _re
            pure = _re.sub(r"\s*(KOSPI|KOSDAQ)\s*\d{6}$", "", sname).strip()
            if len(pure) >= 2:
                stock_names.add(pure)
        conn.close()

        combined = " ".join(titles)
        for name in stock_names:
            if name in combined:
                names.add(name)
        if names:
            print(f"   📰 MBN뉴스 종목 매칭: {len(names)}종목")
    except Exception as e:
        print(f"⚠️ [sbo2] MBN뉴스 조회 오류: {e}")
    return names


def _calc_overlap_boost(name: str, code: str, curr_price: float,
                        tele_scores: dict,
                        catalyst_names: set, news_names: set) -> tuple:
    """
    ★ 2026-07-18 추가, 2026-07-25 생쇼 소스 폐지로 4개로 축소 —
    4개 소스(텔레그램/한경컨센서스/MBN뉴스/촉매) 겹침 점수 가산.
    반환: (가산점, 겹친소스 라벨 리스트)
    """
    boost = 0
    reasons = []
    if tele_scores.get(name, 0) >= 30:
        boost += 10; reasons.append("텔레그램")
    if name in catalyst_names:
        boost += 10; reasons.append("촉매")
    if name in news_names:
        boost += 10; reasons.append("MBN뉴스")
    try:
        from consensus import apply_consensus_bonus
        cbonus, creason = apply_consensus_bonus(code, 0, curr_price) if code else (0, "")
        if cbonus > 0:
            boost += cbonus; reasons.append(f"한경컨센서스({creason})")
    except Exception as e:
        print(f"⚠️ [sbo2] 한경컨센서스 조회 오류 {name}: {e}")
    return boost, reasons


# ============================================================
# 키움 조건검색식 풀 (★ 2026-07-17 추가)
# ============================================================
_KIWOOM_POOL_CACHE = {"date": "", "names": set()}
KIWOOM_POOL_KEYWORDS = ["추세", "VCP", "눌림목"]

def _get_kiwoom_condition_pool() -> set:
    """
    sbot과 동일하게 키움 조건검색식으로 1차 후보 풀을 받아온다. sbot은
    모든 검색식(단타 제외)을 그대로 종목 풀로 쓰지만, sbo2는 사용자가
    별도로 만든 "추세"/"VCP"/"눌림목" 검색식만 골라 쓴다 — 전체 시장을
    EOD 데이터로 스캔하며 반복 발견됐던 자기모순/과필터링 버그의 근본
    원인(라이브 사전검증이 아예 없었음)을 없애기 위함. VCP/추세 스코어링
    자체는 그대로 유지하되, 스캔 범위를 이 "이미 실시간으로 살아있다고
    확인된" 풀로 좁힌다.
    검색식이 아직 없으면(사용자가 준비 중) 빈 set 반환 — 호출부가
    자동으로 전체시장 스캔(기존 동작)으로 폴백한다.
    """
    today = today_str()
    if _KIWOOM_POOL_CACHE["date"] == today:
        return _KIWOOM_POOL_CACHE["names"]

    names = set()
    try:
        import asyncio
        from kiwoom_api import KiwoomAPI
        kapi = KiwoomAPI()
        if kapi.enabled:
            name_map = {}
            loop = asyncio.new_event_loop()
            try:
                codes = loop.run_until_complete(kapi.get_condition_codes(
                    use_keywords=KIWOOM_POOL_KEYWORDS, code_name_map=name_map))
            finally:
                loop.close()
            names = {name_map[c] for c in codes if name_map.get(c)}
            if names:
                print(f"   🔍 키움 조건검색 풀({'/'.join(KIWOOM_POOL_KEYWORDS)}): {len(names)}종목")
            else:
                print(f"   ⚠️ 키움 조건검색식({'/'.join(KIWOOM_POOL_KEYWORDS)}) 미발견 — 전체시장 스캔으로 폴백")
    except Exception as e:
        print(f"⚠️ [sbo2] 키움 조건검색 풀 조회 오류: {e}")

    _KIWOOM_POOL_CACHE["names"] = names
    _KIWOOM_POOL_CACHE["date"]  = today
    return names


# ============================================================
# 한투 관심그룹 'new' (★ 2026-07-17 추가)
# ============================================================
_KIS_WATCHLIST_CACHE = {"date": "", "names": set()}

def _get_kis_new_watchlist_names(api) -> set:
    """
    sbot(_load_new_codes)과 동일하게 한투 'new' 관심그룹에서 종목명을
    받아온다. 사용자가 직접 계속 갱신하는(유망종목 추가/제거) 목록이라
    sbo2 전체 후보가 적거나 없을 때 보조 소스로 사용 — 전체 시장 스캔
    실패 시의 안전망.
    """
    today = today_str()
    if _KIS_WATCHLIST_CACHE["date"] == today:
        return _KIS_WATCHLIST_CACHE["names"]

    names = set()
    try:
        if api is None:
            return set()
        hts_id = os.getenv("KIS_HTS_ID2", os.getenv("KIS_HTS_ID", ""))
        if not hts_id:
            return set()
        groups = api.get_watchlist_groups(hts_id)
        target = next(
            ((gc, gn) for gc, gn in groups.items()
             if gn.lower() in ("new", "신규추천", "신규", "new추천")),
            None,
        )
        if not target:
            print("   ⚠️ 한투 'new' 관심그룹 없음")
        else:
            grp_code, _ = target
            stocks = api.get_watchlist_stocks(grp_code, hts_id)
            names = {name for _, name in stocks if name}
            if names:
                print(f"   🆕 한투 관심그룹 'new': {len(names)}종목")
    except Exception as e:
        print(f"⚠️ [sbo2] 한투 관심그룹 조회 오류: {e}")

    _KIS_WATCHLIST_CACHE["names"] = names
    _KIS_WATCHLIST_CACHE["date"]  = today
    return names


# ============================================================
# swing_master 결과 파싱 (후보 상세 추출)
# ============================================================
def get_candidates(api=None) -> list:
    """
    6슬롯 전략별 후보 반환
    - inter (교집합): 추세 + 촉매 동시 통과 → 슬롯1 최우선 (★ 2026-08-15:
      VCP 레그 제거 — 아래 momentum 슬롯 설명 참고, 원래 vcp∩추세∩촉매
      였으나 VCP가 빠지며 추세∩촉매로 재정의됨)
    - momentum     : AI 모멘텀 스캐너 당일 픽 (VCP 대체)  → 슬롯2
      (★ 2026-08-15: VCP(SLOT_SWING) 제거 — 백테스트 퍼널 진단 결과
      7개월간 30일 신고가 돌파+거래량 서지 동시조건을 통과한 게 2건뿐,
      최종 거래량서지까지 걸리면 0건으로 사실상 죽은 소스였음(사용자
      판단으로 과감히 제외). 대신 07-10부터 관찰전용으로 돌던 AI 모멘텀
      스캐너(lina_bot.py 08:55/14:35 테마추출→종목매핑, ai_momentum_db
      저장)가 60일 61.3% 적중률(31건 판정)로 검증되어 투입. AI가 테마를
      뽑고 결정론적 코드가 VCP∪추세로 게이팅해 저장한 결과를 그대로
      읽어와 sbo2 자체 게이트(MA40/시총/거래량/ATR)만 거쳐 매수.)
    - trend        : 추세 only                    → 슬롯3
      (★ 2026-07-25: 생쇼(전문가추천) 슬롯 제거 — MBN이 생쇼 뉴스
      코너 자체를 폐지해서(news_service_id=10020 게시글 0건, 사이트
      뉴스탭에서도 카테고리 소실 확인) 영구 중단된 소스가 됨.)
    - light         : 촉매 종목 중 VCP/추세 정식조건 미충족 + 완화조건 통과
                      → 슬롯5 최하위 우선순위 (★ 2026-07-14 추가)
      (7/7 VCP 피봇돌파 확인 필터 추가 이후 지금 같은 쏠림장에서 VCP/추세가
      7거래일 연속 0개를 내는 문제 발견 — 스마트머니까지 통과한 종목은
      있어도 "실제 30일 신고가 돌파"까지 요구하면 2,111개 중 1개만 남을
      정도로 장 자체가 얕음. 촉매(실시간 뉴스/수급 신호)는 있는데 아직
      정식 기술적 패턴을 못 갖춘 종목을 완화조건(_check_light_chart_health,
      모멘텀 스캐너와 동일 로직)으로 최소한만 걸러 최하위 슬롯으로 편입.
    api: KisAPI 인스턴스 (완화트랙의 거래량서지 패턴에서 실시간 시세 조회용,
         없으면 해당 패턴은 건너뜀 — 나머지 슬롯엔 영향 없음)
    """
    from trend_analyzer import get_trend_data

    kiwoom_pool  = _get_kiwoom_condition_pool()
    catalyst_set = _get_catalyst_stocks()
    trend_data   = get_trend_data(top_n=20, name_filter=kiwoom_pool or None)

    # ★ 2026-07-18 추가 — 겹침점수 보정용 소스 (텔레그램/MBN뉴스, catalyst_set은
    #   위에서 이미 조회, 한경컨센서스는 종목별 실시간 조회라
    #   _calc_overlap_boost 안에서 호출)
    try:
        from tele_swing_analyzer import _get_tele_stocks
        tele_scores = _get_tele_stocks()
    except Exception as e:
        print(f"⚠️ [sbo2] 텔레그램 조회 오류: {e}")
        tele_scores = {}
    news_names = _get_mbn_news_names()

    trend_names  = {d["name"] for d in trend_data}

    # 상세 데이터 맵 (name → dict)
    detail_map = {}
    for d in trend_data:
        if d["name"] not in detail_map:
            detail_map[d["name"]] = d

    candidates = []

    # ── 슬롯1: 교집합 (추세 + 촉매, ★ 08-15 VCP 레그 제거) ──────
    # ★ 2026-07-02: 스윙/추세 슬롯과 달리 캡이 없어서 교집합에 걸리는 종목이
    #   많은 날엔 _check_buy가 매 루프(30초)마다 그 후보 전체를 현재가+MA40
    #   조회하며 KIS API 호출이 몰리는 원인이 됐음 — 다른 슬롯과 동일하게
    #   점수 상위 N개로 캡.
    inter_names = trend_names & catalyst_set
    inter_list = []
    for name in inter_names:
        d = detail_map.get(name, {})
        inter_list.append({
            "name":     name,
            "grade":    SLOT_INTER,
            "score":    d.get("score", 100),  # 교집합 최고 우선순위
            "vcp":      False,
            "trend":    True,
            "catalyst": True,
            "curr":     d.get("curr_price", 0),
            "stop":     d.get("stop_price", 0),
            "tgt":      d.get("tgt_price", 0),
            "rr":       d.get("rr_ratio", 0),
            "themes":   d.get("themes", []),
        })
    inter_list.sort(key=lambda x: x["score"], reverse=True)
    candidates += inter_list[:CANDIDATE_CAP_PER_SLOT]

    # ── 슬롯2: 모멘텀 (AI 모멘텀 스캐너 당일 픽, 교집합 제외, ★ 08-15 VCP 대체) ──
    momentum_names = set()
    try:
        _mconn = sqlite3.connect(
            os.path.join(os.path.dirname(BASE_DIR), "intelligence", "ai_momentum_picks.db"),
            timeout=5)
        _mrows = _mconn.execute("""
            SELECT stock_name, buy_price, stop_price, tgt_price, theme
            FROM momentum_picks WHERE date = ? ORDER BY id DESC
        """, (today_str(),)).fetchall()
        _mconn.close()
    except Exception as e:
        print(f"⚠️ [sbo2] 모멘텀픽 조회 오류: {e}")
        _mrows = []

    momentum_list = []
    for name, buy_price, stop_price, tgt_price, theme in _mrows:
        if name in momentum_names or name in inter_names:
            continue
        momentum_names.add(name)
        momentum_list.append({
            "name":     name,
            "grade":    SLOT_MOMENTUM,
            "score":    75,   # 60일 61.3% 적중률 기준 — VCP처럼 vcp/trend
                              # 게이트를 다시 태우지 않고 일단 고정값 사용,
                              # 실거래 데이터 쌓이면 재검증
            "vcp":      False,
            "trend":    name in trend_names,
            "catalyst": name in catalyst_set,
            "curr":     buy_price or 0,
            "stop":     stop_price or 0,
            "tgt":      tgt_price or 0,
            "rr":       round((tgt_price - buy_price) / (buy_price - stop_price), 1)
                        if buy_price and stop_price and buy_price > stop_price else 0,
            "themes":   [theme] if theme else [],
        })
    candidates += momentum_list[:CANDIDATE_CAP_PER_SLOT]

    # ── 슬롯3: 추세 (trend only, 교집합 제외) ───────────────────
    trend_only = trend_names - momentum_names
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
    candidates += trend_list[:CANDIDATE_CAP_PER_SLOT]

    # ── 슬롯5: 완화트랙 (촉매 종목 중 VCP/추세 미충족 + 완화조건 통과) ──
    already_covered = trend_names | momentum_names
    light_pool = catalyst_set - already_covered
    light_list = []
    if light_pool:
        conn = sqlite3.connect(os.path.join(BASE_DIR, "kr_theme_finance.db"), timeout=5)
        for name in light_pool:
            light = _check_light_chart_health(name, conn, api)
            if light:
                light_list.append({
                    "name":     name,
                    "grade":    SLOT_LIGHT,
                    "score":    50,
                    "vcp":      False,
                    "trend":    False,
                    "catalyst": True,
                    "curr":     light["curr_price"],
                    "stop":     light["stop_price"],
                    "tgt":      light["tgt_price"],
                    "rr":       round((light["tgt_price"] - light["curr_price"]) /
                                       (light["curr_price"] - light["stop_price"]), 1)
                                if light["curr_price"] > light["stop_price"] else 0,
                    "themes":   [f"완화조건:{light['pattern']}"],
                })
        conn.close()
    light_list.sort(key=lambda x: x["score"], reverse=True)
    candidates += light_list[:CANDIDATE_CAP_PER_SLOT]

    # ── 슬롯6: 한투 'new' 관심종목 (전체 후보가 적거나 없을 때만 보조) ──
    # ★ 2026-07-17 추가: "sbo2 종목이 적거나 없으면 한투 new관심종목도
    #   같이 걸어도 된다 — 내가 계속 갱신하는 유망종목이니까"(사용자
    #   결정). 항상 쓰지 않고 위 5개 슬롯 합쳐서 min_positions 미만일
    #   때만 안전망으로 사용. 사용자가 직접 큐레이션한 목록이라도 실거래
    #   진입이라 완화트랙과 동일한 최소 안전장치(_check_light_chart_health)
    #   는 그대로 적용.
    if len(candidates) < MAX_POSITIONS:
        watchlist_names = _get_kis_new_watchlist_names(api) - already_covered - set(light_pool if light_pool else [])
        watchlist_list = []
        if watchlist_names:
            conn = sqlite3.connect(os.path.join(BASE_DIR, "kr_theme_finance.db"), timeout=5)
            for name in watchlist_names:
                wl = _check_light_chart_health(name, conn, api)
                if wl:
                    watchlist_list.append({
                        "name":     name,
                        "grade":    SLOT_WATCHLIST,
                        "score":    50,
                        "vcp":      False,
                        "trend":    False,
                        "catalyst": False,
                        "curr":     wl["curr_price"],
                        "stop":     wl["stop_price"],
                        "tgt":      wl["tgt_price"],
                        "rr":       round((wl["tgt_price"] - wl["curr_price"]) /
                                           (wl["curr_price"] - wl["stop_price"]), 1)
                                    if wl["curr_price"] > wl["stop_price"] else 0,
                        "themes":   [f"관심종목:{wl['pattern']}"],
                    })
            conn.close()
        watchlist_list.sort(key=lambda x: x["score"], reverse=True)
        candidates += watchlist_list[:CANDIDATE_CAP_PER_SLOT]

    # ── 슬롯7: 키움풀 최소게이트 ──────────────────────────────
    # ★ 2026-07-18 추가: 키움 조건검색(눌림목/VCP/상승추세)이 이미
    #   기술적 패턴을 검증했다는 전제로, VCP/추세 엄격조건을 통과 못 한
    #   나머지 풀 종목엔 최소게이트만 적용(이중필터링 방지, 사용자 결정).
    pool_only = kiwoom_pool - already_covered
    pool_list = []
    if pool_only:
        conn = sqlite3.connect(os.path.join(BASE_DIR, "kr_theme_finance.db"), timeout=5)
        for name in pool_only:
            mg = _check_minimal_gate(name, conn)
            if mg:
                pool_list.append({
                    "name":     name,
                    "grade":    SLOT_POOL,
                    "score":    50,
                    "vcp":      False,
                    "trend":    False,
                    "catalyst": name in catalyst_set,
                    "curr":     mg["curr_price"],
                    "stop":     mg["stop_price"],
                    "tgt":      mg["tgt_price"],
                    "rr":       round((mg["tgt_price"] - mg["curr_price"]) /
                                       (mg["curr_price"] - mg["stop_price"]), 1)
                                if mg["curr_price"] > mg["stop_price"] else 0,
                    "themes":   ["키움풀:최소게이트"],
                })
        conn.close()
    pool_list.sort(key=lambda x: x["score"], reverse=True)
    candidates += pool_list[:CANDIDATE_CAP_PER_SLOT]

    # ── 겹침 점수 보정 (전체 슬롯 공통) ────────────────────────
    # ★ 2026-07-18 추가, 2026-07-25 생쇼 소스 폐지로 4개로 축소:
    #   텔레그램/한경컨센서스/MBN뉴스/촉매 중 겹치는 소스가 있으면
    #   점수 가산 — 슬롯 내 우선순위(상위 N개 캡)에 반영되도록 재정렬은
    #   각 슬롯에서 이미 끝난 뒤 점수만 보정.
    for c in candidates:
        code = get_stock_code(c["name"])
        boost, reasons = _calc_overlap_boost(
            c["name"], code, c["curr"], tele_scores, catalyst_set, news_names)
        if boost:
            c["score"] += boost
            c["themes"] = list(c.get("themes", [])) + [f"겹침:{'/'.join(reasons)}"]

    return candidates


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

        # 1. kr_theme_stocks 에서 조회
        # ★ 2026-07-02: 기존 LIKE '%name%'는 저장포맷이 "{종목명}{시장}
        #   {코드}"로 붙어있다보니 "삼성전자" 조회 시 "삼성전자우"(완전히
        #   다른 우선주, 005935)까지 같이 매칭되고 ORDER BY 없는 LIMIT 1이라
        #   어느 게 나올지 예측 불가 — 잘못된 종목을 살 위험이 있었음.
        #   name 바로 뒤에 시장구분자가 붙는 행만 매칭하도록 앵커링해
        #   "삼성전자우" 같은 접두어 충돌을 확정적으로 배제.
        row = conn.execute("""
            SELECT DISTINCT stock_name FROM kr_theme_stocks
            WHERE stock_name LIKE ? OR stock_name LIKE ?
            LIMIT 1
        """, (f"{name}KOSPI %", f"{name}KOSDAQ %")).fetchone()

        if row:
            m = re.search(r'(\d{6})', row[0])
            if m:
                conn.close()
                return m.group(1)

        # 2. kr_stock_daily_data 에서 폴백 (코드 미포함 포맷 — 종목명 정확일치만)
        row = conn.execute("""
            SELECT stock_name FROM kr_stock_daily_data
            WHERE stock_name = ?
            LIMIT 1
        """, (name,)).fetchone()
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
        self._is_holiday      = False    # 휴장일 판단 결과 (★ 2026-07-17 추가)
        self._holiday_checked = ""       # 마지막으로 휴장일 체크한 날짜 (하루 1회만 조회)
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

        # ★ 2026-07-06: 텔레스윙 갱신(07:50/14:40) 제거 — 매수 소스에서
        #   빠졌으니 sbo2 안에서 더 이상 계산할 이유가 없음(사용자 결정).
        #   텔레스윙 리포트 자체는 lina_bot.py의 07:50/14:40 스케줄러가
        #   독립적으로 계속 제공하므로 "시장판단 자료" 용도는 그대로 유지됨.
        #   (부수효과: sbo2 자체 API/연산 부담도 그만큼 줄어듦)

        # ── 전체 갱신 (하루 1회, 날짜 바뀌거나 처음 실행 시) ──
        if hasattr(self, '_cand_date') and self._cand_date == today:
            return
        print(f"\n🔄 [sbo2] 후보 전체 갱신 중...")
        try:
            all_cands = _filter(get_candidates(api=self.api))
            self.candidates = all_cands
            self._cand_date = today
            _save_cand_date(self._cand_date)
        except Exception as e:
            print(f"⚠️ 후보 갱신 오류: {e}")

        inter    = sum(1 for c in self.candidates if c["grade"] == SLOT_INTER)
        momentum = sum(1 for c in self.candidates if c["grade"] == SLOT_MOMENTUM)
        trend    = sum(1 for c in self.candidates if c["grade"] == SLOT_TREND)
        light    = sum(1 for c in self.candidates if c["grade"] == SLOT_LIGHT)
        print(f"   교집합:{inter}개 모멘텀:{momentum}개 추세:{trend}개 완화:{light}개")
        for c in self.candidates:
            save_candidate(
                name=c["name"], grade=c["grade"], score=c["score"],
                vcp=c["vcp"], trend=c["trend"], catalyst=c["catalyst"],
                curr=c["curr"], stop=c["stop"], tgt=c["tgt"], rr=c["rr"],
            )

    def _refresh_momentum_candidates(self):
        """★ 2026-08-21 신설 — 모멘텀 슬롯이 08-15 실거래 투입 이후 단 한
        건도 후보로조차 안 잡힌 구조적 버그 발견(sbo2_candidates/sbo2_trades
        둘 다 grade='momentum' 0건 확인, 사용자가 "모멘텀은 매일 쌓였을테니
        들여다보자"고 요청해 발견). 원인: 후보 전체갱신(_refresh_candidates)
        이 "하루 1회, 그날 첫 루프(보통 08:00 직후)"에만 도는데, AI 모멘텀
        스캐너는 08:55/14:35에 그때그때 생성됨 — 즉 sbo2가 후보를 읽는
        시점엔 그날 모멘텀픽이 아직 존재한 적이 없어 영원히 못 잡히는
        구조였음. 모멘텀은 로컬 DB 조회만 하면 되고(API 호출 없음) 비용이
        가벼우므로, 전체갱신과 별도로 매 루프 독립적으로 재조회해 모멘텀
        슬롯만 갱신한다."""
        if not self.candidates:
            return  # 전체갱신이 아직 한 번도 안 됐으면(기동 직후) 건너뜀
        held_codes = set(self.positions.keys())
        held_names = {p.get("name") for p in self.positions.values()}
        inter_names = {c["name"] for c in self.candidates if c["grade"] == SLOT_INTER}

        try:
            _mconn = sqlite3.connect(
                os.path.join(os.path.dirname(BASE_DIR), "intelligence", "ai_momentum_picks.db"),
                timeout=5)
            _mrows = _mconn.execute("""
                SELECT stock_name, buy_price, stop_price, tgt_price, theme
                FROM momentum_picks WHERE date = ? ORDER BY id DESC
            """, (today_str(),)).fetchall()
            _mconn.close()
        except Exception as e:
            print(f"⚠️ [sbo2] 모멘텀픽 재조회 오류: {e}")
            return

        momentum_names = set()
        momentum_list = []
        for name, buy_price, stop_price, tgt_price, theme in _mrows:
            if name in momentum_names or name in inter_names:
                continue
            if get_stock_code(name) in held_codes or name in held_names:
                continue
            momentum_names.add(name)
            momentum_list.append({
                "name": name, "grade": SLOT_MOMENTUM, "score": 75,
                "vcp": False, "trend": False, "catalyst": False,
                "curr": buy_price or 0, "stop": stop_price or 0, "tgt": tgt_price or 0,
                "rr": round((tgt_price - buy_price) / (buy_price - stop_price), 1)
                    if buy_price and stop_price and buy_price > stop_price else 0,
                "themes": [theme] if theme else [],
            })

        momentum_list = momentum_list[:CANDIDATE_CAP_PER_SLOT]
        old_names = {c["name"] for c in self.candidates if c["grade"] == SLOT_MOMENTUM}
        new_names = {c["name"] for c in momentum_list}
        if new_names == old_names:
            return

        self.candidates = [c for c in self.candidates if c["grade"] != SLOT_MOMENTUM] + momentum_list
        print(f"   🔄 [sbo2] 모멘텀 후보 갱신: {len(new_names)}개 ({', '.join(new_names) or '없음'})")
        for c in momentum_list:
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
        # ★ 2026-08-18: 슬롯당 보유 1종목 제한 → 슬롯당 최대 2종목으로 완화
        #   (사용자 지적 — "하나의 슬롯은 계속 놀아.. 같은 종류는 2개까지
        #   허용하자"). 슬롯별 후보가 마르면(예: 교집합 0건) 그 슬롯 자리가
        #   MAX_POSITIONS 안 채워진 채로 계속 비어있는 문제가 있었음 — 다른
        #   슬롯이 후보가 있으면 그 슬롯에서 2번째 종목을 채울 수 있게 함.
        MAX_PER_SLOT_TYPE = 2
        from collections import Counter as _Counter
        grade_counts = _Counter(p.get("grade", "") for p in self.positions.values())

        def _buyable(grade):
            return [c for c in self.candidates
                    if c["grade"] == grade
                    and c["name"] not in held_names
                    and get_stock_code(c["name"]) not in held_codes]

        # 슬롯별 이미 상한(2개) 도달 여부 확인
        has_inter = grade_counts[SLOT_INTER] >= MAX_PER_SLOT_TYPE
        has_momentum = grade_counts[SLOT_MOMENTUM] >= MAX_PER_SLOT_TYPE
        has_trend = grade_counts[SLOT_TREND] >= MAX_PER_SLOT_TYPE
        has_light = grade_counts[SLOT_LIGHT] >= MAX_PER_SLOT_TYPE
        has_watchlist = grade_counts[SLOT_WATCHLIST] >= MAX_PER_SLOT_TYPE
        has_pool = grade_counts[SLOT_POOL] >= MAX_PER_SLOT_TYPE

        # ★ 2026-07-06: 텔레스윙을 매수 소스에서 제외 (사용자 결정) —
        #   사후검증 결과 텔레스윙이 표본 1368건 중 손절률 77.3%로 압도적으로
        #   나빴음. 뉴스/언급 기반이라 사실상 단타에 가까운 신호라 스윙
        #   매수 판단에는 더 이상 쓰지 않고, 시장 판단 참고자료로만 남긴다
        #   (텔레스윙 스캔/리포트 자체는 lina_bot.py 07:50·14:40 스케줄러와
        #   !텔레스윙 명령으로 계속 제공됨 — 여기서 빼는 건 sbo2 매수풀뿐).
        # ★ 2026-07-14: 완화트랙(SLOT_LIGHT) 추가 — 정식조건 미충족 최하위
        #   신뢰도 슬롯이라 우선순위 맨 뒤에 둔다.
        # ★ 2026-07-25: 생쇼(SLOT_SSHOW) 슬롯 제거 — MBN이 생쇼 뉴스 코너
        #   자체를 폐지해서 소스가 영구 중단됨.
        # ★ 2026-08-15: VCP(SLOT_SWING) 제거 → SLOT_MOMENTUM으로 대체.
        # 우선순위: 교집합 → 점수 높은 순 (모멘텀/추세/완화)
        buyable = []
        if not has_inter:
            buyable += sorted(_buyable(SLOT_INTER), key=lambda x: x["score"], reverse=True)
        if not has_momentum:
            buyable += sorted(_buyable(SLOT_MOMENTUM), key=lambda x: x["score"], reverse=True)
        if not has_trend:
            buyable += sorted(_buyable(SLOT_TREND), key=lambda x: x["score"], reverse=True)
        if not has_light:
            buyable += sorted(_buyable(SLOT_LIGHT), key=lambda x: x["score"], reverse=True)
        if not has_watchlist:
            buyable += sorted(_buyable(SLOT_WATCHLIST), key=lambda x: x["score"], reverse=True)
        if not has_pool:
            buyable += sorted(_buyable(SLOT_POOL), key=lambda x: x["score"], reverse=True)

        print(f"   매수후보: 교집합{len(_buyable(SLOT_INTER))} 모멘텀{len(_buyable(SLOT_MOMENTUM))} "
              f"추세{len(_buyable(SLOT_TREND))} "
              f"완화{len(_buyable(SLOT_LIGHT))} 관심종목{len(_buyable(SLOT_WATCHLIST))} "
              f"키움풀{len(_buyable(SLOT_POOL))} (텔레 제외됨)")

        for cand in buyable:
            if slots <= 0:
                break

            # ★ 2026-08-18: buyable 리스트는 루프 시작 시점 스냅샷이라,
            #   같은 루프 안에서 한 슬롯의 후보 여러 개를 연달아 사고
            #   MAX_PER_SLOT_TYPE(2) 상한을 넘겨버릴 수 있음 — 매수 성공할
            #   때마다 grade_counts를 갱신해 실시간으로 다시 체크.
            if grade_counts[cand["grade"]] >= MAX_PER_SLOT_TYPE:
                continue

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

            # ★ sbot 교차 보유 방지 — master_db 기반 (2026-07-02)
            #   기존엔 sbot_state.json을 직접 열어 읽었음 — 락이 없어 sbot이
            #   쓰는 도중이면 깨진 JSON을 만날 수 있고, 상대 state 파일의
            #   내부 스키마(last_status.positions_detail)에 그대로 의존해
            #   구조가 바뀌면 조용히 깨지는 구조였음. 두 봇 다 매수/매도마다
            #   이미 기록하는 master_db(master_positions)를 단일 기준으로 사용.
            #   조회 실패 시엔 기존과 동일하게 "교차 보유 없음"으로 보고
            #   매수를 막지는 않음(안전 폴백 — 이 체크는 사고 방지용 부가
            #   안전장치일 뿐 매수 자체를 중단시킬 이유는 아님).
            sbot_pos = set()
            if get_all_positions:
                try:
                    sbot_pos = {p["code"] for p in get_all_positions() if p["bot_type"] == "sbot"}
                except Exception as _e:
                    print(f"⚠️ sbot 포지션 조회 오류: {_e}")
            if code in sbot_pos:
                print(f"⛔ {name}({code}) sbot 보유 중 — sbo2 매수 제외")
                continue
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

            # ★ 2026-08-10 추가 — 시가총액/거래량 최소 기준 (사용자 지적:
            #   "거래량이 거의 없는데 매수를 하는듯 하더군.. 기본체크를
            #   거의 빼버리니까 발생한 문제 같아"). 완화트랙/키움풀 등
            #   여러 슬롯이 늘면서 정작 잡주 배제 안전장치가 약해진 걸
            #   보완 — 모든 매수 경로가 거치는 이 지점에 공통 게이트로 추가.
            MIN_MARKET_CAP_EOK = 3000     # 시가총액 3,000억원 이상
            MIN_PREV_VOLUME    = 300_000  # 전일거래량 30만주 이상
            _mkt_cap = float(mdata.get("hts_avls", 0) or 0)  # 억원 단위 (KIS 시가총액)
            if _mkt_cap > 0 and _mkt_cap < MIN_MARKET_CAP_EOK:
                print(f"⏭️ {name} 패스 — 시가총액({_mkt_cap:,.0f}억) < 최소기준({MIN_MARKET_CAP_EOK:,}억)")
                save_candidate(name=name, grade=cand["grade"], score=cand["score"],
                               vcp=cand["vcp"], trend=cand["trend"], catalyst=cand["catalyst"],
                               curr=curr_price, stop=cand["stop"], tgt=cand["tgt"], rr=cand["rr"],
                               bought=False, skip_reason="시총미달")
                continue
            try:
                _vconn = sqlite3.connect(os.path.join(BASE_DIR, "kr_theme_finance.db"), timeout=5)
                _vrow = _vconn.execute("""
                    SELECT volume FROM kr_stock_daily_data
                    WHERE stock_name = ? ORDER BY date DESC LIMIT 1
                """, (name,)).fetchone()
                _vconn.close()
                _prev_vol = _vrow[0] if _vrow and _vrow[0] else 0
                if _prev_vol and _prev_vol < MIN_PREV_VOLUME:
                    print(f"⏭️ {name} 패스 — 전일거래량({_prev_vol:,}주) < 최소기준({MIN_PREV_VOLUME:,}주)")
                    save_candidate(name=name, grade=cand["grade"], score=cand["score"],
                                   vcp=cand["vcp"], trend=cand["trend"], catalyst=cand["catalyst"],
                                   curr=curr_price, stop=cand["stop"], tgt=cand["tgt"], rr=cand["rr"],
                                   bought=False, skip_reason="거래량미달")
                    continue
            except Exception as _e:
                print(f"⚠️ 거래량 조회 오류 {name}: {_e}")

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

            # 매수 실행 — extra_ticks=1(총 +2호가)로 체결 확률 높임 (2026-08-10,
            # 사용자 지적: 지정가가 타이트해 일부만 체결되고 나머지가 취소되는
            # 사고 — 스윙 매매라 1~2틱 슬리피지는 문제 안 됨)
            ok, orgno, odno, qty = self.api.buy(code, curr_price, amount, {code: name}, extra_ticks=1)
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
                # ★ 2026-08-17: 목표가1 배수 3.0→2.0 (사용자 지적 — 목표가
                #   너무 높아 도달 전에 손절되는 경우가 많음, 목표1 달성 후
                #   ATR 재설정(3.0 유지, 아래 _check_sell 참고)되니 초기
                #   목표만 낮춰도 됨). kr_theme_finance.db 백테스트로 검증:
                #   7.5개월(01-01~07-16)/전체11개월(09-09~08-14) 두 구간 다
                #   3.0→2.5→2.0 순서로 수익률/승률/PF/MDD 전부 개선
                #   (예: 11개월 기준 수익률 3.0=-11.68%→2.5=-7.75%→2.0=-0.11%).
                #   1.5까지 더 내려보니 7.5개월 구간은 더 좋아지지만(+7.86%)
                #   11개월 구간에선 오히려 악화(-4.01%)해 과최적화 신호 —
                #   두 구간 모두에서 견고한 2.0을 채택.
                _tgt_atr  = curr_price + _atr_val * 2.0
                _tgt_cap  = curr_price * 1.20
                _tgt      = round(min(_tgt_atr, _tgt_cap), 0)
            else:
                # ★ 2026-08-10 수정 — ATR 조회 실패 시 후보의 고정 손절/목표가
                #   (cand["stop"]/cand["tgt"])를 그대로 쓰던 게 문제였음: 후보는
                #   하루 한 번만 갱신되는데, 스캔 시점 가격 기준으로 계산된
                #   절대가라서 실제 매수 체결가와 시간차가 벌어지면 손절가가
                #   매수가 바로 코앞까지 붙어버릴 수 있었음(화일약품 3원차,
                #   프로티아 49원차로 매수 직후 바로 손절된 실사례 — 사용자
                #   지적). 항상 실제 체결가(curr_price) 기준 고정비율로
                #   재계산해 이 문제를 근본적으로 차단.
                _stop = round(curr_price * 0.90, 0)   # -10% 고정 손절
                _tgt  = round(curr_price * 1.15, 0)   # +15% 고정 목표
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
            grade_counts[cand["grade"]] += 1   # ★ 슬롯당 상한(2) 실시간 반영
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
        """ATR/현재가 비율 (성공 시 30분 캐시, 실패 시 60초만 — 2026-08-10 수정:
        기존엔 API 조회 실패도 30분씩 캐싱해서, 장시작 직후처럼 일시적으로
        조회가 막히면 그 30분 내내 ATR=0으로 취급돼 손절/목표가 폴백
        경로로 계속 새는 문제가 있었음(화일약품/프로티아 실사례)."""
        import time as _time
        now_ts = _time.time()
        FAIL_CACHE_SEC = 60
        if code in self.atr_cache:
            cached_rate, ts = self.atr_cache[code]
            ttl = 1800 if cached_rate > 0 else FAIL_CACHE_SEC
            if now_ts - ts < ttl:
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

            # ★ 2026-07-07: 보유기한(25일) 강제청산 로직 제거 — sbot과 동일하게
            #   ATR 손절/트레일링/목표가만으로 관리 (사용자 결정, 최근 장세에서
            #   기간매도가 손실 구간 포지션을 강제로 털어버리는 부작용 반복됨).

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
        # ★ 2026-07-23 버그 수정: 취소 성공/실패와 무관하게 무조건
        #   sold_today에 등록하고 있었음. cancel_order()가 실패하는 이유가
        #   "정정취소 가능수량이 없습니다"(=주문이 이미 전량 체결됨)인
        #   경우가 있는데, 이 경우는 미체결이 아니라 정상 체결된 포지션
        #   이라 sold_today 등록이 완전히 틀림(KB금융 실사례 - 체결됐는데
        #   sold_today 등록되어 재입양 영구 차단됨). 취소가 실제로
        #   성공했을 때만(=진짜 미체결이었을 때만) sold_today 등록.
        for code, (orgno, odno, qty) in list(self._pending_orders.items()):
            name = get_stock_name(code)
            print(f"   🚫 미체결 취소: {name}({code}) odno:{odno}")
            ok = self.api.cancel_order(orgno, odno, code, qty)
            if ok:
                _notify(f"🚫 [sbo2] 미체결 취소 {name}({code}) → 자금 반환 / 재매수 방지 등록")
                # 재매수 방지 — 취소가 실제로 성공(=진짜 미체결)했을 때만
                self.sold_today[code] = now_hms()
            else:
                print(f"   ℹ️ {name}({code}) 취소 실패 — 이미 체결된 것으로 보여 "
                      f"sold_today 등록 안 함 (다음 동기화에서 정상 포지션으로 반영됨)")
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

            # ★ 2026-07-21: 매수 직후 qty 동기화 보호(BUY_SYNC_GUARD_SEC)와
            #   동일한 기준시간을 여기서도 써야 해서 위로 이동 — 아래
            #   "수동매도 감지" 루프가 이 값을 참조.
            BUY_SYNC_GUARD_SEC = 90

            # ── 수동매도/손절 감지 ─────────────────────────────
            # ★ 2026-07-21 버그 수정: 방금 매수한 종목이 한투 체결반영
            #   지연(정정취소 API에서도 "정정취소 가능수량 없음"으로 확인
            #   되듯, 주문 자체는 정상 체결됐는데 잔고 조회 API에 아직
            #   반영이 안 된 경우)으로 new_pos에 잠깐 안 잡히면, 실제로는
            #   멀쩡히 보유 중인데 "수동매도"로 오판해 포지션을 잃어버리는
            #   사고가 있었음(피에스케이/코스맥스 실사례, 매수 30초만에
            #   오탐 — 사용자가 HTS에서 정상 보유 확인해줌). 아래 qty
            #   동기화 보호와 동일하게, 매수 직후 BUY_SYNC_GUARD_SEC 동안은
            #   이 종목을 수동매도 감지 대상에서 제외.
            manual_sold_codes = []
            for code in list(self.positions.keys()):
                guard_until = self._buy_sync_guard.get(code, 0) + BUY_SYNC_GUARD_SEC
                if code not in new_pos and code not in self.sold_today:
                    if time.time() < guard_until:
                        print(f"   🛡️ {self.positions[code].get('name', code)}({code}) "
                              f"매수직후 동기화 보호 중 — 수동매도 감지 스킵")
                        continue
                    manual_sold_codes.append(code)

            # ★ 2026-08-15: 수동매도가 감지만 되고 DB에 기록되지 않던 문제 수정
            #   (사용자 지적 — "모든 거래가 우리 디비에 기록되어야 의미있는
            #   통계가 만들어질거야"). 실계좌 기간별손익 API(정확한 매도가/
            #   실현손익)를 조회해 save_sell_trade()로 기록한다. 이 함수는
            #   매수 기록이 없어도(수동매수) INSERT로 처리하는 로직이 이미
            #   있었는데, 이 감지 루프가 애초에 호출을 안 하고 있었음.
            _prows = {}
            if manual_sold_codes:
                today_ymd = datetime.datetime.now().strftime("%Y%m%d")
                _pdata = self.api.get_period_trade_profit(today_ymd, today_ymd)
                _prows = {r["pdno"]: r for r in _pdata.get("trades", [])}
            for code in manual_sold_codes:
                self.sold_today[code] = now_hms()
                print(f"   🔍 수동매도 감지: {code} → sold_today 추가")
                pos = self.positions.get(code, {})
                row = _prows.get(code)
                if row and int(row.get("sll_qty", 0) or 0) > 0:
                    sell_price = float(row.get("sll_pric", 0) or 0)
                    sell_qty   = int(row.get("sll_qty", 0) or 0) or pos.get("qty", 0)
                else:
                    # 기간별손익 API에 아직 반영 안 됨 — 최근 시세로 추정 기록
                    mdata = self.api.get_market_data(code)
                    sell_price = float(mdata.get("stck_prpr", 0)) if mdata else pos.get("entry_price", 0)
                    sell_qty   = pos.get("qty", 0)
                save_sell_trade(
                    code=code, sell_price=sell_price, reason="수동매도",
                    entry_price=pos.get("entry_price", 0), qty=sell_qty,
                    buy_time=pos.get("buy_time", ""),
                    stock_name=pos.get("name", code), grade=pos.get("grade", "실계좌"),
                    stage=pos.get("stage", 0),
                )
                if _master_remove:
                    _master_remove("sbo2", code)

            # ── 실계좌 기준으로 포지션 갱신 ───────────────────
            # 기존 포지션 메타(손절/목표/등급) 보존하면서 수량/평단 갱신
            # ★ 매수 직후 BUY_SYNC_GUARD_SEC 동안은 qty 동기화 스킵
            #   (한투 체결반영 지연으로 잘못된 qty가 들어와 50% 익절 수량
            #   계산이 망가지는 레이스컨디션 방지 — 2026-06-29)
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
                        old_entry = existing["entry_price"]
                        existing["qty"]         = rdata["qty"]
                        existing["entry_price"] = rdata["entry_price"]
                        # ★ 2026-08-07: 평단가가 바뀌면(추가매수) 손절/목표도
                        #   같은 비율(-10%/+15%)로 재계산 — 기존엔 최초 진입가
                        #   기준으로 고정돼서, 평단이 바뀌어도 손절/목표가 그대로
                        #   남아 원래 의도한 리스크 비율에서 벗어나고 있었음
                        #   (사용자 지적 — 삼성전자 20만원 매수 후 18만원 추가매수
                        #   사례). "실계좌"(수동매수 추적) 포지션에만 적용 — sbo2
                        #   자체 신호 매수는 ATR 기반 별도 로직(_check_sell 단계별
                        #   피라미딩)이라 건드리지 않음.
                        new_entry = existing["entry_price"]
                        if (existing.get("grade") == "실계좌"
                                and abs(new_entry - old_entry) > 1):
                            existing["stop_price"] = round(new_entry * 0.90, 0)
                            existing["tgt_price"]  = round(new_entry * 1.15, 0)
                            print(f"   🔄 {existing.get('name', code)}({code}) 평단 변경 "
                                  f"({old_entry:,.0f}→{new_entry:,.0f}) — 손절/목표 재계산 "
                                  f"(손절:{existing['stop_price']:,.0f} "
                                  f"목표:{existing['tgt_price']:,.0f})")
                    # 종목명이 코드 그대로면 DB에서 다시 조회
                    cur_name = existing.get("name", code)
                    if cur_name == code:
                        cur_name = get_stock_name(code)
                    existing["name"] = cur_name
                    updated[code] = existing
                else:
                    # ★ 2026-07-07: 오늘 이미 매도한 종목이면 재입양하지 않음 —
                    #   매도 주문 체결 직후 KIS 잔고API가 T+1/T+2 정산 전이라
                    #   아직 보유수량을 남겨두는 경우가 있어, 방금 판 종목을
                    #   "신규 종목"으로 오인해 재입양하고 master_positions에도
                    #   그대로 반영되는 사고가 있었음(다음 루프에서 잔고가
                    #   정산되며 self.positions에서는 다시 사라졌지만, 그 사이
                    #   master_positions엔 유령 행이 남음 — 한화엔진 사례,
                    #   09:00:36 매도 → 09:01:06 오인 재입양 → 09:01:37 재소실).
                    if code in self.sold_today:
                        print(f"   ⏭️ {rdata.get('name', code)}({code}) "
                              f"오늘 이미 매도 — 정산 지연으로 보이는 잔고, 재입양 스킵")
                        continue

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

            # ★ 2026-08-11 추가 — 위 루프는 new_pos(실계좌 조회 결과)에 있는
            #   코드만 처리하므로, 매수 직후 정산 지연으로 실계좌 조회에
            #   아직 안 잡힌 종목은 new_pos에 없다는 이유만으로 이 루프에서
            #   통째로 누락되어 self.positions에서 조용히 사라졌음. 위쪽
            #   "수동매도 감지" 루프는 이 정산지연 가드가 있어 sold_today엔
            #   안 들어갔지만, 이 재구성 자체는 가드 없이 new_pos 기준으로만
            #   덮어써서 결과적으로 포지션이 사라지는 건 똑같았음 — 그 다음
            #   루프에서 "보유 안 함"으로 보여 같은 종목을 또 매수하는 사고로
            #   이어짐(하나금융지주 실사례: 09:27:23 매수 → 33초 뒤 실계좌
            #   미반영으로 사라짐 → 09:27:54 재매수, 총 2배 매수됨).
            #   가드 기간 내에는 new_pos에 없어도 메모리 포지션을 유지한다.
            for code, pos in self.positions.items():
                if code in updated or code in self.sold_today:
                    continue
                guard_until = self._buy_sync_guard.get(code, 0) + BUY_SYNC_GUARD_SEC
                if now_ts < guard_until:
                    updated[code] = pos
                    print(f"   🛡️ {pos.get('name', code)}({code}) 매수직후 동기화 보호 중 "
                          f"— 실계좌 미반영이지만 포지션 유지(재매수 방지)")

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

                # ── 휴장일 (★ 2026-07-17 추가 — sbot과 동일 패턴) ──
                #   기존엔 sbo2가 주말만 체크하고 공휴일 체크가 아예 없어서,
                #   제헌절 같은 공휴일에도 정상 개장으로 착각하고 하루종일
                #   후보갱신/매수매도 체크를 계속 돌렸음(전일 마감 스냅샷을
                #   그대로 실시간 데이터로 오인). sbot처럼 하루 1회만 조회.
                # ★ 2026-08-17: is_market_open()이 None(API 실패/판단불가)이면
                #   그날 캐시하지 않고 다음 루프 재시도 — sbot과 동일 사유
                #   (08-17 광복절 대체공휴일에 sbot에서 실제로 발생한 사고,
                #   sbo2도 같은 구조라 예방 차원에서 동일 적용)
                today = today_str()
                if self._holiday_checked != today:
                    _open = self.api.is_market_open()
                    if _open is None:
                        print(f"⚠️ [{now_hms()}] 휴장일 판단 실패 — 다음 루프 재시도")
                    else:
                        self._is_holiday      = not _open
                        self._holiday_checked = today
                if self._is_holiday:
                    print(f"🎌 [{now_hms()}] 휴장일 — 대기 중...")
                    time.sleep(300)
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

                # 후보 갱신 (하루 1회)
                self._refresh_candidates()
                # ★ 모멘텀만 매 루프 별도 갱신 — 사유는 _refresh_momentum_candidates() 참고
                self._refresh_momentum_candidates()

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
                    # ★ 2026-07-02: master_positions 현재가 갱신 (sbot과 동일 패턴).
                    #   기존엔 신규 매수 시점에만 upsert하고 재시작으로 실계좌에서
                    #   그대로 복원(adopt)된 보유종목은 한 번도 master_db에 반영된
                    #   적이 없어서, sbo2 현재 보유종목이 master_positions에서
                    #   통째로 빠져있었음(sbot이 sbo2 교차보유 체크에 master_db를
                    #   쓰면 항상 빈 결과를 받는 문제로 이어짐). 매 루프 upsert로
                    #   실계좌 기준과 항상 동기화되도록 함.
                    if _master_upsert:
                        try:
                            _master_upsert(
                                bot_type='sbo2', code=code,
                                stock_name=pos.get('name', code),
                                entry_price=pos['entry_price'],
                                current_price=curr,
                                qty=pos.get('qty', 0),
                                stage=pos.get('stage', 0),
                            )
                        except Exception:
                            pass

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
