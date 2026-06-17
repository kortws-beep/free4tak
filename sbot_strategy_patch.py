# sbot_strategy.py check_sell() 수정 — 25일 기한 + 목표가1 50% 매도
# 서버에서 직접 적용할 패치 스크립트

import sys

path = sys.argv[1] if len(sys.argv) > 1 else "sbot_strategy.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

results = []

# 1. tracker 초기화에 buy_time 추가
old1 = '''            peak_tracker[code] = {
                "peak_rate":   rate,
                "peak_price":  current,
                "stage":       0,           # 0=초기, 1=목표1달성, 2=목표2이상
                "buy2_done":   True,
                "buy1_price":  entry,
                "stop_price":  levels["stop_price"],
                "target1":     levels["target1"],
                "target_next": levels["target1"],
                "atr_val":     atr_val,
            }'''
new1 = '''            import datetime as _dt
            peak_tracker[code] = {
                "peak_rate":   rate,
                "peak_price":  current,
                "stage":       0,           # 0=초기, 1=목표1달성, 2=목표2이상
                "buy2_done":   True,
                "buy1_price":  entry,
                "stop_price":  levels["stop_price"],
                "target1":     levels["target1"],
                "target_next": levels["target1"],
                "atr_val":     atr_val,
                "buy_date":    _dt.date.today().isoformat(),  # ★ 25일 기한 추적용
            }'''
if old1 in content:
    content = content.replace(old1, new1, 1)
    results.append("✅ tracker 초기화 buy_date 추가")
else:
    results.append("❌ tracker 초기화 미일치")

# 2. ④ 트레일링 스탑 앞에 "25일 기한" 체크 삽입 (stage==0 한정)
old2 = '''        # ----------------------------------------------------------
        # ④ 트레일링 스탑 (목표가1 달성 이후)
        # ----------------------------------------------------------
        if stage >= 1 and atr_val > 0:'''
new2 = '''        # ----------------------------------------------------------
        # ④-0 보유기한 초과 (stage==0, 목표가1 미달성 종목만 — 25일)
        # ----------------------------------------------------------
        if stage == 0:
            try:
                import datetime as _dt
                buy_date_str = tracker.get("buy_date", "")
                if buy_date_str:
                    buy_date = _dt.date.fromisoformat(buy_date_str)
                    held_days = (_dt.date.today() - buy_date).days
                    if held_days >= 25:
                        label = "기한초과" if rate >= 0 else "기한초과(손실)"
                        print(f"⏰ {label} {code} | 보유{held_days}일 | {rate:+.2%}")
                        on_sell(code, qty, f"{label}({rate:+.2%})", current)
                        if rate < 0:
                            on_loss()
                        peak_tracker.pop(code, None)
                        return label
            except Exception:
                pass

        # ----------------------------------------------------------
        # ④ 트레일링 스탑 (목표가1 달성 이후)
        # ----------------------------------------------------------
        if stage >= 1 and atr_val > 0:'''
if old2 in content:
    content = content.replace(old2, new2, 1)
    results.append("✅ 25일 기한 체크 추가")
else:
    results.append("❌ 25일 기한 체크 미일치")

# 3. ⑤ 목표가1 달성 시 50% 매도 추가 (stage==0 → 1 전환부)
old3 = '''        if current >= target_next:
            if stage == 0:
                # 목표가1 달성 → 손절을 매수가 + ATR×1 로 올림
                new_stop   = round(entry + atr_val * ATR_RAISE_MULT, 0)
                new_target = round(current + atr_val * ATR_TARGET_MULT, 0)
                tracker["stop_price"]  = new_stop
                tracker["target_next"] = new_target
                tracker["stage"]       = 1
                print(f"🎯 목표가1 달성 {code} ({rate:+.2%}) | "
                      f"손절 상향:{new_stop:,.0f} | 새목표:{new_target:,.0f}")
            else:'''
new3 = '''        if current >= target_next:
            if stage == 0:
                # ★ 목표가1 달성 → 50% 매도(수익실현) + 손절을 매수가+ATR×1로 올림
                sell_qty = qty if qty <= 1 else qty // 2
                if sell_qty > 0:
                    on_sell(code, sell_qty, f"목표1익절50%({rate:+.2%})", current)
                new_stop   = round(entry + atr_val * ATR_RAISE_MULT, 0)
                new_target = round(current + atr_val * ATR_TARGET_MULT, 0)
                tracker["stop_price"]  = new_stop
                tracker["target_next"] = new_target
                tracker["stage"]       = 1
                tracker["half_sold"]   = True
                print(f"🎯 목표가1 달성 {code} ({rate:+.2%}) | 50%매도:{sell_qty}주 | "
                      f"손절 상향:{new_stop:,.0f} | 새목표:{new_target:,.0f}")
            else:'''
if old3 in content:
    content = content.replace(old3, new3, 1)
    results.append("✅ 목표가1 50% 매도 추가")
else:
    results.append("❌ 목표가1 50% 매도 미일치")

print("\n".join(results))
with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
