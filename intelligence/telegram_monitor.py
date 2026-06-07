"""
telegram_monitor.py — 텔레그램 채널 실시간 모니터링 v2
================================================================
[v2 변경사항]
  1. KIND 채널 공시 → 종목코드 추출 → 종목 직접 가산점
  2. consensus.py 연동 → 리포트 가산점 합산
  3. stock_event_bonus 테이블 추가 (종목코드 단위 저장)
  4. 악재 공시 → 마이너스 가산점 (매수 억제)
  5. nbot/sbot 구분 적용

[가산점 흐름]
  KIND 메시지 수신
    → 종목코드 추출 (정규식)
    → 공시 타입 판별 → 기본 score
    → consensus.py 조회 → 리포트 가산점 합산
    → stock_event_bonus DB 저장 (2시간 유효)
    → nbot/sbot 분석 시 get_stock_event_bonus(code) 조회
================================================================
"""
import os
import sys
import json
import sqlite3
import asyncio
import datetime
import re

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ["core", "intelligence", ""]:
    _p = os.path.join(_BASE, _d)
    if _p not in sys.path:
        _sys_path_insert = True
        sys.path.insert(0, _p)

from dotenv import load_dotenv
load_dotenv(os.path.join(_BASE, ".env"))

from telethon import TelegramClient, events

# ── consensus 연동 ─────────────────────────────────────
try:
    from consensus import get_consensus
    _CONSENSUS_OK = True
except ImportError:
    _CONSENSUS_OK = False
    print("⚠️ consensus.py 없음 → 리포트 가산점 비활성")

