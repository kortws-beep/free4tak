import sys

path = sys.argv[1] if len(sys.argv) > 1 else "bots/sbot.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

results = []

# 패턴 1: run() 루프의 슬롯 없으면 분석 스킵 체크
old1 = '''                익절중 = sum(
                    1 for c in self.positions
                    if self.peak_tracker.get(c, {}).get("stage", 0) >= 1
                )
                avail_slots = MAX_POSITIONS - len(self.positions) + 익절중
                if avail_slots <= 0:'''
new1 = '''                익절중 = sum(
                    1 for c in self.positions
                    if self.peak_tracker.get(c, {}).get("stage", 0) >= 1
                )
                # ★ 주문가능금액 100만원 이상일 때만 보너스 슬롯 적용
                보너스 = 익절중 if psbl_cash >= 1_000_000 else 0
                avail_slots = MAX_POSITIONS - len(self.positions) + 보너스
                if avail_slots <= 0:'''
if old1 in content:
    content = content.replace(old1, new1, 1)
    results.append("✅ run루프 분석스킵체크")
else:
    results.append("❌ run루프 분석스킵체크 미일치")

# 패턴 2: _run_analysis 안의 미너비니 추천용 avail 계산
old2 = '''            익절중 = sum(
                1 for c in self.positions
                if self.peak_tracker.get(c, {}).get("stage", 0) >= 1
            )
            avail = MAX_POSITIONS - len(self.positions) + 익절중
            existing_codes = set(c for c, _, _ in top10)'''
new2 = '''            익절중 = sum(
                1 for c in self.positions
                if self.peak_tracker.get(c, {}).get("stage", 0) >= 1
            )
            보너스 = 익절중 if psbl_cash >= 1_000_000 else 0
            avail = MAX_POSITIONS - len(self.positions) + 보너스
            existing_codes = set(c for c, _, _ in top10)'''
if old2 in content:
    content = content.replace(old2, new2, 1)
    results.append("✅ 미너비니 avail 계산")
else:
    results.append("❌ 미너비니 avail 계산 미일치")

# 패턴 3: _execute_buys 안의 실제 매수 슬롯 계산
old3 = '''        # 1차 익절 후 슬롯 반환
        익절중 = sum(
            1 for c in self.positions
            if self.peak_tracker.get(c, {}).get("stage", 0) >= 1
        )
        slots = MAX_POSITIONS - len(self.positions) + 익절중'''
new3 = '''        # 1차 익절 후 슬롯 반환 (주문가능금액 100만원 이상일 때만)
        익절중 = sum(
            1 for c in self.positions
            if self.peak_tracker.get(c, {}).get("stage", 0) >= 1
        )
        보너스 = 익절중 if psbl_cash >= 1_000_000 else 0
        slots = MAX_POSITIONS - len(self.positions) + 보너스'''
if old3 in content:
    content = content.replace(old3, new3, 1)
    results.append("✅ _execute_buys 슬롯 계산")
else:
    results.append("❌ _execute_buys 슬롯 계산 미일치")

print("\n".join(results))
with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
