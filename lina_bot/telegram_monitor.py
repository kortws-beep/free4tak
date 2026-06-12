"""
telegram_monitor.py — 텔레그램 채널 실시간 모니터링
빅 이벤트 감지 → nbot 테마 가산점 연동
"""
import os
import sys
import json
import sqlite3
import asyncio
import datetime
import re

# 1. 경로 설정 변수를 가장 먼저 정의!
_BASE = os.path.dirname(os.path.abspath(__file__))

# 2. 시스템 경로 추가
for _d in ["core", "intelligence", ""]:
    _p = os.path.join(_BASE, _d)
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 3. 그 다음에 env 로드!
from dotenv import load_dotenv
load_dotenv(os.path.join(_BASE, ".env"))

from telethon import TelegramClient, events
from telethon.tl.types import PeerChannel

# ── 설정 ─────────────────────────────────────────────
API_ID   = int(os.getenv("TELEGRAM_API_ID", "34756144"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "d7c4e05b6ac021c5bfe2e89db29938fc")
SESSION  = os.path.join(_BASE, "intelligence", "telegram_session")

# 모니터링 채널
CHANNELS = [
    "hankyung_fin",   # 한국경제 금융
    "stocknewskorea", # 주식뉴스
    "kind_krx",       # 공시(KIND)
    "AllStockNews",   # 전체 주식 뉴스 (상한가/이슈)
    "stock0",         # 주식 정보
    "darthacking",    # 다크해킹
    "korea_news11",   # 한국 뉴스
]

# ── 빅 이벤트 키워드 매핑 ────────────────────────────
EVENT_KEYWORDS = {
    # AI/반도체
    "젠슨황":       {"themes": ["AI", "반도체", "전력", "서버"], "score": 20},
    "엔비디아":     {"themes": ["AI", "반도체", "HBM"], "score": 15},
    "AI":           {"themes": ["AI", "반도체", "클라우드"], "score": 10},
    "HBM":          {"themes": ["반도체", "HBM"], "score": 15},

    # 트럼프/매크로
    "트럼프":       {"themes": ["방산", "에너지", "철강"], "score": 15},
    "관세":         {"themes": ["수출", "자동차", "철강"], "score": 12},
    "금리인하":     {"themes": ["성장주", "바이오", "부동산"], "score": 15},
    "금리동결":     {"themes": ["성장주", "바이오"], "score": 8},

    # 실적/공시
    "흑자전환":     {"themes": ["실적개선"], "score": 20},
    "어닝서프라이즈": {"themes": ["실적개선"], "score": 18},
    "실적개선":     {"themes": ["실적개선"], "score": 15},
    "영업이익":     {"themes": ["실적개선"], "score": 10},

    # 제약/바이오
    "FDA승인":      {"themes": ["바이오", "제약"], "score": 25},
    "임상성공":     {"themes": ["바이오", "제약"], "score": 20},
    "빅파마":       {"themes": ["바이오", "제약"], "score": 18},
    "기술수출":     {"themes": ["바이오", "제약"], "score": 20},
    "계약체결":     {"themes": ["바이오", "제약"], "score": 15},

    # 기타
    "수주":         {"themes": ["방산", "조선", "건설"], "score": 15},
    "상장":         {"themes": ["IPO"], "score": 10},
    "자사주":       {"themes": ["배당주"], "score": 10},
}

# ── DB 초기화 ─────────────────────────────────────────
DB_PATH = os.path.join(_BASE, "intelligence", "telegram_events.db")

def init_db():
    conn = sqlite3.connect(DB_PATH)
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
    conn.commit()
    conn.close()

def save_event(channel, message, keywords, themes, score):
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        INSERT INTO telegram_events (channel, message, keywords, themes, score)
        VALUES (?, ?, ?, ?, ?)
    """, (channel, message[:500], json.dumps(keywords), json.dumps(themes), score))

    # event_bonus 갱신 (2시간 유효)
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
    print(f"💾 이벤트 저장: {channel} | {keywords} | {themes} | +{score}점")

def get_event_bonus(theme: str) -> int:
    """테마별 이벤트 가산점 조회 (만료 체크)"""
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
    """메시지에서 빅 이벤트 키워드 감지"""
    found_keywords = []
    found_themes   = set()
    max_score      = 0

    for kw, info in EVENT_KEYWORDS.items():
        if kw in text:
            found_keywords.append(kw)
            found_themes.update(info["themes"])
            max_score = max(max_score, info["score"])

    return found_keywords, list(found_themes), max_score

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

        keywords, themes, score = analyze_message(text)
        if not keywords:
            return

        print(f"\n🚨 [{now}] 빅 이벤트 감지!")
        print(f"   채널: {channel}")
        print(f"   키워드: {keywords}")
        print(f"   테마: {themes}")
        print(f"   가산점: +{score}점")
        print(f"   내용: {text[:100]}")

        save_event(channel, text, keywords, themes, score)

        # 디스코드 알림 (state 파일 통해)
        try:
            from common_utils import update_state
            update_state(
                "nbot_state.json",
                telegram_event={
                    "keywords": keywords,
                    "themes":   themes,
                    "score":    score,
                    "text":     text[:200],
                    "time":     now,
                }
            )
        except Exception as e:
            print(f"⚠️ state 업데이트 오류: {e}")

    print("👂 메시지 대기 중...")
    await client.run_until_disconnected()

if __name__ == "__main__":
    init_db()
    print("🚀 텔레그램 모니터 시작")
    asyncio.run(main())
