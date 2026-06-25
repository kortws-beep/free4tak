import sys

path = sys.argv[1] if len(sys.argv) > 1 else "lina_bot/sbo2.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old = '''            # ① MA20 이탈 — 추세 종료'''
new = '''            # ① MA40 이탈 — 추세 종료 (구 MA20 → 백테스트 검증 후 MA40으로 변경, 2026-06-19)'''
if old in content:
    content = content.replace(old, new, 1)
    n1 = "✅ 주석 수정"
else:
    n1 = "❌ 주석 미일치"

old2 = '''                    ma20 = float(tech.get("ma20", 0) or 0)
                    if ma20 > 0 and curr < ma20:
                        reason = f"MA20이탈({rate:+.1f}%)"
                        print(f"📉 MA20 이탈 {code} | 현재:{curr:,.0f} < MA20:{ma20:,.0f}")'''
new2 = '''                    ma20 = float(tech.get("ma40", 0) or 0)   # ★ 변수명 유지(영향범위 최소화), 값은 ma40
                    if ma20 > 0 and curr < ma20:
                        reason = f"MA40이탈({rate:+.1f}%)"
                        print(f"📉 MA40 이탈 {code} | 현재:{curr:,.0f} < MA40:{ma20:,.0f}")'''
if old2 in content:
    content = content.replace(old2, new2, 1)
    n2 = "✅ MA40 적용"
else:
    n2 = "❌ MA20 체크부 미일치"

print(n1)
print(n2)
if "❌" in n1 or "❌" in n2:
    sys.exit(1)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
