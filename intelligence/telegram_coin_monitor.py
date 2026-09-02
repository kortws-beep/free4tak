"""
telegram_coin_monitor.py — 코인 전용 텔레그램 채널 수집기
================================================================
[이 파일이 하는 일]
  코인 뉴스/시황 텔레그램 채널을 별도로 모니터링해 원문을 그대로 저장한다.
  telegram_monitor.py(주식 전용, 키워드 가산점/공시 파이프라인)와는
  완전히 분리 — 코인 데이터가 주식 신호 파이프라인에 섞이면 안 된다는
  사용자 요구(2026-09-03)로 별도 스크립트/DB/세션으로 신설.

[현재 범위]
  수집만 한다 — 요약/AI 시장국면 판단/cbot 프롬프트 연결은 아직 없음
  (사용자 결정: "일단 뉴스만 수집하는 걸로"). 나중에 데이터가 쌓이면
  cbot의 "AI 프롬프트에 시장국면 전달" 기능과 연결할 예정.

[세션]
  telegram_monitor.py와 같은 텔레그램 계정을 쓰되, 세션 파일은
  telegram_coin_session.session으로 분리(동일 세션파일을 두 프로세스가
  동시에 열면 충돌 위험 — 텔레그램은 계정당 다중 세션을 정상 지원하므로
  세션파일만 복제해 별도 프로세스로 기동).
"""
import os
import sys
import sqlite3
import asyncio
import datetime

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ["core", "intelligence", ""]:
    _p = os.path.join(_BASE, _d)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from dotenv import load_dotenv
load_dotenv(os.path.join(_BASE, ".env"))

from telethon import TelegramClient, events

API_ID   = int(os.getenv("TELEGRAM_API_ID", "34756144"))
API_HASH = os.getenv("TELEGRAM_API_HASH", "d7c4e05b6ac021c5bfe2e89db29938fc")
SESSION  = os.path.join(_BASE, "intelligence", "telegram_coin_session")

# ★ 코인 채널 전용 목록 — 주식 채널(telegram_monitor.py의 CHANNELS)과 절대 겹치지 않게 유지
CHANNELS = [
    "coinnesskr",   # 코인니스 — 코인 뉴스/시황 속보
]

DB_PATH = os.path.join(_BASE, "intelligence", "coin_telegram_events.db")


def init_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS coin_telegram_events (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            channel  TEXT NOT NULL,
            message  TEXT NOT NULL,
            recv_at  TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_coin_tg_recv ON coin_telegram_events(recv_at)
    """)
    conn.commit()
    conn.close()


def save_event(channel: str, message: str):
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.execute(
            "INSERT INTO coin_telegram_events (channel, message, recv_at) VALUES (?, ?, ?)",
            (channel, message[:2000], datetime.datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"⚠️ 코인 텔레그램 이벤트 저장 오류: {e}")


def cleanup_old_events(days: int = 30):
    try:
        cutoff = (datetime.datetime.now() - datetime.timedelta(days=days)).isoformat(timespec="seconds")
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.execute("DELETE FROM coin_telegram_events WHERE recv_at < ?", (cutoff,))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"⚠️ 코인 텔레그램 이벤트 정리 오류: {e}")


client = TelegramClient(SESSION, API_ID, API_HASH)


async def main():
    init_db()
    await client.start()
    print("✅ 코인 텔레그램 연결 완료")
    print(f"📡 모니터링 채널: {CHANNELS}")

    @client.on(events.NewMessage(chats=CHANNELS))
    async def handler(event):
        text    = event.message.message or ""
        channel = event.chat.username or str(event.chat_id)
        if not text.strip():
            return
        now = datetime.datetime.now().strftime("%H:%M:%S")
        print(f"[{now}] {channel}: {text[:80]}")
        save_event(channel, text)

    async def _periodic_cleanup():
        while True:
            cleanup_old_events(days=30)
            await asyncio.sleep(3600)
    asyncio.create_task(_periodic_cleanup())

    print("🚀 코인 텔레그램 모니터 가동 중...")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
