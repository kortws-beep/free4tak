import sys

path = sys.argv[1] if len(sys.argv) > 1 else "interface/kiki.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old = '''    elif cmd == "!s정지":
        await cmd_pause(ctx, True, "sbot")
    elif cmd == "!s시작":
        await cmd_pause(ctx, False, "sbot")
    elif cmd.startswith("!s관심"):'''

new = '''    elif cmd == "!s정지":
        await cmd_pause(ctx, True, "sbot")
    elif cmd == "!s시작":
        await cmd_pause(ctx, False, "sbot")
    # ── sbo2 (스윙봇2) ───────────────────────────────────────
    elif cmd in ("!sbo2상태", "!상태 sbo2"):
        await cmd_status(ctx, "sbo2")
    elif cmd.startswith("!sbo2매도"):
        parts = cmd.split()
        if len(parts) == 2:
            await cmd_sell(ctx, parts[1], "sbo2")
        else:
            await ctx.send("❌ 사용법: !sbo2매도 005930")
    elif cmd == "!sbo2정지":
        await cmd_pause(ctx, True, "sbo2")
    elif cmd == "!sbo2시작":
        await cmd_pause(ctx, False, "sbo2")
    elif cmd.startswith("!s관심"):'''

if old in content:
    content = content.replace(old, new, 1)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("✅ 패치 완료")
else:
    print("❌ 패턴 미일치 — 수동 확인 필요")
