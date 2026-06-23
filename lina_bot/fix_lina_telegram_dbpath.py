import sys

path = sys.argv[1] if len(sys.argv) > 1 else "lina_bot.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old = '''DB_PATH_TELEGRAM = os.path.join(base_dir, "intelligence", "telegram_events.db")'''
new = '''# ★ 수정 (2026-06-23): base_dir(lina_bot/)가 아니라 stock_bot 루트 기준으로 변경.
#   기존 경로(lina_bot/intelligence/telegram_events.db)는 죽은 옛 사본(6/12 이후 갱신 안됨)을
#   가리키고 있어, 30분 텔레그램 브리핑이 항상 "새 속보 없음"으로 나오던 근본 원인.
DB_PATH_TELEGRAM = os.path.join(os.path.dirname(base_dir), "intelligence", "telegram_events.db")'''

if old in content:
    content = content.replace(old, new, 1)
    print("✅ 수정 완료")
else:
    print("❌ 패턴 미일치")
    sys.exit(1)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
