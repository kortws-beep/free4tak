import sys

path = sys.argv[1] if len(sys.argv) > 1 else "core/sbot_strategy.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old = '''        # ----------------------------------------------------------
        # ② MA20 이탈 — 추세 종료 (stage 무관, 항상 체크)
        # ----------------------------------------------------------
        if ma20 > 0 and current < ma20:
            print(f"📉 MA20 이탈 {code} | 현재:{current:,.0f} < MA20:{ma20:,.0f}")
            on_sell(code, qty, f"MA20이탈({rate:+.2%})", current)
            if rate < 0:
                on_loss()
            peak_tracker.pop(code, None)
            return "MA20이탈"

        # ----------------------------------------------------------
        # ③ 손절가 이탈'''

new = '''        # ----------------------------------------------------------
        # ② MA20 이탈 체크 — 제거함 (2026-06-23)
        #   ATR 기반 손절/트레일링/목표가로 충분히 추세 관리되며,
        #   MA20이 손절선보다 멀리 있어 오히려 손절이 늦어지는 부작용
        #   발생(삼화콘덴서 -21.3%까지 방치). sbo2와 동일한 이유로 비활성화.
        #   (필요시 git 히스토리에서 복원 가능)

        # ----------------------------------------------------------
        # ③ 손절가 이탈'''

if old in content:
    content = content.replace(old, new, 1)
    print("✅ MA20 이탈 체크 제거 완료")
else:
    print("❌ 패턴 미일치")
    sys.exit(1)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
