import sys

path = sys.argv[1] if len(sys.argv) > 1 else "swing_analyzer.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old = '''        valid_volumes = [v for v in volumes if v > 0]
        if len(valid_volumes) < 10: continue
        vol_avg_all    = sum(valid_volumes) / len(valid_volumes)
        vol_avg_recent = sum(valid_volumes[:5]) / 5
        if vol_avg_all == 0 or vol_avg_recent >= vol_avg_all * VOL_DRY_RATIO: continue'''

new = '''        valid_volumes = [v for v in volumes if v > 0]
        if len(valid_volumes) < 10: continue
        vol_avg_all    = sum(valid_volumes) / len(valid_volumes)
        vol_avg_recent = sum(valid_volumes[:5]) / 5
        if vol_avg_all == 0 or vol_avg_recent >= vol_avg_all * VOL_DRY_RATIO: continue

        # ★ 거래대금 필터 — 최근 5일 평균 거래대금 50억 미달 제외 (잡주 방지)
        recent_trading_value = (curr_price * vol_avg_recent) / 100_000_000  # 억원
        if recent_trading_value < MIN_TRADING_VALUE_EOK:
            continue'''

if old in content:
    content = content.replace(old, new, 1)
    results_msg = "✅ 거래대금 필터 추가"
else:
    results_msg = "❌ 패턴 미일치"
    print(results_msg)
    sys.exit(1)

# 상수 추가 — 파일 상단 ATR_PERIOD 등이 정의된 곳 근처에 추가
old2 = '''VOL_DRY_RATIO'''
if old2 in content and "MIN_TRADING_VALUE_EOK" not in content.split("VOL_DRY_RATIO")[0]:
    # VOL_DRY_RATIO가 정의된 라인을 찾아 그 뒤에 상수 추가
    import re
    m = re.search(r'^(VOL_DRY_RATIO\s*=\s*[\d.]+.*\n)', content, re.MULTILINE)
    if m:
        content = content[:m.end()] + "MIN_TRADING_VALUE_EOK = 50    # 최소 거래대금(억원) — 잡주 방지\n" + content[m.end():]
        results_msg += " + 상수 정의 추가"
    else:
        results_msg += " (⚠️ 상수 정의 위치 못찾음 — 수동 추가 필요: MIN_TRADING_VALUE_EOK = 50)"

print(results_msg)
with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
