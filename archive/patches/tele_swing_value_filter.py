import sys

path = sys.argv[1] if len(sys.argv) > 1 else "tele_swing_analyzer.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old = '''        # ── 방어막 ────────────────────────────────────────────
        # 손절가 음수 or 손절폭 20% 초과 → 비정상 ATR → 스킵
        if stop_price <= 0 or stop_pct > 20:
            return result'''

new = '''        # ── 방어막 ────────────────────────────────────────────
        # 손절가 음수 or 손절폭 20% 초과 → 비정상 ATR → 스킵
        if stop_price <= 0 or stop_pct > 20:
            return result

        # ★ 거래대금 필터 — 최근 5일 평균 거래대금 50억 미달 제외 (잡주 방지)
        if len(volumes) >= 5:
            _vol_avg_recent = sum(volumes[:5]) / 5
            _recent_trading_value = (curr * _vol_avg_recent) / 100_000_000  # 억원
            if _recent_trading_value < MIN_TRADING_VALUE_EOK:
                return result'''

if old in content:
    content = content.replace(old, new, 1)
    print("✅ 거래대금 필터 추가")
else:
    print("❌ 패턴 미일치")
    sys.exit(1)

# 상수 추가 — 파일 상단 적당한 위치 찾기
if "MIN_TRADING_VALUE_EOK" not in content.split("def _calc_swing_score")[0]:
    import re
    m = re.search(r'^(TOP_N\s*=.*\n|MIN_SCORE\s*=.*\n)', content, re.MULTILINE)
    if m:
        insert_pos = content.find('\n', m.end()) if False else m.end()
        content = content[:m.end()] + "MIN_TRADING_VALUE_EOK = 50    # 최소 거래대금(억원) — 잡주 방지\n" + content[m.end():]
        print("✅ 상수 정의 추가")
    else:
        print("⚠️ 상수 정의 위치 못찾음 — 수동 추가 필요: MIN_TRADING_VALUE_EOK = 50")

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
