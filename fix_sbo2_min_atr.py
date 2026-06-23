import sys

path = sys.argv[1] if len(sys.argv) > 1 else "lina_bot/sbo2.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old = '''            # ★ ATR 기반 손절/목표가 계산 (추세추종 방식)
            _atr_rate = self._get_atr_rate(code)
            if _atr_rate > 0:
                _atr_val  = curr_price * _atr_rate
                _stop     = round(curr_price - _atr_val * 2.0, 0)
                _tgt      = round(curr_price + _atr_val * 3.0, 0)
            else:
                # ATR'''

new = '''            # ★ ATR 기반 손절/목표가 계산 (추세추종 방식)
            #   최소 ATR 비율 1% 강제 — 더존비즈온처럼 변동성이 극단적으로
            #   낮은 종목은 ATR이 0에 가까워 목표/손절폭이 너무 좁아지고
            #   매수 직후 즉시 목표달성/손절이 동시에 발동하는 문제 방지 (2026-06-23)
            MIN_ATR_RATE_FOR_BUY = 0.01
            _atr_rate = self._get_atr_rate(code)
            if _atr_rate > 0 and _atr_rate < MIN_ATR_RATE_FOR_BUY:
                print(f"   ⚠️ {name} ATR 비율 {_atr_rate:.3%} 너무 낮음 → 최소값 {MIN_ATR_RATE_FOR_BUY:.0%} 적용")
                _atr_rate = MIN_ATR_RATE_FOR_BUY
            if _atr_rate > 0:
                _atr_val  = curr_price * _atr_rate
                _stop     = round(curr_price - _atr_val * 2.0, 0)
                _tgt      = round(curr_price + _atr_val * 3.0, 0)
            else:
                # ATR'''

if old in content:
    content = content.replace(old, new, 1)
    print("✅ 수정 완료")
else:
    print("❌ 패턴 미일치")
    sys.exit(1)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
