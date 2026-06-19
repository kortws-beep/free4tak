import sys

path = sys.argv[1] if len(sys.argv) > 1 else "sbot_backtest_engine.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old = '''        self.peak_tracker[code] = {
            "peak_rate":       0.0,
            "stage":           0,
            "remain_qty":      qty,
            "buy2_done":       True,   # 스윙은 2차 매수 없음
            "buy1_price":      fill_price,
            "effective_entry": fill_price,
        }'''
new = '''        self.peak_tracker[code] = {
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
