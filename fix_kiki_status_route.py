import sys

path = sys.argv[1] if len(sys.argv) > 1 else "interface/kiki.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old = '''    # ── 단타봇 ───────────────────────────────────────────────
    if cmd == "!상태":
        await cmd_status(ctx, "nbot")'''
new = '''    # ── 스윙봇 ───────────────────────────────────────────────
    if cmd == "!상태":
        await cmd_status(ctx, "sbot")'''

if old in content:
    content = content.replace(old, new, 1)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("✅ 수정 완료")
else:
    print("❌ 패턴 미일치")
