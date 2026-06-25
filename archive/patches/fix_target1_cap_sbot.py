import sys

path = sys.argv[1] if len(sys.argv) > 1 else "core/sbot_strategy.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old = '''        if atr_rate > 0:
            atr_val  = entry * atr_rate
            stop     = round(entry - atr_val * ATR_STOP_MULT, 0)
            target1  = round(entry + atr_val * ATR_TARGET_MULT, 0)
        else:
            # ATR 없을 때 폴백
            atr_val  = entry * abs(FALLBACK_STOP) / ATR_STOP_MULT
            stop     = round(entry * (1 + FALLBACK_STOP), 0)
            target1  = round(entry * (1 + FALLBACK_TARGET), 0)'''

new = '''        if atr_rate > 0:
            atr_val  = entry * atr_rate
            stop     = round(entry - atr_val * ATR_STOP_MULT, 0)
            # ★ 목표가1 상한 캡 (2026-06-23) — ATR×3과 +20% 중 작은 값
            #   고변동성 종목(예: 테크윙)은 ATR×3이 +50~80%까지 치솟아
            #   1차 목표가 사실상 도달불가능해지는 문제 방지
            target1_atr = entry + atr_val * ATR_TARGET_MULT
            target1_cap = entry * (1 + TARGET1_CAP_RATE)
            target1     = round(min(target1_atr, target1_cap), 0)
        else:
            # ATR 없을 때 폴백
            atr_val  = entry * abs(FALLBACK_STOP) / ATR_STOP_MULT
            stop     = round(entry * (1 + FALLBACK_STOP), 0)
            target1  = round(entry * (1 + FALLBACK_TARGET), 0)'''

if old in content:
    content = content.replace(old, new, 1)
    print("✅ target1 캡 로직 추가")
else:
    print("❌ 패턴 미일치")
    sys.exit(1)

# 상수 추가
old2 = "ATR_TARGET_MULT  = 3.0    # 목표: 매수가 + ATR × 3"
new2 = ("ATR_TARGET_MULT  = 3.0    # 목표: 매수가 + ATR × 3\n"
        "TARGET1_CAP_RATE = 0.20   # ★ 목표가1 상한 +20% (ATR×3과 비교해 작은 값 사용)")
if old2 in content:
    content = content.replace(old2, new2, 1)
    print("✅ TARGET1_CAP_RATE 상수 추가")
else:
    print("⚠️ 상수 정의 위치 못찾음 — 수동 확인 필요")

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
