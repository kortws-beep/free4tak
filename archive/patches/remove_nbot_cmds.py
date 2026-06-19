import sys

path = sys.argv[1] if len(sys.argv) > 1 else "interface/kiki.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

results = []

# 1. !매도 (nbot) 명령 블록 제거
old1 = '''    elif cmd.startswith("!매도"):
        parts = cmd.split()
        if len(parts) == 2:
            await cmd_sell(ctx, parts[1], "nbot")
        else:
            await ctx.send("❌ 사용법: !매도 005930")
    elif cmd.startswith("!매수"):
        parts = cmd.split()
        if len(parts) == 3 and parts[2].isdigit():
            await cmd_buy(ctx, parts[1], int(parts[2]))
        else:
            await ctx.send("❌ 사용법: !매수 005930 10")'''
new1 = ''  # 완전 제거

if old1 in content:
    content = content.replace(old1, new1, 1)
    results.append("✅ !매도(nbot) + !매수 명령 제거")
else:
    results.append("❌ !매도/!매수 블록 미일치")

# 2. import에서 cmd_buy 제거
old2 = "    cmd_status, cmd_score, cmd_sell, cmd_buy, cmd_analyze,"
new2 = "    cmd_status, cmd_score, cmd_sell, cmd_analyze,"
if old2 in content:
    content = content.replace(old2, new2, 1)
    results.append("✅ import에서 cmd_buy 제거")
else:
    results.append("❌ import 라인 미일치")

print("\n".join(results))
with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
