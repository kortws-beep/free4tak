import sys

path = sys.argv[1] if len(sys.argv) > 1 else "interface/kiki_cmd.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

results = []

old1 = '''    """전체 봇 재시작 (kiki 제외) — nbot/sbot/cbot/telegram/sector"""
    import subprocess as _sp
    import asyncio as _ac
    SERVICES = [
        ("sbot2",     "yeongam9-nbot"),
        ("sbot",     "yeongam9-sbot"),
        ("cbot",     "yeongam9-cbot"),
        ("telegram", "yeongam9-telegram"),
        ("sector",   "yeongam9-sector"),
    ]'''
new1 = '''    """전체 봇 재시작 (kiki 제외) — sbot/sbo2/cbot/telegram/sector"""
    import subprocess as _sp
    import asyncio as _ac
    SERVICES = [
        ("sbot",     "yeongam9-sbot"),
        ("sbo2",     "yeongam9-sbo2"),
        ("cbot",     "yeongam9-cbot"),
        ("telegram", "yeongam9-telegram"),
        ("sector",   "yeongam9-sector"),
    ]'''
if old1 in content:
    content = content.replace(old1, new1, 1)
    results.append("✅ cmd_restart_all SERVICES")
else:
    results.append("❌ cmd_restart_all 미일치")

old2 = '''async def cmd_theme_status(ctx):
    state          = read_state("sbot2")'''
new2 = '''async def cmd_theme_status(ctx):
    state          = read_state("sbot")'''
if old2 in content:
    content = content.replace(old2, new2, 1)
    results.append("✅ cmd_theme_status read_state")
else:
    results.append("❌ cmd_theme_status 미일치")

old3 = '''        lines.append("💡 nbot이 매시 20분 자동 체크합니다")'''
new3 = '''        lines.append("💡 sbot이 매시 20분 자동 체크합니다")'''
if old3 in content:
    content = content.replace(old3, new3, 1)
    results.append("✅ 안내 텍스트")
else:
    results.append("❌ 안내 텍스트 미일치")

print("\n".join(results))
with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
