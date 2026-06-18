import sys

path = sys.argv[1] if len(sys.argv) > 1 else "bots/sbot.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old = '''BUY_SCORE_ENTER  = 70             # 매수 진입 기준점'''
new = '''BUY_SCORE_ENTER  = 85             # 매수 진입 기준점 (백테스트 검증: PF 2.07, 승률44.9%)'''

if old in content:
    content = content.replace(old, new, 1)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("✅ 수정 완료")
else:
    print("❌ 패턴 미일치")
