import sys

path = sys.argv[1] if len(sys.argv) > 1 else "trend_analyzer.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old = '''        valid_volumes = [v for v in volumes if v > 0]
        if len(valid_volumes) < 10: continue
        vol_avg_all    = sum(valid_volumes) / len(valid_volumes)
        vol_avg_recent = sum(valid_volumes[:5]) / 5
        if vol_avg_all == 0 or vol_avg_recent >= vol_avg_all * VOL_PULL_RATIO: continue

        f_net_raw  = [r[3] for r in rows]
        i_net_raw  = [r[4] for r in rows]
        supply_len = max(sum(1 for v in f_net_raw if v is not None),
                         sum(1 for v in i_net_raw if v is not None))
        f_net = [v if v is not None else 0 for v in f_net_raw]
        i_net = [v if v is not None else 0 for v in i_net_raw]

        if supply_len == 0:
            smart_ok = True
            f_pos_days = i_pos_days = f_cum = i_cum = 0
        else:'''

new = '''        valid_volumes = [v for v in volumes if v > 0]
        if len(valid_volumes) < 10: continue
        vol_avg_all    = sum(valid_volumes) / len(valid_volumes)
        vol_avg_recent = sum(valid_volumes[:5]) / 5
        if vol_avg_all == 0 or vol_avg_recent >= vol_avg_all * VOL_PULL_RATIO: continue

        # ★ 거래대금 필터 — 최근 5일 평균 거래대금 50억 미달 제외 (잡주 방지)
        recent_trading_value = (curr_price * vol_avg_recent) / 100_000_000  # 억원
        if recent_trading_value < MIN_TRADING_VALUE_EOK:
            continue

        f_net_raw  = [r[3] for r in rows]
        i_net_raw  = [r[4] for r in rows]
        supply_len = max(sum(1 for v in f_net_raw if v is not None),
                         sum(1 for v in i_net_raw if v is not None))
        f_net = [v if v is not None else 0 for v in f_net_raw]
        i_net = [v if v is not None else 0 for v in i_net_raw]

        if supply_len == 0:
            smart_ok = True
            f_pos_days = i_pos_days = f_cum = i_cum = 0
        else:'''

if old in content:
    content = content.replace(old, new, 1)
    print("✅ 거래대금 필터 추가")
else:
    print("❌ 패턴 미일치")
    sys.exit(1)

# 상수 추가
if "MIN_TRADING_VALUE_EOK" not in content:
    import re
    m = re.search(r'^(VOL_PULL_RATIO\s*=\s*[\d.]+.*\n)', content, re.MULTILINE)
    if m:
        content = content[:m.end()] + "MIN_TRADING_VALUE_EOK = 50    # 최소 거래대금(억원) — 잡주 방지\n" + content[m.end():]
        print("✅ 상수 정의 추가")
    else:
        print("⚠️ 상수 정의 위치 못찾음 — 수동 추가 필요: MIN_TRADING_VALUE_EOK = 50")

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
