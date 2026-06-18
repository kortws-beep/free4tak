import sys

path = sys.argv[1] if len(sys.argv) > 1 else "bots/cbot.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old = '''                # ── 매수 슬롯 (1차 익절 후 슬롯 반환) ──────────
                익절중 = sum(
                    1 for m in self.positions
                    if self.peak_tracker.get(m, {}).get("stage", 0) >= 1
                )
                available_slots = MAX_POSITIONS - len(self.positions) + 익절중
                if 익절중:
                    print(f"  ♻️ 익절진행중 {익절중}코인 슬롯 반환 → 가용:{available_slots}")'''

new = '''                # ── 매수 슬롯 (1차 익절 후 슬롯 반환, 주문가능금액 40만원 이상일 때만) ──
                익절중 = sum(
                    1 for m in self.positions
                    if self.peak_tracker.get(m, {}).get("stage", 0) >= 1
                )
                보너스 = 익절중 if krw >= 400_000 else 0
                available_slots = MAX_POSITIONS - len(self.positions) + 보너스
                if 보너스:
                    print(f"  ♻️ 익절진행중 {보너스}코인 슬롯 반환 → 가용:{available_slots}")'''

if old in content:
    content = content.replace(old, new, 1)
    print("✅ 수정 완료")
else:
    print("❌ 패턴 미일치")
    sys.exit(1)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
