import sys

path = sys.argv[1] if len(sys.argv) > 1 else "interface/kiki_briefing.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old = '''    active = state.get("active_sectors", [])'''
new = '''    _sbot_state_tmp = read_state("sbot")
    active = _sbot_state_tmp.get("active_sectors", [])'''

if old in content:
    content = content.replace(old, new, 1)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("✅ 수정 완료")
else:
    print("❌ 패턴 미일치")
