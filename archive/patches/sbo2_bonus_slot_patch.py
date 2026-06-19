import sys

path = sys.argv[1] if len(sys.argv) > 1 else "lina_bot/sbo2.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old = '''        slots = MAX_POSITIONS - len(self.positions) - len(self._pending_orders)
        if slots <= 0:
            print("📦 [sbo2] 포지션 FULL")
            return'''

new = '''        # ★ 1차 익절 후 슬롯 반환 (주문가능금액 100만원 이상일 때만)
        익절중 = sum(1 for p in self.positions.values() if p.get("stage", 0) >= 1)
        _psbl_check = self.api.get_psbl_order_cash("005930")
        보너스 = 익절중 if _psbl_check >= 1_000_000 else 0
        slots = MAX_POSITIONS - len(self.positions) - len(self._pending_orders) + 보너스
        if slots <= 0:
            print("📦 [sbo2] 포지션 FULL")
            return'''

if old in content:
    content = content.replace(old, new, 1)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("✅ 수정 완료")
else:
    print("❌ 패턴 미일치")
