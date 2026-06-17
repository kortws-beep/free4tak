import sys

path = sys.argv[1] if len(sys.argv) > 1 else "interface/kiki.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

results = []

old1 = '''    elif cmd == "!정지":
        await cmd_pause(ctx, True, "nbot")
    elif cmd == "!시작":
        await cmd_pause(ctx, False, "nbot")'''
new1 = '''    elif cmd == "!정지":
        await cmd_pause(ctx, True, "sbot")
    elif cmd == "!시작":
        await cmd_pause(ctx, False, "sbot")'''
if old1 in content:
    content = content.replace(old1, new1, 1)
    results.append("✅ !정지/!시작")
else:
    results.append("❌ !정지/!시작 미일치")

old2 = '''    elif cmd.startswith("!관심"):
        parts = cmd.split()
        if len(parts) == 2:
            await cmd_watchlist(ctx, parts[1], "nbot")
        elif len(parts) == 1:
            await cmd_watchlist_show(ctx, "nbot")
        else:
            await ctx.send("❌ 사용법: !관심 005930")'''
new2 = '''    elif cmd.startswith("!관심"):
        parts = cmd.split()
        if len(parts) == 2:
            await cmd_watchlist(ctx, parts[1], "sbot")
        elif len(parts) == 1:
            await cmd_watchlist_show(ctx, "sbot")
        else:
            await ctx.send("❌ 사용법: !관심 005930")'''
if old2 in content:
    content = content.replace(old2, new2, 1)
    results.append("✅ !관심")
else:
    results.append("❌ !관심 미일치")

print("\n".join(results))
with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
