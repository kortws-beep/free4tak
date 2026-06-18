import sys

path = sys.argv[1] if len(sys.argv) > 1 else "bots/cbot.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

results = []

# 1. MAX_POSITIONS 3 → 4
old1 = "MAX_POSITIONS     = 3           # 최대 3코인"
new1 = "MAX_POSITIONS     = 4           # 최대 4코인 (3→4, 마지막 슬롯은 잔액만큼 매수)"
if old1 in content:
    content = content.replace(old1, new1, 1)
    results.append("✅ MAX_POSITIONS 3→4")
else:
    results.append("❌ MAX_POSITIONS 미일치")

# 2. 매수금액 — 마지막 슬롯이면 잔액만큼만 매수
old2 = '''                        print(f"🚀 매수 시도 {market} | {ai_score}점 | "
                              f"{BUY_1ST_AMT:,}원")
                        if self.buy(market, BUY_1ST_AMT):
                            buy_price = ind.get("current", 0)
                            est_qty   = BUY_1ST_AMT / buy_price if buy_price else 0'''
new2 = '''                        # ★ 마지막 슬롯(포지션 4개째)이면 잔액만큼만 매수
                        _is_last_slot = (len(self.positions) + 1) >= MAX_POSITIONS
                        _buy_amt = min(BUY_1ST_AMT, int(krw * 0.98)) if _is_last_slot else BUY_1ST_AMT
                        if _buy_amt < MIN_ORDER_AMT:
                            print(f"  ⏭️ {market} — 잔액 부족({_buy_amt:,}원 < 최소주문)")
                            continue
                        print(f"🚀 매수 시도 {market} | {ai_score}점 | "
                              f"{_buy_amt:,}원" + (" (마지막슬롯·잔액매수)" if _is_last_slot else ""))
                        if self.buy(market, _buy_amt):
                            buy_price = ind.get("current", 0)
                            est_qty   = _buy_amt / buy_price if buy_price else 0'''
if old2 in content:
    content = content.replace(old2, new2, 1)
    results.append("✅ 마지막 슬롯 잔액매수 로직 추가")
else:
    results.append("❌ 매수 시도 블록 미일치")

print("\n".join(results))
with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
