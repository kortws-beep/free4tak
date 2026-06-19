import sys

path = sys.argv[1] if len(sys.argv) > 1 else "interface/kiki_cmd.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

results = []

old1 = '''        # 중단기봇 손절카운터 표시
        if bot_name == "sbot2":
            daily_loss = status.get("daily_loss", 0)
            if daily_loss > 0:
                lines.append(f"🛑 당일 손절: {daily_loss}회")'''
new1 = '''        # 당일 손절카운터 표시 (모든 봇 공통)
        daily_loss = status.get("daily_loss", 0)
        if daily_loss > 0:
            lines.append(f"🛑 당일 손절: {daily_loss}회")'''

if old1 in content:
    content = content.replace(old1, new1, 1)
    results.append("✅ 손절카운터 표시 — 모든 봇 공통으로 변경")
else:
    results.append("❌ 손절카운터 블록 미일치")

old2 = '''    update_state("sbot2", score_enter=score)'''
new2 = '''    update_state("sbot", score_enter=score)'''

if old2 in content:
    content = content.replace(old2, new2, 1)
    results.append("✅ cmd_score sbot2→sbot")
else:
    results.append("❌ cmd_score 미일치")

print("\n".join(results))
with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
