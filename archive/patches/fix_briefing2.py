import sys

path = sys.argv[1] if len(sys.argv) > 1 else "interface/kiki_briefing.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

replacements = [
    ('def read_state(bot: str = "nbot") -> dict:\n    from common_utils import read_state as _rs\n    fname = _BOT_STATE_FILES.get(bot, "bot_state.json")',
     'def read_state(bot: str = "sbot") -> dict:\n    from common_utils import read_state as _rs\n    fname = _BOT_STATE_FILES.get(bot)\n    if not fname:\n        return {}'),

    ('def write_state(bot: str = "nbot", state: dict = None):\n    from common_utils import write_state as _ws\n    fname = _BOT_STATE_FILES.get(bot, "bot_state.json")',
     'def write_state(bot: str = "sbot", state: dict = None):\n    from common_utils import write_state as _ws\n    fname = _BOT_STATE_FILES.get(bot)\n    if not fname:\n        return'),

    ('def update_state(bot: str = "nbot", **kwargs):',
     'def update_state(bot: str = "sbot", **kwargs):'),

    ('BOT_STATE_FILES = {}\n',
     ''),  # 빈 딕셔너리로 덮어쓰는 죽은 코드 제거
]

results = []
for old, new in replacements:
    if old in content:
        content = content.replace(old, new, 1)
        results.append(f"✅ 적용: {old[:50]}...")
    else:
        results.append(f"❌ 미일치: {old[:50]}...")

print("\n".join(results))
with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
