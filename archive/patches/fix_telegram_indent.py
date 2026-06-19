import sys

path = sys.argv[1] if len(sys.argv) > 1 else "intelligence/telegram_monitor.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old = '''        # 기존 빅 이벤트 키워드 (테마 가산점)
        keywords, themes, score = analyze_message(text)
        if keywords:
            print(f"\\n🚨 [{now}] 빅 이벤트 감지!")
            print(f"   채널: {channel} | 키워드: {keywords} | +{score}점")
            print(f"   내용: {text[:100]}")
        # ★ 키워드 매칭 여부와 무관하게 모든 메시지 저장 (브리핑/텔레스윙용)
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
                print(f"⚠️ state 업데이트 오류: {e}")'''

new = '''        # 기존 빅 이벤트 키워드 (테마 가산점)
        keywords, themes, score = analyze_message(text)
        if keywords:
            print(f"\\n🚨 [{now}] 빅 이벤트 감지!")
            print(f"   채널: {channel} | 키워드: {keywords} | +{score}점")
            print(f"   내용: {text[:100]}")
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
        # ★ 키워드 매칭 여부와 무관하게 모든 메시지 저장 (브리핑/텔레스윙용)
        save_event(channel, text, keywords, themes, score)'''

if old in content:
    content = content.replace(old, new, 1)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("✅ 들여쓰기 수정 완료")
else:
    print("❌ 패턴 미일치")
