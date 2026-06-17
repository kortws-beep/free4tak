import sys

path = sys.argv[1] if len(sys.argv) > 1 else "interface/kiki_cmd.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old = '''    bot_label = "📈 단타봇" if bot_name == "sbot2" else "📊 스윙봇" if bot_name == "sbot" else "🤖 봇"'''
new = '''    bot_label = "📊 스윙봇" if bot_name == "sbot" else "📊 스윙봇2" if bot_name == "sbo2" else "🤖 봇"'''

if old in content:
    content = content.replace(old, new, 1)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("✅ 수정 완료")
else:
    print("❌ 패턴 미일치")
