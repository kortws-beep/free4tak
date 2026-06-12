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

# ── 의존 모듈 ─────────────────────────────────────────────────
from kis_api       import KisAPI
from swing_master  import get_master_report, _get_catalyst_stocks, _extract_names_from_report
from swing_analyzer import get_swing_picks
from trend_analyzer import get_trend_picks

try:
    import sys as _sys
    _STOCK_BOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for _d in ["core", "interface", "bots", ""]:
        _p = os.path.join(_STOCK_BOT, _d)
        if os.path.exists(_p) and _p not in _sys.path:
            _sys.path.insert(0, _p)
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
MAX_POSITIONS    = 4            # 최대 보유 종목
A_GRADE_RATIO    = 0.7          # A급 상위 70%만 매수
LOOP_SLEEP       = 30           # 루프 간격 (초)
BUY_START_TIME   = "0910"       # 매수 시작
BUY_END_TIME     = "1520"       # 매수 마감
SELL_START_TIME  = "0800"       # 프리장부터 매도 체크
SELL_END_TIME    = "2000"       # 애프터장까지 매도 체크
CANDIDATE_REFRESH= 86400        # 후보 갱신 주기 (하루 1회)

MIN_PRICE        = 3_000        # 최소 주가
MAX_PRICE        = 3_000_000    # 최대 주가

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
            grade       TEXT    NOT NULL,   -- S / A
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
            grade        TEXT    DEFAULT '',   -- S / A
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
                   score: int, stop: float, tgt: float, rr: float):
    """매수 이력 저장"""
    try:
        conn = sqlite3.connect(SBO2_DB_PATH, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            INSERT INTO sbo2_trades
                (code, stock_name, grade, vcp_hit, trend_hit, catalyst_hit,
                 buy_price, buy_time, buy_amount, qty, score,
                 stop_price, tgt_price, rr_ratio)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            code, name, grade, int(vcp), int(trend), int(catalyst),
            buy_price, now_hms(), amount, qty, score,
            stop, tgt, rr
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"⚠️ 매수 저장 오류: {e}")


