import sys

path = sys.argv[1] if len(sys.argv) > 1 else "trend_analyzer.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old = '''        recent_trading_value = (curr_price * vol_avg_recent) / 100_000_000  # 억원
        if recent_trading_value < MIN_TRADING_VALUE_EOK:
            continue

'''
new = ''

if old in content:
    content = content.replace(old, new, 1)
    print("✅ get_trend_data() 거래대금 필터 제거 완료")
else:
    print("❌ 패턴 미일치")
    sys.exit(1)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
