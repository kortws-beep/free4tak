import sys

path = sys.argv[1] if len(sys.argv) > 1 else "bots/cbot.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

results = []

# 1. tracker 초기화 시 buy_date 추가 (신규 초기화 블록)
old1 = '''            existing = self.peak_tracker.get(market, {})
            self.peak_tracker[market] = {
                "peak_rate":   existing.get("peak_rate", rate),
                "peak_price":  existing.get("peak_price", current),
                "stage":       existing.get("stage", 0),
                "stop_price":  stop,
                "target1":     target1,
                "target_next": existing.get("target_next", target1),
                "atr_val":     atr_val,
            }'''
new1 = '''            import datetime as _dt
            existing = self.peak_tracker.get(market, {})
            self.peak_tracker[market] = {
                "peak_rate":   existing.get("peak_rate", rate),
                "peak_price":  existing.get("peak_price", current),
                "stage":       existing.get("stage", 0),
                "stop_price":  stop,
                "target1":     target1,
                "target_next": existing.get("target_next", target1),
                "atr_val":     atr_val,
                "buy_date":    existing.get("buy_date", _dt.date.today().isoformat()),  # ★ 25일 기한
            }'''
if old1 in content:
    content = content.replace(old1, new1, 1)
    results.append("✅ tracker 초기화 buy_date 추가")
else:
    results.append("❌ tracker 초기화 미일치")

# 2. 매수 시점 peak_tracker에도 buy_date 추가
old2 = '''                            self.peak_tracker[market] = {
                                "peak_rate":       0.0,
                                "stage":           0,
                                "remain_qty":      est_qty,
                                "buy2_done":       False,
                                "buy1_price":      buy_price,
                                "effective_entry": buy_price,
                            }'''
new2 = '''                            import datetime as _dt2
                            self.peak_tracker[market] = {
                                "peak_rate":       0.0,
                                "stage":           0,
                                "remain_qty":      est_qty,
                                "buy2_done":       False,
                                "buy1_price":      buy_price,
                                "effective_entry": buy_price,
                                "buy_date":        _dt2.date.today().isoformat(),  # ★ 25일 기한
                            }'''
if old2 in content:
    content = content.replace(old2, new2, 1)
    results.append("✅ 매수시점 buy_date 추가")
else:
    results.append("❌ 매수시점 buy_date 미일치")

# 3. ③ 트레일링 앞에 25일 기한 체크 추가 (stage==0만)
old3 = '''        # ③ 트레일링 스탑 (목표가1 달성 이후) ──────────────
        if stage >= 1 and atr_val > 0:'''
new3 = '''        # ②-1 보유기한 초과 (stage==0, 목표가1 미달성만 — 25일) ──
        if stage == 0:
            try:
                import datetime as _dt3
                buy_date_str = tracker.get("buy_date", "")
                if buy_date_str:
                    buy_date  = _dt3.date.fromisoformat(buy_date_str)
                    held_days = (_dt3.date.today() - buy_date).days
                    if held_days >= 25:
                        label = "기한초과" if rate >= 0 else "기한초과(손실)"
                        self.notify(
                            f"⏰ {label} {market} | 보유{held_days}일 | {rate:+.2%}",
                            critical=True,
                        )
                        if self.sell(market, qty, f"{label}({rate:+.2%})",
                                     sell_price=current, force_all=True):
                            if rate < 0:
                                self.daily_loss_count += 1
                            self.peak_tracker.pop(market, None)
                            self._check_daily_loss_limit()
                        return
            except Exception:
                pass

        # ③ 트레일링 스탑 (목표가1 달성 이후) ──────────────
        if stage >= 1 and atr_val > 0:'''
if old3 in content:
    content = content.replace(old3, new3, 1)
    results.append("✅ 25일 기한 체크 추가")
else:
    results.append("❌ 25일 기한 체크 미일치")

# 4. ④ 목표가1 달성 시 50% 매도 추가
old4 = '''        if target_next > 0 and current >= target_next:
            if stage == 0:
                new_stop   = round(entry + atr_val * ATR_RAISE_MULT, 0)
                new_target = round(current + atr_val * ATR_TARGET_MULT, 0)
                tracker["stop_price"]  = new_stop
                tracker["target_next"] = new_target
                tracker["stage"]       = 1
                print(f"🎯 목표가1 달성 {market} ({rate:+.2%}) | "
                      f"손절↑:{new_stop:,.0f} | 새목표:{new_target:,.0f}")
                self.notify(
                    f"🎯 목표가1 달성 {market} ({rate:+.2%})\\n"
                    f"손절↑:{new_stop:,.0f} | 새목표:{new_target:,.0f}",
                    critical=False,
                )
            else:'''
new4 = '''        if target_next > 0 and current >= target_next:
            if stage == 0:
                # ★ 목표가1 달성 → 50% 매도(수익실현)
                half_qty = qty if qty * current <= MIN_ORDER_AMT * 2 else qty / 2
                if half_qty > 0 and (qty - half_qty) * current >= MIN_ORDER_AMT:
                    if self.sell(market, half_qty, f"목표1익절50%({rate:+.2%})",
                                 sell_price=current, force_all=False):
                        print(f"💰 목표1 50%매도 {market} | {half_qty:.6f}개 @ {current:,.0f}")
                new_stop   = round(entry + atr_val * ATR_RAISE_MULT, 0)
                new_target = round(current + atr_val * ATR_TARGET_MULT, 0)
                tracker["stop_price"]  = new_stop
                tracker["target_next"] = new_target
                tracker["stage"]       = 1
                print(f"🎯 목표가1 달성 {market} ({rate:+.2%}) | "
                      f"손절↑:{new_stop:,.0f} | 새목표:{new_target:,.0f}")
                self.notify(
                    f"🎯 목표가1 달성 {market} ({rate:+.2%}) — 50%매도\\n"
                    f"손절↑:{new_stop:,.0f} | 새목표:{new_target:,.0f}",
                    critical=False,
                )
            else:'''
if old4 in content:
    content = content.replace(old4, new4, 1)
    results.append("✅ 목표가1 50% 매도 추가")
else:
    results.append("❌ 목표가1 50% 매도 미일치")

print("\n".join(results))
with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
