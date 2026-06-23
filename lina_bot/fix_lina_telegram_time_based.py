import sys

path = sys.argv[1] if len(sys.argv) > 1 else "lina_bot.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old = '''def fetch_recent_telegram_events(limit_count=4):
    """마지막 브리핑 이후 새로 들어온 메시지만 반환 (중복 방지)"""
    global LAST_TELEGRAM_ID
    try:
        conn = sqlite3.connect(DB_PATH_TELEGRAM, timeout=10)
        cursor = conn.cursor()
        # 마지막 처리 ID 이후 새 메시지만 조회
        query = """
            SELECT id, channel, message, keywords, themes, score 
            FROM telegram_events 
            WHERE id > ?
            ORDER BY id ASC
        """
        cursor.execute(query, (LAST_TELEGRAM_ID,))
        rows = cursor.fetchall()
        conn.close()
        if not rows: return ""
        raw_context = ""
        max_id = LAST_TELEGRAM_ID
        seen = set()
        for r in rows:
            row_id, channel, msg, keywords, themes, score = r
            msg = str(msg or "").strip().replace("\\xed\\x8c\\xb9리스", "팹리스")
            if not msg or msg in seen:
                continue
            seen.add(msg)
            kw = ", ".join(json.loads(keywords)) if keywords else "없음"
            raw_context += f"채널: [{channel}] | 내용: {msg} | 키워드: {kw} | 가산점: +{score or 10}점\\n\\n"
            if row_id > max_id:
                max_id = row_id
        if raw_context:
            LAST_TELEGRAM_ID = max_id  # 처리한 마지막 ID 업데이트
        return raw_context
    except Exception as e:
        return f"디비 접근 오류: {str(e)}"'''

new = '''def fetch_recent_telegram_events(limit_count=4, minutes_back=65):
    """
    ★ 시간 기준으로 변경 (2026-06-23) — 기존 id 기반(LAST_TELEGRAM_ID 전역변수) 방식은
      재시작/재로드/예외 상황에서 값이 꼬이면 영구적으로 "새 메시지 없음"이 되는
      버그가 있어 시간 윈도우 방식으로 교체. 메모리 상태에 의존하지 않아 안전.
    최근 minutes_back분 이내 메시지만 반환.
    """
    try:
        conn = sqlite3.connect(DB_PATH_TELEGRAM, timeout=10)
        cursor = conn.cursor()
        cutoff = (datetime.datetime.now() -
                  datetime.timedelta(minutes=minutes_back)).strftime("%Y-%m-%d %H:%M:%S")
        query = """
            SELECT id, channel, message, keywords, themes, score
            FROM telegram_events
            WHERE created_at >= ?
            ORDER BY id ASC
        """
        cursor.execute(query, (cutoff,))
        rows = cursor.fetchall()
        conn.close()
        if not rows: return ""
        raw_context = ""
        seen = set()
        for r in rows:
            row_id, channel, msg, keywords, themes, score = r
            msg = str(msg or "").strip().replace("\\xed\\x8c\\xb9리스", "팹리스")
            if not msg or msg in seen:
                continue
            seen.add(msg)
            kw = ", ".join(json.loads(keywords)) if keywords else "없음"
            raw_context += f"채널: [{channel}] | 내용: {msg} | 키워드: {kw} | 가산점: +{score or 10}점\\n\\n"
        return raw_context
    except Exception as e:
        return f"디비 접근 오류: {str(e)}"'''

if old in content:
    content = content.replace(old, new, 1)
    print("✅ 수정 완료")
else:
    print("❌ 패턴 미일치")
    sys.exit(1)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
