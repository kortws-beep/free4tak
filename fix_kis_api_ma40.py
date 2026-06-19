import sys

path = sys.argv[1] if len(sys.argv) > 1 else "core/kis_api.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old = '''                "ma5":           ma(5),
                "ma10":          ma(10),
                "ma20":          ma(20),
                "ma60":          ma(60),'''
new = '''                "ma5":           ma(5),
                "ma10":          ma(10),
                "ma20":          ma(20),
                "ma40":          ma(40),    # ★ sbo2 MA이탈 매도 기준 (백테스트 검증: MA20보다 우수)
                "ma60":          ma(60),'''

if old in content:
    content = content.replace(old, new, 1)
    print("✅ 수정 완료")
else:
    print("❌ 패턴 미일치")
    sys.exit(1)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
