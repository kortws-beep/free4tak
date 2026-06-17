import sys

path = sys.argv[1] if len(sys.argv) > 1 else "lina_bot/sbo2.py"

with open(path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

idx = None
for i, line in enumerate(lines):
    if line.strip() == 'slots = MAX_POSITIONS - len(self.positions)':
        idx = i
        break

if idx is None:
    print("❌ 라인을 찾을 수 없음")
    sys.exit(1)

print(f"교체 대상: {idx+1}번 라인 → {lines[idx].rstrip()}")

new_block = '''        # ★ 1차 익절 후 슬롯 반환 (주문가능금액 100만원 이상일 때만)
        익절중 = sum(1 for p in self.positions.values() if p.get("stage", 0) >= 1)
        _psbl_check = self.api.get_psbl_order_cash("005930")
        보너스 = 익절중 if _psbl_check >= 1_000_000 else 0
        slots = MAX_POSITIONS - len(self.positions) - len(self._pending_orders) + 보너스
'''

lines[idx] = new_block

with open(path, 'w', encoding='utf-8') as f:
    f.writelines(lines)

print("✅ 교체 완료")
