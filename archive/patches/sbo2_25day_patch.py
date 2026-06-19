import sys

path = sys.argv[1] if len(sys.argv) > 1 else "lina_bot/sbo2.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

results = []

# 1. ③ 트레일링 앞에 25일 기한 체크 추가 (stage==0만)
old1 = '''            reason = None

            # ① MA20 이탈 — 추세 종료
            try:
                tech = self.api.get_technical_indicators(code, {})
                ma20 = float(tech.get("ma20", 0) or 0)
                if ma20 > 0 and curr < ma20:
                    reason = f"MA20이탈({rate:+.1f}%)"
                    print(f"📉 MA20 이탈 {code} | 현재:{curr:,.0f} < MA20:{ma20:,.0f}")
            except Exception:
                ma20 = 0'''
new1 = '''            reason = None

            # ⓪ 보유기한 초과 (stage==0, 목표가1 미달성 종목만 — 25일)
            if stage == 0:
                try:
                    import datetime as _dt
                    buy_date_str = pos.get("buy_time", "")
                    if buy_date_str:
                        buy_date  = _dt.datetime.strptime(buy_date_str, "%Y-%m-%d").date()
                        held_days = (_dt.date.today() - buy_date).days
                        if held_days >= 25:
                            reason = f"기한초과({rate:+.1f}%)"
                            print(f"⏰ 기한초과 {code} | 보유{held_days}일 | {rate:+.1f}%")
                except Exception:
                    pass

            # ① MA20 이탈 — 추세 종료
            if not reason:
                try:
                    tech = self.api.get_technical_indicators(code, {})
                    ma20 = float(tech.get("ma20", 0) or 0)
                    if ma20 > 0 and curr < ma20:
                        reason = f"MA20이탈({rate:+.1f}%)"
                        print(f"📉 MA20 이탈 {code} | 현재:{curr:,.0f} < MA20:{ma20:,.0f}")
                except Exception:
                    ma20 = 0'''
if old1 in content:
    content = content.replace(old1, new1, 1)
    results.append("✅ 25일 기한 체크 추가")
else:
    results.append("❌ 25일 기한 체크 미일치")

# 2. ④ 목표가1 달성 시 50% 매도 추가
old2 = '''            if not reason and target_next > 0 and curr >= target_next:
                if stage == 0:
                    new_stop   = round(entry + atr_val * 1.0, 0) if atr_val > 0 else round(entry * 1.02, 0)
                    new_target = round(curr + atr_val * 3.0, 0) if atr_val > 0 else round(curr * 1.10, 0)
                    pos["stop_price"]  = new_stop
                    pos["tgt_price"]   = new_target
                    pos["target_next"] = new_target
                    pos["stage"]       = 1
                    self._save_state()
                    print(f"🎯 목표가1 달성 {code} ({rate:+.1f}%) | "
                          f"손절↑:{new_stop:,.0f} | 새목표:{new_target:,.0f}")
                    _notify(
                        f"🎯 [sbo2] 목표가1 달성 {name}({code})\\n"
                        f"   {rate:+.1f}% | 손절↑:{new_stop:,.0f} | 새목표:{new_target:,.0f}",
                        critical=False
                    )
                else:'''
new2 = '''            if not reason and target_next > 0 and curr >= target_next:
                if stage == 0:
                    # ★ 목표가1 달성 → 50% 매도(수익실현)
                    half_qty = qty if qty <= 1 else qty // 2
                    if half_qty > 0:
                        ok_half = self.api.sell(code, half_qty, price=int(curr))
                        if ok_half:
                            save_sell_trade(
                                code=code, sell_price=curr, reason=f"목표1익절50%({rate:+.1f}%)",
                                entry_price=entry, qty=half_qty, buy_time=pos.get("buy_time", ""),
                                stock_name=name, grade=pos.get("grade", "")
                            )
                            if _master_record:
                                _master_record(
                                    bot_type="sbo2", code=code, stock_name=name,
                                    buy_price=entry, sell_price=curr, qty=half_qty,
                                    sell_reason=f"목표1익절50%({rate:+.1f}%)",
                                    buy_tag=pos.get("grade", ""), ai_score=pos.get("score", 0),
                                )
                            pos["qty"] = qty - half_qty
                            print(f"💰 목표1 50%매도 {code} | {half_qty}주 @ {curr:,.0f}원")
                    new_stop   = round(entry + atr_val * 1.0, 0) if atr_val > 0 else round(entry * 1.02, 0)
                    new_target = round(curr + atr_val * 3.0, 0) if atr_val > 0 else round(curr * 1.10, 0)
                    pos["stop_price"]  = new_stop
                    pos["tgt_price"]   = new_target
                    pos["target_next"] = new_target
                    pos["stage"]       = 1
                    self._save_state()
                    print(f"🎯 목표가1 달성 {code} ({rate:+.1f}%) | "
                          f"손절↑:{new_stop:,.0f} | 새목표:{new_target:,.0f}")
                    _notify(
                        f"🎯 [sbo2] 목표가1 달성 {name}({code}) — 50%매도\\n"
                        f"   {rate:+.1f}% | 손절↑:{new_stop:,.0f} | 새목표:{new_target:,.0f}",
                        critical=False
                    )
                else:'''
if old2 in content:
    content = content.replace(old2, new2, 1)
    results.append("✅ 목표가1 50% 매도 추가")
else:
    results.append("❌ 목표가1 50% 매도 미일치")

print("\n".join(results))
with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
