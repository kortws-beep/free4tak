import sys

path = sys.argv[1] if len(sys.argv) > 1 else "interface/kiki_cmd.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old = '''    realized_all = get_today_realized_all()
    sbot2_p = realized_all.get("sbot2", 0)
    sbot_p = realized_all.get("sbot", 0)
    cbot_p = realized_all.get("cbot", 0)
    total  = sbot2_p + sbot_p + cbot_p'''
new = '''    realized_all = get_today_realized_all()
    sbo2_p = realized_all.get("sbo2", 0)
    sbot_p = realized_all.get("sbot", 0)
    cbot_p = realized_all.get("cbot", 0)
    total  = sbo2_p + sbot_p + cbot_p'''

results = []
if old in content:
    content = content.replace(old, new, 1)
    results.append("✅ 변수 초기화부")
else:
    results.append("❌ 변수 초기화부 미일치")

old2 = '''    if sbot2_p: msg += f"  📈 중단기봇: **{sbot2_p:+,}원**\\n"
    if sbot_p: msg += f"  📊 스윙봇: **{sbot_p:+,}원**\\n"
    if cbot_p: msg += f"  🪙 코인봇: **{cbot_p:+,}원**\\n"
    if not (sbot2_p or sbot_p or cbot_p):'''
new2 = '''    if sbot_p: msg += f"  📊 스윙봇: **{sbot_p:+,}원**\\n"
    if sbo2_p: msg += f"  📊 스윙봇2: **{sbo2_p:+,}원**\\n"
    if cbot_p: msg += f"  🪙 코인봇: **{cbot_p:+,}원**\\n"
    if not (sbo2_p or sbot_p or cbot_p):'''
if old2 in content:
    content = content.replace(old2, new2, 1)
    results.append("✅ 표시부")
else:
    results.append("❌ 표시부 미일치")

print("\n".join(results))
with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
