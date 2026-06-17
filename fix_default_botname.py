import sys

path = sys.argv[1] if len(sys.argv) > 1 else "interface/kiki_cmd.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

replacements = [
    ('async def cmd_status(ctx, bot_name: str = "sbot2"):',
     'async def cmd_status(ctx, bot_name: str = "sbot"):'),
    ('async def cmd_sell(ctx, code: str, bot_name: str = "sbot2"):',
     'async def cmd_sell(ctx, code: str, bot_name: str = "sbot"):'),
    ('async def cmd_pause(ctx, pause: bool, bot_name: str = "sbot2"):',
     'async def cmd_pause(ctx, pause: bool, bot_name: str = "sbot"):'),
    ('async def cmd_watchlist(ctx, code: str, bot_name: str = "sbot2"):',
     'async def cmd_watchlist(ctx, code: str, bot_name: str = "sbot"):'),
    ('async def cmd_watchlist_show(ctx, bot_name: str = "sbot2"):',
     'async def cmd_watchlist_show(ctx, bot_name: str = "sbot"):'),
]

results = []
for old, new in replacements:
    if old in content:
        content = content.replace(old, new, 1)
        results.append(f"✅ {old}")
    else:
        results.append(f"❌ 미일치: {old}")

print("\n".join(results))
with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
