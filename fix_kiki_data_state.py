import sys

path = sys.argv[1] if len(sys.argv) > 1 else "interface/kiki_data.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

results = []

old1 = '''BOT_STATE_FILES = {
    "nbot": "bot_state.json",
    "sbot": "sbot_state.json",
    "cbot": "cbot_state.json",
}'''
new1 = '''BOT_STATE_FILES = {
    "sbot": "sbot_state.json",
    "sbo2": os.path.join("lina_bot", "sbo2_state.json"),
    "cbot": "cbot_state.json",
}'''
if old1 in content:
    content = content.replace(old1, new1, 1)
    results.append("✅ BOT_STATE_FILES")
else:
    results.append("❌ BOT_STATE_FILES 미일치")

old2 = '''def read_state(bot: str = "nbot") -> dict:
    """봇 상태 파일 읽기 (없으면 기본값)"""
    fname = BOT_STATE_FILES.get(bot, "bot_state.json")
    return _read_state_atomic(fname, default={
        "paused":      False,
        "score_enter": 55,
        "pending_cmd": None,
        "cmd_result":  None,
        "last_status": None,
    })
def write_state(bot: str = "nbot", state: dict = None):
    """봇 상태 파일 쓰기 (★ atomic — 중간에 죽어도 안 깨짐)"""
    if state is None: state = {}
    fname = BOT_STATE_FILES.get(bot, "bot_state.json")
    _write_state_atomic(fname, state)
def update_state(bot: str = "nbot", **kwargs):
    """봇 상태 부분 업데이트"""
    fname = BOT_STATE_FILES.get(bot, "bot_state.json")
    _update_state_atomic(fname, **kwargs)'''
new2 = '''def read_state(bot: str = "sbot") -> dict:
    """봇 상태 파일 읽기 (없으면 기본값)"""
    fname = BOT_STATE_FILES.get(bot)
    if not fname:
        return {}
    return _read_state_atomic(fname, default={
        "paused":      False,
        "score_enter": 70,
        "pending_cmd": None,
        "cmd_result":  None,
        "last_status": None,
    })
def write_state(bot: str = "sbot", state: dict = None):
    """봇 상태 파일 쓰기 (★ atomic — 중간에 죽어도 안 깨짐)"""
    if state is None: state = {}
    fname = BOT_STATE_FILES.get(bot)
    if not fname:
        return
    _write_state_atomic(fname, state)
def update_state(bot: str = "sbot", **kwargs):
    """봇 상태 부분 업데이트"""
    fname = BOT_STATE_FILES.get(bot)
    if not fname:
        return
    _update_state_atomic(fname, **kwargs)'''
if old2 in content:
    content = content.replace(old2, new2, 1)
    results.append("✅ read/write/update_state")
else:
    results.append("❌ read/write/update_state 미일치")

print("\n".join(results))
with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
