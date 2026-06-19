import sys

path = sys.argv[1] if len(sys.argv) > 1 else "lina_bot/sbo2.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old = '''            # 손절만 sold_today 등록
            if "손절" in reason:
                self.sold_today[code] = now_hms()'''

new = '''            # ★ 매도 사유 무관하게 모든 매도는 당일 재매수 금지
            #   (이전: "손절"만 등록 → MA20이탈 등은 재매수 금지가 안 걸려
            #    매수↔매도 무한 반복 버그 발생. 2026-06-19 확인)
            self.sold_today[code] = now_hms()'''

if old in content:
    content = content.replace(old, new, 1)
    print("✅ 수정 완료")
else:
    print("❌ 패턴 미일치")
    sys.exit(1)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
