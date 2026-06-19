import sys

path = sys.argv[1] if len(sys.argv) > 1 else "interface/kiki_briefing.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old1 = '''_BOT_STATE_FILES = {
    "nbot": "bot_state.json",
    "sbot": "sbot_state.json",
    "cbot": "cbot_state.json",
}'''
new1 = '''_BOT_STATE_FILES = {
    "sbot": "sbot_state.json",
    "sbo2": "lina_bot/sbo2_state.json",
    "cbot": "cbot_state.json",
}'''

old2 = '''def read_state(bot: str = "nbot") -> dict:
    from common_utils import read_state as _rs
    fname = _BOT_STATE_FILES.get(bot, "bot_state.json")
    fpath = _os2.path.join(_base, fname)
    return _rs(fpath, default={})
def write_state(bot: str = "nbot", state: dict = None):
    from common_utils import write_state as _ws
    fname = _BOT_STATE_FILES.get(bot, "bot_state.json")
    fpath = _os2.path.join(_base, fname)
    _ws(fpath, state or {})
def update_state(bot: str = "nbot", **kwargs):
    state = read_state(bot)
    state.update(kwargs)
    write_state(bot, state)'''
new2 = '''def read_state(bot: str = "sbot") -> dict:
    from common_utils import read_state as _rs
    fname = _BOT_STATE_FILES.get(bot)
    if not fname:
        return {}
    fpath = _os2.path.join(_base, fname)
    return _rs(fpath, default={})
def write_state(bot: str = "sbot", state: dict = None):
    from common_utils import write_state as _ws
    fname = _BOT_STATE_FILES.get(bot)
    if not fname:
        return
    fpath = _os2.path.join(_base, fname)
    _ws(fpath, state or {})
def update_state(bot: str = "sbot", **kwargs):
    state = read_state(bot)
    state.update(kwargs)
    write_state(bot, state)'''

old3 = '''BOT_STATE_FILES = {
    "nbot": os.path.join(_base, "bot_state.json"),
    "sbot": os.path.join(_base, "sbot_state.json"),
    "cbot": os.path.join(_base, "cbot_state.json"),
}'''
new3 = '''BOT_STATE_FILES = {
    "sbot": os.path.join(_base, "sbot_state.json"),
    "sbo2": os.path.join(_base, "lina_bot", "sbo2_state.json"),
    "cbot": os.path.join(_base, "cbot_state.json"),
}'''

results = []
for name, old, new in [("_BOT_STATE_FILES", old1, new1), ("read/write/update", old2, new2), ("BOT_STATE_FILES", old3, new3)]:
    if old in content:
        content = content.replace(old, new, 1)
        results.append(f"✅ {name}")
    else:
        results.append(f"❌ {name} 미일치")

print("\n".join(results))
with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
