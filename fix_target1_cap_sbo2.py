import sys

path = sys.argv[1] if len(sys.argv) > 1 else "lina_bot/sbo2.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old = '''            if _atr_rate > 0:
                _atr_val  = curr_price * _atr_rate
                _stop     = round(curr_price - _atr_val * 2.0, 0)
                _tgt      = round(curr_price + _atr_val * 3.0, 0)
            else:
                # ATR 없을 때 후보에서 제공한 값 or 폴백
                _stop = cand["stop"] or round(curr_price * 0.93, 0)
                _tgt  = cand["tgt"]  or round(curr_price * 1.12, 0)
                _atr_val = 0'''

new = '''            if _atr_rate > 0:
                _atr_val  = curr_price * _atr_rate
                _stop     = round(curr_price - _atr_val * 2.0, 0)
                # ★ 목표가1 상한 캡 (2026-06-23) — ATR×3과 +20% 중 작은 값
                #   고변동성 종목의 1차 목표가 사실상 도달불가능해지는 문제 방지
                _tgt_atr  = curr_price + _atr_val * 3.0
                _tgt_cap  = curr_price * 1.20
                _tgt      = round(min(_tgt_atr, _tgt_cap), 0)
            else:
                # ATR 없을 때 후보에서 제공한 값 or 폴백
                _stop = cand["stop"] or round(curr_price * 0.93, 0)
                _tgt  = cand["tgt"]  or round(curr_price * 1.12, 0)
                _atr_val = 0'''

if old in content:
    content = content.replace(old, new, 1)
    print("✅ 수정 완료")
else:
    print("❌ 패턴 미일치")
    sys.exit(1)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
