with open('cbot.py', 'r') as f:
    content = f.read()

# 1. get_current_positions — 전체 잔고 기준
old = """    def get_current_positions(self) -> dict:
        \"\"\"보유 코인 포지션 조회 — coin_pool 기준.\"\"\"
        balances = self.get_balances()
        markets  = [m for m in self.coin_pool if m.startswith("KRW-")]
        prices   = self.get_current_price(markets) if markets else {}
        pos = {}
        for market in markets:
            currency = market.replace("KRW-", "")
            info     = balances.get(currency)
            if not info or info["balance"] < 0.00001: continue
            avg = info["avg_buy_price"]
            bal = info["balance"]
            if avg > 0:
                pos[market] = {
                    "entry_price": avg,
                    "qty":         bal,
                    "current":     prices.get(market, avg),
                }
        return pos"""

new = """    def get_current_positions(self) -> dict:
        \"\"\"보유 코인 포지션 조회 — 전체 잔고 기준 (coin_pool 무관).\"\"\"
        balances = self.get_balances()
        held_markets = [
            f"KRW-{cur}" for cur in balances
            if cur != "KRW" and balances[cur]["balance"] > 0.00001
               and balances[cur]["avg_buy_price"] > 0
        ]
        if not held_markets:
            return {}
        prices = self.get_current_price(held_markets)
        pos = {}
        for market in held_markets:
            cur  = market.replace("KRW-", "")
            info = balances.get(cur)
            if not info: continue
            pos[market] = {
                "entry_price": info["avg_buy_price"],
                "qty":         info["balance"],
                "current":     prices.get(market, info["avg_buy_price"]),
            }
        return pos"""

content = content.replace(old, new)

# 2. 1차익절 force_all=True
old2 = """        # ── 1차 익절 +5% ────────────────────────────────────
        if stage < 1 and rate >= SELL_1ST_RATE:
            sell_qty = max(qty * SELL_1ST_QTY, 0.00001)
            if (qty - sell_qty) * current < MIN_ORDER_AMT:
                sell_qty = qty
                print(f"ℹ️ 1차익절 후 잔량 미달 → 전량 {market}")
            self.notify(f"✂️ 1차익절 {market} | {rate:+.2%} | {sell_qty:.6f}개")
            if self.sell(market, sell_qty, f"1차익절({rate:+.2%})",
                         sell_price=current, force_all=(sell_qty >= qty)):
                if sell_qty >= qty:
                    self.peak_tracker.pop(market, None)
                else:
                    tracker["stage"]      = 1
                    tracker["remain_qty"] = qty - sell_qty
            return"""

new2 = """        # ── 1차 익절 +5% ────────────────────────────────────
        if stage < 1 and rate >= SELL_1ST_RATE:
            sell_qty = max(qty * SELL_1ST_QTY, 0.00001)
            if (qty - sell_qty) * current < MIN_ORDER_AMT or sell_qty * current < MIN_ORDER_AMT:
                sell_qty = qty
                print(f"ℹ️ 1차익절 최소금액 미달 → 전량 {market}")
            self.notify(f"✂️ 1차익절 {market} | {rate:+.2%} | {sell_qty:.6f}개")
            if self.sell(market, sell_qty, f"1차익절({rate:+.2%})",
                         sell_price=current, force_all=True):
                if sell_qty >= qty:
                    self.peak_tracker.pop(market, None)
                else:
                    tracker["stage"]      = 1
                    tracker["remain_qty"] = qty - sell_qty
            return"""

content = content.replace(old2, new2)

with open('cbot.py', 'w') as f:
    f.write(content)
print("✅ cbot.py 패치 완료")
