import sys

path = sys.argv[1] if len(sys.argv) > 1 else "lina_bot.py"

with open(path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# 314번~351번 라인 (1-indexed) = index 313~350 교체
start_idx = 313
end_idx   = 352  # exclusive (351번째 줄, 즉 index 350인 "return f...오류" 라인까지 포함하려면 352)

# 안전장치: 시작/끝 줄 내용 확인
assert lines[start_idx].startswith("def fetch_recent_telegram_events"), f"시작줄 불일치: {lines[start_idx]!r}"
assert "디비 접근 오류" in lines[end_idx-1], f"끝줄 불일치: {lines[end_idx-1]!r}"

new_func = '''def fetch_recent_telegram_events(limit_count=4, minutes_back=65):
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
        return f"디비 접근 오류: {str(e)}"
'''

new_lines = lines[:start_idx] + [new_func] + lines[end_idx:]

with open(path, 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print("✅ 함수 교체 완료")