def save_sell_trade(code: str, sell_price: float, reason: str,
                    entry_price: float, qty: int, buy_time: str):
    """매도 이력 업데이트"""
    try:
        profit_rate = (sell_price - entry_price) / entry_price * 100 if entry_price else 0
        profit_krw  = (sell_price - entry_price) * qty

        buy_date = buy_time[:10] if buy_time else today_str()
        try:
            bd = datetime.datetime.strptime(buy_date, "%Y-%m-%d").date()
            hold_days = (datetime.date.today() - bd).days
        except Exception:
            hold_days = 0

        conn = sqlite3.connect(SBO2_DB_PATH, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            UPDATE sbo2_trades
            SET sell_price  = ?,
                sell_time   = ?,
                sell_reason = ?,
                profit_rate = ?,
                profit_krw  = ?,
                hold_days   = ?
            WHERE code = ? AND sell_time IS NULL
            ORDER BY id DESC LIMIT 1
        """, (
            sell_price, now_hms(), reason,
            round(profit_rate, 2), round(profit_krw, 0),
            hold_days, code
        ))
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

def _save_cand_date(date: str):
    """후보 갱신 날짜 상태파일에 저장"""
    st = _read_state()
    st["cand_date"] = date
    _write_state(st)


# ============================================================
# 점수 비례 매수금액 계산
# ============================================================
def calc_buy_amount(grade: str, psbl_cash: int) -> int:
    """
    등급별 매수금액:
    - S급 → 150만원 전액 (100%)
    - A급 → 105만원 (150만 × 70%)
    - 주문가능금액 초과 시 조정
    """
    if grade == 'S':
        amount = BASE_BUY_AMT              # 150만원
    else:                                   # A급
        amount = int(BASE_BUY_AMT * 0.7)   # 105만원

    amount = min(amount, psbl_cash)
    return amount


# ============================================================
# swing_master 결과 파싱 (후보 상세 추출)
# ============================================================
def get_candidates() -> list:
    """
    swing_master S/A급 종목 + 상세 데이터 추출
    반환: [{"name", "grade", "score", "vcp", "trend", "catalyst",
             "curr", "stop", "tgt", "rr", "code"}, ...]
    """
    from swing_analyzer import get_swing_picks
    from trend_analyzer import get_trend_picks

    catalyst_set = _get_catalyst_stocks()
    swing_report = get_swing_picks(top_n=20)
    trend_report = get_trend_picks(top_n=20)
    swing_names  = _extract_names_from_report(swing_report)
    trend_names  = _extract_names_from_report(trend_report)

    s_grade = swing_names & trend_names & catalyst_set
    a_grade = (
        ((swing_names & trend_names)  - catalyst_set) |
        ((swing_names & catalyst_set) - trend_names)  |
        ((trend_names & catalyst_set) - swing_names)
    )

    # swing_analyzer 상세 데이터 파싱 (ATR 손절/목표)
    detail_map = {}
    for line in swing_report.splitlines():
        import re
        m = re.search(r'\*?\*?\d+위:\s*\*?\*?(.+?)\*?\*?\s*\(스코어:\s*(\d+)', line)
        if m:
            cur_name  = m.group(1).strip()
            cur_score = int(m.group(2))
            detail_map[cur_name] = {"score": cur_score, "stop": 0, "tgt": 0, "rr": 0, "curr": 0}
        if '현재가' in line:
            try:
                price = int(re.sub(r'[^\d]', '', line.split(':')[1].split('원')[0]))
                if cur_name: detail_map[cur_name]["curr"] = price
            except Exception: pass
        if '목표가' in line:
            try:
                price = int(re.sub(r'[^\d]', '', line.split(':')[1].split('원')[0]))
                if cur_name: detail_map[cur_name]["tgt"] = price
            except Exception: pass
        if '손절가' in line:
            try:
                price = int(re.sub(r'[^\d]', '', line.split(':')[1].split('원')[0]))
                if cur_name: detail_map[cur_name]["stop"] = price
            except Exception: pass
        if 'R:R' in line:
            try:
                rr = float(re.search(r'1\s*:\s*([\d.]+)', line).group(1))
                if cur_name: detail_map[cur_name]["rr"] = rr
            except Exception: pass

    # trend_analyzer 상세도 파싱
    cur_name = None
    for line in trend_report.splitlines():
        import re
        m = re.search(r'\*?\*?\d+위:\s*\*?\*?(.+?)\*?\*?\s*\(스코어:\s*(\d+)', line)
        if m:
            cur_name  = m.group(1).strip()
            cur_score = int(m.group(2))
            if cur_name not in detail_map:
                detail_map[cur_name] = {"score": cur_score, "stop": 0, "tgt": 0, "rr": 0, "curr": 0}
        if cur_name and '현재가' in line and detail_map.get(cur_name, {}).get("curr") == 0:
            try:
                price = int(re.sub(r'[^\d]', '', line.split(':')[1].split('원')[0]))
                detail_map[cur_name]["curr"] = price
            except Exception: pass
        if cur_name and '목표가' in line and detail_map.get(cur_name, {}).get("tgt") == 0:
            try:
                price = int(re.sub(r'[^\d]', '', line.split(':')[1].split('원')[0]))
                detail_map[cur_name]["tgt"] = price
            except Exception: pass
        if cur_name and '손절가' in line and detail_map.get(cur_name, {}).get("stop") == 0:
            try:
                price = int(re.sub(r'[^\d]', '', line.split(':')[1].split('원')[0]))
                detail_map[cur_name]["stop"] = price
            except Exception: pass

    candidates = []

    # S급
    for name in s_grade:
        d = detail_map.get(name, {})
        candidates.append({
            "name":     name,
            "grade":    "S",
            "score":    d.get("score", 0),
            "vcp":      name in swing_names,
            "trend":    name in trend_names,
            "catalyst": name in catalyst_set,
            "curr":     d.get("curr", 0),
            "stop":     d.get("stop", 0),
            "tgt":      d.get("tgt", 0),
            "rr":       d.get("rr", 0),
        })

    # A급 — 점수 상위 70%만
    a_list = []
    for name in a_grade:
        d = detail_map.get(name, {})
        a_list.append({
            "name":     name,
            "grade":    "A",
            "score":    d.get("score", 0),
            "vcp":      name in swing_names,
            "trend":    name in trend_names,
            "catalyst": name in catalyst_set,
            "curr":     d.get("curr", 0),
            "stop":     d.get("stop", 0),
            "tgt":      d.get("tgt", 0),
            "rr":       d.get("rr", 0),
        })
    a_list.sort(key=lambda x: x["score"], reverse=True)
    cutoff = max(1, int(len(a_list) * A_GRADE_RATIO))
    candidates += a_list[:cutoff]

    return candidates


# ============================================================
# 종목코드 조회 (이름 → 코드)
# ============================================================
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
        self.candidates = []       # 현재 후보 리스트
        self._cand_ts   = 0        # 후보 마지막 갱신 시각
        self._cand_date = ""       # 후보 마지막 갱신 날짜

        # 상태 복원
        st = _read_state()
        self.positions  = st.get("positions", {})
        self.sold_today = st.get("sold_today", {})
        self._cand_date = st.get("cand_date", "")   # 후보 갱신 날짜 복원
        if st.get("sold_today_date") != today_str():
            self.sold_today = {}

        init_sbo2_db()

        # 실계좌 포지션 동기화
        self._sync_real_positions()

        print("✅ [sbo2] 초기화 완료")
        print(f"   보유 포지션: {list(self.positions.keys())}")

    def _save_state(self):
        _write_state({
            "positions":       self.positions,
            "sold_today":      self.sold_today,
            "sold_today_date": today_str(),
            "cand_date":       getattr(self, "_cand_date", ""),
        })

    def _name(self, code: str) -> str:
        for pos in self.positions.values():
            if pos.get("code") == code:
                return pos.get("name", code)
        return code

    # ── 후보 갱신 ─────────────────────────────────────────────
    def _refresh_candidates(self):
        now = time.time()
        # 하루 1회 갱신 (날짜 바뀌거나 처음 실행시)
        today = today_str()
        if hasattr(self, '_cand_date') and self._cand_date == today and self.candidates:
            return
        # 장 시작 전(~08:50)에만 갱신 (장중 재시작 시엔 이전 캐시 유지)
        now_t = now_hhmm()
        if hasattr(self, '_cand_date') and self._cand_date == today and now_t > "0850":
            return
        print(f"\n🔄 [sbo2] 후보 갱신 중...")
        try:
            self.candidates = get_candidates()
            self._cand_ts   = now
            self._cand_date = today_str()
            _save_cand_date(self._cand_date)
            print(f"   S급: {sum(1 for c in self.candidates if c['grade']=='S')}개 "
                  f"A급: {sum(1 for c in self.candidates if c['grade']=='A')}개")

            # 후보 DB 저장
            for c in self.candidates:
                save_candidate(
                    name=c["name"], grade=c["grade"], score=c["score"],
                    vcp=c["vcp"], trend=c["trend"], catalyst=c["catalyst"],
                    curr=c["curr"], stop=c["stop"], tgt=c["tgt"], rr=c["rr"],
                )
        except Exception as e:
            print(f"❌ 후보 갱신 오류: {e}")

    # ── 매수 체크 ─────────────────────────────────────────────
    def _check_buy(self):
        now_t = now_hhmm()
        if not (BUY_START_TIME <= now_t <= BUY_END_TIME):
            return

        slots = MAX_POSITIONS - len(self.positions)
        if slots <= 0:
            print("📦 [sbo2] 포지션 FULL")
            return

        # 주문가능금액 조회 — 보유종목 기준 (진짜 주문가능금액)
        psbl_cash = 0
        for _code in list(self.positions.keys()):
            psbl_cash = self.api.get_psbl_order_cash(_code)
            if psbl_cash > 0:
                break
        # 보유종목 없으면 후보 종목으로 조회
        if psbl_cash <= 0 and self.candidates:
            for cand in self.candidates:
                _code = get_stock_code(cand["name"])
                if _code:
                    psbl_cash = self.api.get_psbl_order_cash(_code)
                    if psbl_cash > 0:
                        break
        print(f"   💰 주문가능: {psbl_cash:,}원")
        if psbl_cash <= 0:
            print("⚠️ [sbo2] 주문가능금액 없음 — 매수 스킵")
            return

        for cand in self.candidates:
            if slots <= 0:
                break

            name = cand["name"]
            code = get_stock_code(name)
            if not code:
                print(f"⚠️ 코드 조회 실패: {name}")
                continue

            if code in self.positions:
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

            # 매수금액 계산 — 예수금 부족시 있는 만큼 매수
            amount = calc_buy_amount(cand["grade"], psbl_cash)

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
            ok, orgno, odno = self.api.buy(code, curr_price, amount, {})
            if not ok:
                continue

            qty = max(int(amount / curr_price), 1)

            # 포지션 등록
            self.positions[code] = {
                "code":       code,
                "name":       name,
                "grade":      cand["grade"],
                "entry_price": curr_price,
                "qty":        qty,
                "buy_time":   today_str(),
                "stop_price": cand["stop"] or curr_price * 0.93,
                "tgt_price":  cand["tgt"]  or curr_price * 1.15,
                "score":      cand["score"],
                "vcp":        cand["vcp"],
                "trend":      cand["trend"],
                "catalyst":   cand["catalyst"],
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
    def _check_sell(self):
        now_t = now_hhmm()
        if not (SELL_START_TIME <= now_t <= SELL_END_TIME):
            return

        for code, pos in list(self.positions.items()):
            mdata = self.api.get_market_data(code)
            if not mdata:
                continue

            curr  = float(mdata.get("stck_prpr", 0))
            entry = pos["entry_price"]
            qty   = pos["qty"]
            stop  = pos["stop_price"]
            tgt   = pos["tgt_price"]
            name  = pos.get("name", code)

            if curr <= 0:
                continue

            rate = (curr - entry) / entry * 100
            reason = None

            # 손절 체크
            if curr <= stop:
                reason = f"손절 {rate:+.1f}%"

            # 목표가 체크
            elif curr >= tgt:
                reason = f"목표달성 {rate:+.1f}%"

            # 장 마감 전 청산 (15:20)
            elif now_t >= "1520":
                reason = f"장마감청산 {rate:+.1f}%"

            if reason:
                ok = self.api.sell(code, qty)
                if not ok:
                    continue

                # DB 저장
                save_sell_trade(
                    code=code, sell_price=curr, reason=reason,
                    entry_price=entry, qty=qty, buy_time=pos.get("buy_time", "")
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
    def _sync_real_positions(self):
        """
        실계좌 잔고 기준 포지션 동기화 (sbot 방식)
        매 루프 실행 — 실계좌가 진실
        """
        try:
            new_pos = self.api.get_current_positions()
            if not new_pos and self.positions:
                print("⚠️ 실계좌 잔고 빈값 — 동기화 스킵 (캐시 유지)")
                return

            # ── 수동매도/손절 감지 ─────────────────────────────
            for code in list(self.positions.keys()):
                if code not in new_pos and code not in self.sold_today:
                    self.sold_today[code] = now_hms()
                    print(f"   🔍 수동매도 감지: {code} → sold_today 추가")
                    if _master_remove:
                        _master_remove("sbo2", code)

            # ── 실계좌 기준으로 포지션 갱신 ───────────────────
            # 기존 포지션 메타(손절/목표/등급) 보존하면서 수량/평단 갱신
            updated = {}
            for code, rdata in new_pos.items():
                if code in self.positions:
                    # 기존 포지션 메타 유지 + 수량/평단 갱신
                    existing = self.positions[code]
                    existing["qty"]         = rdata["qty"]
                    existing["entry_price"] = rdata["entry_price"]
                    existing["name"]        = rdata.get("name", existing.get("name", code))
                    updated[code] = existing
                else:
                    # 신규 (수동매수 또는 새로 잡힌 종목)
                    entry = rdata["entry_price"]
                    updated[code] = {
                        "code":        code,
                        "name":        rdata.get("name", code),
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
                    mdata = self.api.get_market_data(code)
                    curr  = float(mdata.get("stck_prpr", 0)) if mdata else pos["entry_price"]
                    rate  = (curr - pos["entry_price"]) / pos["entry_price"] * 100
                    print(f"   💼 {pos.get('name', code)}({pos.get('grade','')}) "
                          f"{rate:+.1f}% | 손절:{pos['stop_price']:,.0f} 목표:{pos['tgt_price']:,.0f}")

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
