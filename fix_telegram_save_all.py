import sys

path = sys.argv[1] if len(sys.argv) > 1 else "intelligence/telegram_monitor.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

results = []

# 1. handler에서 keywords 유무와 상관없이 항상 저장
old1 = '''        # 기존 빅 이벤트 키워드 (테마 가산점)
        keywords, themes, score = analyze_message(text)
        if keywords:
            print(f"\\n🚨 [{now}] 빅 이벤트 감지!")
            print(f"   채널: {channel} | 키워드: {keywords} | +{score}점")
            print(f"   내용: {text[:100]}")
            save_event(channel, text, keywords, themes, score)'''
new1 = '''        # 기존 빅 이벤트 키워드 (테마 가산점)
        keywords, themes, score = analyze_message(text)
        if keywords:
            print(f"\\n🚨 [{now}] 빅 이벤트 감지!")
            print(f"   채널: {channel} | 키워드: {keywords} | +{score}점")
            print(f"   내용: {text[:100]}")
        # ★ 키워드 매칭 여부와 무관하게 모든 메시지 저장 (브리핑/텔레스윙용)
        save_event(channel, text, keywords, themes, score)'''
if old1 in content:
    content = content.replace(old1, new1, 1)
    results.append("✅ 모든 메시지 저장으로 변경")
else:
    results.append("❌ handler 저장부 미일치")

# 2. init_db()에 30일 초과 삭제 로직 추가
old2 = '''def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")'''
new2 = '''def cleanup_old_events(days: int = 30):
    """★ 신규: telegram_events 30일 초과 데이터 정리 (원본 로그만 — 분석결과는 event_bonus/stock_event_bonus에 별도 보관)"""
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        cutoff = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        cur = conn.execute("DELETE FROM telegram_events WHERE created_at < ?", (cutoff,))
        deleted = cur.rowcount
        conn.commit()
        conn.close()
        if deleted > 0:
            print(f"🧹 telegram_events {days}일 초과 {deleted}건 삭제")
    except Exception as e:
        print(f"⚠️ telegram_events 정리 오류: {e}")


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")'''
if old2 in content:
    content = content.replace(old2, new2, 1)
    results.append("✅ cleanup_old_events 함수 추가")
else:
    results.append("❌ init_db 위치 미일치")

# 3. main()에서 시작 시 + 이후 주기적으로 cleanup 호출
old3 = '''    print("👂 메시지 대기 중...")
    await client.run_until_disconnected()'''
new3 = '''    # ★ 1시간마다 오래된 메시지 정리
    async def _periodic_cleanup():
        while True:
            cleanup_old_events(days=30)
            await asyncio.sleep(3600)
    asyncio.create_task(_periodic_cleanup())

    print("👂 메시지 대기 중...")
    await client.run_until_disconnected()'''
if old3 in content:
    content = content.replace(old3, new3, 1)
    results.append("✅ 주기적 정리 태스크 추가")
else:
    results.append("❌ main() 끝부분 미일치")

print("\n".join(results))
with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