# ── 설정 ─────────────────────────────────────────────
API_ID   = int(os.getenv("TELEGRAM_API_ID", "34756144"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "d7c4e05b6ac021c5bfe2e89db29938fc")
SESSION  = os.path.join(_BASE, "intelligence", "telegram_session")

CHANNELS = [
    "hankyung_fin",   # 한국경제 금융
    "stocknewskorea", # 주식뉴스
    "kind_krx",       # 공시(KIND)
    "AllStockNews",   # 전체 주식 뉴스 (상한가/이슈)
]

# ── 공시 타입별 가산점 ────────────────────────────────
# (점수, 적용봇: 'nbot'/'sbot'/'both', 유효시간(시))
DISCLOSURE_SCORES = {
    # 호재
    "수주":             ( 20, "nbot", 4),
    "계약체결":         ( 18, "nbot", 4),
    "공급계약":         ( 18, "nbot", 4),
    "실적":             ( 15, "both", 6),
    "흑자전환":         ( 20, "both", 6),
    "어닝서프라이즈":   ( 20, "both", 4),
    "영업이익":         ( 12, "both", 6),
    "자기주식":         ( 10, "sbot", 8),   # 스윙 유리 (중장기 호재)
    "배당":             (  8, "sbot", 8),
    "기술수출":         ( 20, "nbot", 4),
    "FDA":              ( 25, "nbot", 4),
    "임상":             ( 18, "nbot", 4),
    "합병":             ( 12, "sbot", 8),
    "인수":             ( 10, "sbot", 8),
    "신규상장":         (  8, "nbot", 2),
    # 악재
    "유상증자":         (-15, "both", 8),
    "횡령":             (-25, "both", 24),
    "불성실공시":       (-20, "both", 24),
    "조사":             (-15, "both", 8),
    "적자":             (-12, "both", 6),
    "손실":             (-10, "both", 6),
    "영업정지":         (-20, "both", 24),
}

# ── 빅 이벤트 키워드 (기존 테마 가산점 유지) ──────────
EVENT_KEYWORDS = {
    "젠슨황":       {"themes": ["AI", "반도체", "전력", "서버"], "score": 20},
    "엔비디아":     {"themes": ["AI", "반도체", "HBM"], "score": 15},
    "AI":           {"themes": ["AI", "반도체", "클라우드"], "score": 10},
    "HBM":          {"themes": ["반도체", "HBM"], "score": 15},
    "트럼프":       {"themes": ["방산", "에너지", "철강"], "score": 15},
    "관세":         {"themes": ["수출", "자동차", "철강"], "score": 12},
    "금리인하":     {"themes": ["성장주", "바이오", "부동산"], "score": 15},
    "금리동결":     {"themes": ["성장주", "바이오"], "score": 8},
    "흑자전환":     {"themes": ["실적개선"], "score": 20},
    "어닝서프라이즈": {"themes": ["실적개선"], "score": 18},
    "실적개선":     {"themes": ["실적개선"], "score": 15},
    "영업이익":     {"themes": ["실적개선"], "score": 10},
    "FDA승인":      {"themes": ["바이오", "제약"], "score": 25},
    "임상성공":     {"themes": ["바이오", "제약"], "score": 20},
    "빅파마":       {"themes": ["바이오", "제약"], "score": 18},
    "기술수출":     {"themes": ["바이오", "제약"], "score": 20},
    "계약체결":     {"themes": ["바이오", "제약"], "score": 15},
    "수주":         {"themes": ["방산", "조선", "건설"], "score": 15},
    "상장":         {"themes": ["IPO"], "score": 10},
    "자사주":       {"themes": ["배당주"], "score": 10},
}

# ── DB ───────────────────────────────────────────────
DB_PATH = os.path.join(_BASE, "intelligence", "telegram_events.db")

# 종목코드 추출 정규식 (6자리 숫자 괄호 안)
CODE_PATTERN = re.compile(r'[\(（](\d{6})[\)）]')
# 종목명 추출 (코드 앞의 한글)
NAME_PATTERN = re.compile(r'([가-힣A-Za-z0-9&·\s]{2,20}?)[\(（]\d{6}[\)）]')


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")

    # 기존 테이블
    conn.execute("""
        CREATE TABLE IF NOT EXISTS telegram_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            channel     TEXT,
            message     TEXT,
            keywords    TEXT,
            themes      TEXT,
            score       INTEGER,
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS event_bonus (
            theme       TEXT PRIMARY KEY,
            bonus_score INTEGER,
            reason      TEXT,
            expires_at  TEXT,
            updated_at  TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    # ★ 신규: 종목코드 단위 이벤트 가산점
    conn.execute("""
        CREATE TABLE IF NOT EXISTS stock_event_bonus (
            code        TEXT PRIMARY KEY,
            stock_name  TEXT DEFAULT '',
            bonus_nbot  INTEGER DEFAULT 0,   -- nbot 가산점
            bonus_sbot  INTEGER DEFAULT 0,   -- sbot 가산점
            reason      TEXT DEFAULT '',
            disclosure  TEXT DEFAULT '',     -- 공시 내용 요약
            expires_at  TEXT,
            updated_at  TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_stock_event_expires
        ON stock_event_bonus(expires_at)
    """)
    conn.commit()
    conn.close()


def save_event(channel, message, keywords, themes, score):
    """기존 테마 이벤트 저장 (하위 호환)"""
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        INSERT INTO telegram_events (channel, message, keywords, themes, score)
        VALUES (?, ?, ?, ?, ?)
    """, (channel, message[:500], json.dumps(keywords), json.dumps(themes), score))

    expires = (datetime.datetime.now() + datetime.timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S")
    for theme in themes:
        conn.execute("""
            INSERT INTO event_bonus (theme, bonus_score, reason, expires_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(theme) DO UPDATE SET
                bonus_score = MAX(bonus_score, excluded.bonus_score),
                reason      = excluded.reason,
                expires_at  = excluded.expires_at,
                updated_at  = datetime('now','localtime')
        """, (theme, score, f"텔레그램: {','.join(keywords)}", expires))

    conn.commit()
    conn.close()


def save_stock_event(code: str, stock_name: str, bonus_nbot: int,
                     bonus_sbot: int, reason: str, disclosure: str,
                     expire_hours: int = 4):
    """★ 신규: 종목코드 단위 이벤트 가산점 저장"""
    expires = (datetime.datetime.now()
               + datetime.timedelta(hours=expire_hours)
               ).strftime("%Y-%m-%d %H:%M:%S")
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            INSERT INTO stock_event_bonus
                (code, stock_name, bonus_nbot, bonus_sbot, reason, disclosure, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(code) DO UPDATE SET
                stock_name = excluded.stock_name,
                bonus_nbot = CASE WHEN ABS(excluded.bonus_nbot) > ABS(bonus_nbot)
                                  THEN excluded.bonus_nbot ELSE bonus_nbot END,
                bonus_sbot = CASE WHEN ABS(excluded.bonus_sbot) > ABS(bonus_sbot)
                                  THEN excluded.bonus_sbot ELSE bonus_sbot END,
                reason     = excluded.reason,
                disclosure = excluded.disclosure,
                expires_at = excluded.expires_at,
                updated_at = datetime('now','localtime')
        """, (code, stock_name, bonus_nbot, bonus_sbot, reason, disclosure, expires))
        conn.commit()
        conn.close()
        sign = "+" if bonus_nbot >= 0 else ""
        print(f"  💾 종목이벤트 저장: {code}({stock_name}) | "
              f"nbot:{sign}{bonus_nbot} sbot:{sign}{bonus_sbot} | "
              f"{reason} | {expire_hours}h")
    except Exception as e:
        print(f"⚠️ stock_event 저장 오류: {e}")


def get_stock_event_bonus(code: str, bot_type: str = "nbot") -> tuple:
    """
    ★ 신규: 종목코드 단위 이벤트 가산점 조회.
    nbot/sbot에서 호출.
    반환: (bonus_score, reason)
    """
    try:
        conn = sqlite3.connect(DB_PATH, timeout=3)
        conn.execute("PRAGMA journal_mode=WAL")
        row = conn.execute("""
            SELECT bonus_nbot, bonus_sbot, reason, disclosure
            FROM stock_event_bonus
            WHERE code = ?
              AND expires_at > datetime('now','localtime')
        """, (code,)).fetchone()
        conn.close()
        if not row:
            return 0, ""
        bonus_nbot, bonus_sbot, reason, disclosure = row
        bonus = bonus_nbot if bot_type == "nbot" else bonus_sbot
        if bonus == 0:
            return 0, ""
        sign = "+" if bonus >= 0 else ""
        label = "공시호재" if bonus > 0 else "공시악재"
        return bonus, f"{label}[{disclosure[:20]}]({sign}{bonus})"
    except Exception:
        return 0, ""


def get_event_bonus(theme: str) -> int:
    """기존 테마 이벤트 가산점 조회 (하위 호환)"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=3)
        row = conn.execute("""
            SELECT bonus_score FROM event_bonus
            WHERE theme = ?
              AND expires_at > datetime('now','localtime')
        """, (theme,)).fetchone()
        conn.close()
        return row[0] if row else 0
    except Exception:
        return 0


def analyze_message(text: str) -> tuple:
    """기존 빅 이벤트 키워드 감지 (하위 호환)"""
    found_keywords = []
    found_themes   = set()
    max_score      = 0
    for kw, info in EVENT_KEYWORDS.items():
        if kw in text:
            found_keywords.append(kw)
            found_themes.update(info["themes"])
            max_score = max(max_score, info["score"])
    return found_keywords, list(found_themes), max_score


def extract_disclosure_info(text: str) -> tuple:
    """
    ★ 신규: KIND 공시 메시지에서 종목코드 + 공시 타입 추출.
    반환: (code, stock_name, disc_score, bot_type, expire_h, disc_label)
    """
    # 종목코드 추출
    code_match = CODE_PATTERN.search(text)
    if not code_match:
        return None, "", 0, "both", 4, ""

    code = code_match.group(1)

    # 종목명 추출
    name_match = NAME_PATTERN.search(text)
    stock_name = name_match.group(1).strip() if name_match else ""

    # 공시 타입 판별 (우선순위 높은 것 먼저)
    best_score  = 0
    best_bot    = "both"
    best_expire = 4
    best_label  = ""

    for keyword, (score, bot, expire_h) in DISCLOSURE_SCORES.items():
        if keyword in text:
            if abs(score) > abs(best_score):
                best_score  = score
                best_bot    = bot
                best_expire = expire_h
                best_label  = keyword

    return code, stock_name, best_score, best_bot, best_expire, best_label


async def process_kind_message(text: str, channel: str):
    """
    ★ 신규: KIND 채널 공시 처리.
    종목코드 추출 → 공시 타입 판별 → consensus 조회 → DB 저장
    """
    code, stock_name, disc_score, bot_type, expire_h, disc_label = \
        extract_disclosure_info(text)

    if not code:
        return  # 종목코드 없는 메시지 스킵

    if disc_score == 0:
        return  # 관련 공시 키워드 없음

    now = datetime.datetime.now().strftime("%H:%M:%S")
    sign = "+" if disc_score >= 0 else ""
    emoji = "🟢" if disc_score > 0 else "🔴"

    print(f"\n{emoji} [{now}] KIND 공시 감지!")
    print(f"   종목: {code}({stock_name})")
    print(f"   공시: {disc_label} | {sign}{disc_score}점 | 봇:{bot_type}")
    print(f"   내용: {text[:80]}")

    # consensus 가산점 (호재 공시만, 현재가 없으므로 리포트 수만 활용)
    consensus_bonus = 0
    consensus_reason = ""
    if disc_score > 0 and _CONSENSUS_OK:
        try:
            c_data = get_consensus(code, current_price=0, days=7)
            consensus_bonus  = c_data.get("bonus", 0)
            consensus_reason = c_data.get("reason", "")
        except Exception as e:
            print(f"  ⚠️ 컨센서스 조회 오류: {e}")

    total_score = disc_score + consensus_bonus
    reason_parts = [f"공시:{disc_label}({sign}{disc_score})"]
    if consensus_reason:
        reason_parts.append(f"컨센:{consensus_reason}")
    reason = " | ".join(reason_parts)

    # 봇 타입별 점수 분배
    bonus_nbot = total_score if bot_type in ("nbot", "both") else 0
    bonus_sbot = total_score if bot_type in ("sbot", "both") else 0

    # 점수 클램프 (-30 ~ +30)
    bonus_nbot = max(-30, min(30, bonus_nbot))
    bonus_sbot = max(-30, min(30, bonus_sbot))

    save_stock_event(
        code       = code,
        stock_name = stock_name,
        bonus_nbot = bonus_nbot,
        bonus_sbot = bonus_sbot,
        reason     = reason,
        disclosure = f"{disc_label}:{text[:40]}",
        expire_hours = expire_h,
    )

    # 디스코드 알림 (호재/악재 모두)
    try:
        from common_utils import update_state
        update_state("nbot_state.json", disclosure_event={
            "code":       code,
            "name":       stock_name,
            "disc":       disc_label,
            "score":      total_score,
            "bot_type":   bot_type,
            "text":       text[:150],
            "time":       now,
        })
    except Exception as e:
        print(f"  ⚠️ state 업데이트 오류: {e}")


# ── 텔레그램 클라이언트 ───────────────────────────────
client = TelegramClient(SESSION, API_ID, API_HASH)


async def main():
    await client.start()
    print("✅ 텔레그램 연결 완료")
    print(f"📡 모니터링 채널: {CHANNELS}")

    @client.on(events.NewMessage(chats=CHANNELS))
    async def handler(event):
        text    = event.message.message or ""
        channel = event.chat.username or str(event.chat_id)
        now     = datetime.datetime.now().strftime("%H:%M:%S")

        # ★ KIND 채널 → 종목코드 직접 매핑
        if channel == "kind_krx" or "공시" in text:
            await process_kind_message(text, channel)

        # 기존 빅 이벤트 키워드 (테마 가산점)
        keywords, themes, score = analyze_message(text)
        if keywords:
            print(f"\n🚨 [{now}] 빅 이벤트 감지!")
            print(f"   채널: {channel} | 키워드: {keywords} | +{score}점")
            print(f"   내용: {text[:100]}")
            save_event(channel, text, keywords, themes, score)
            try:
                from common_utils import update_state
                update_state("nbot_state.json", telegram_event={
                    "keywords": keywords,
                    "themes":   themes,
                    "score":    score,
                    "text":     text[:200],
                    "time":     now,
                })
            except Exception as e:
                print(f"⚠️ state 업데이트 오류: {e}")

    print("👂 메시지 대기 중...")
    await client.run_until_disconnected()


if __name__ == "__main__":
    init_db()
    print("🚀 텔레그램 모니터 v2 시작")
    asyncio.run(main())
