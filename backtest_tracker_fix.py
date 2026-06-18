import sys

path = sys.argv[1] if len(sys.argv) > 1 else "sbot_backtest_engine.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old = '''    def _simulate_buy(self, code: str, price: float, qty: int,
                      date: str, score: int = 0) -> bool:
        fill_price = price * (1 + self.config.slippage)
        cost  = fill_price * qty
        fee   = cost * self.config.fee_rate
        total = cost + fee

        if total > self.cash:
            return False

        self.cash -= total
        self.positions[code] = {
            "entry_price": fill_price,
            "qty":         qty,
            "buy_date":    date,
            "score":       score,
        }
        self.peak_tracker[code] = {
            "peak_rate":       0.0,
            "stage":           0,
            "remain_qty":      qty,
            "buy2_done":       True,   # 스윙은 2차 매수 없음
            "buy1_price":      fill_price,
            "effective_entry": fill_price,
            "buy_date":        date,   # ★ 시뮬레이션 날짜로 고정 (실제 오늘 날짜 아님!)
        }'''

new = '''    def _simulate_buy(self, code: str, price: float, qty: int,
                      date: str, score: int = 0) -> bool:
        fill_price = price * (1 + self.config.slippage)
        cost  = fill_price * qty
        fee   = cost * self.config.fee_rate
        total = cost + fee

        if total > self.cash:
            return False

        self.cash -= total
        self.positions[code] = {
            "entry_price": fill_price,
            "qty":         qty,
            "buy_date":    date,
            "score":       score,
        }

        # ★ sbot_strategy.SwingStrategy.check_sell()이 기대하는 완전한 tracker 구조
        #   (stop_price/target1/target_next/atr_val/peak_price 모두 필요 — 없으면 KeyError)
        if self._using_swing:
            atr_rate_init = 0.0
            try:
                df0 = self.loader.load_ohlcv(code)
                if not df0.empty:
                    d0 = pd.Timestamp(date)
                    if d0 in df0.index:
                        _r0 = df0.loc[d0]
                        if hasattr(_r0, "columns"): _r0 = _r0.iloc[-1]
                        atr14_0 = float(_r0.get("atr14", 0) or 0)
                        if atr14_0 > 0 and fill_price > 0:
                            atr_rate_init = atr14_0 / fill_price
            except Exception:
                pass
            levels = self.strategy.calc_atr_levels(fill_price, atr_rate_init)
            self.peak_tracker[code] = {
                "peak_rate":   0.0,
                "peak_price":  fill_price,
                "stage":       0,
                "buy2_done":   True,
                "buy1_price":  fill_price,
                "stop_price":  levels["stop_price"],
                "target1":     levels["target1"],
                "target_next": levels["target1"],
                "atr_val":     levels["atr_val"],
                "buy_date":    date,   # ★ 시뮬레이션 날짜로 고정 (실제 오늘 날짜 아님!)
            }
        else:
            self.peak_tracker[code] = {
                "peak_rate":       0.0,
                "stage":           0,
                "remain_qty":      qty,
                "buy2_done":       True,   # 스윙은 2차 매수 없음
                "buy1_price":      fill_price,
                "effective_entry": fill_price,
                "buy_date":        date,   # ★ 시뮬레이션 날짜로 고정 (실제 오늘 날짜 아님!)
            }'''

if old in content:
    content = content.replace(old, new, 1)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("✅ 수정 완료")
else:
    print("❌ 패턴 미일치")
