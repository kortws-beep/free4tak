import sys

path = sys.argv[1] if len(sys.argv) > 1 else "lina_bot/sbo2.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old = '''            # 현재가 조회
            mdata = self.api.get_market_data(code)
            if not mdata:
                continue
            curr_price = float(mdata.get("stck_prpr", 0))
            if not (MIN_PRICE <= curr_price <= MAX_PRICE):
                continue'''

new = '''            # 현재가 조회
            mdata = self.api.get_market_data(code)
            if not mdata:
                continue
            curr_price = float(mdata.get("stck_prpr", 0))
            if not (MIN_PRICE <= curr_price <= MAX_PRICE):
                continue

            # ★ MA40 아래에서는 매수 금지 (매수 즉시 MA40이탈로 청산되는 헛매매 방지, 2026-06-19)
            try:
                _tech = self.api.get_technical_indicators(code, {})
                _ma40 = float(_tech.get("ma40", 0) or 0)
                if _ma40 > 0 and curr_price < _ma40:
                    print(f"⏭️ {name} 패스 — MA40({_ma40:,.0f}) 아래 (현재:{curr_price:,.0f})")
                    save_candidate(name=name, grade=cand["grade"], score=cand["score"],
                                   vcp=cand["vcp"], trend=cand["trend"], catalyst=cand["catalyst"],
                                   curr=curr_price, stop=cand["stop"], tgt=cand["tgt"], rr=cand["rr"],
                                   bought=False, skip_reason="MA40하단")
                    continue
            except Exception as _e:
                print(f"⚠️ MA40 조회 오류 {name}: {_e}")'''

if old in content:
    content = content.replace(old, new, 1)
    print("✅ 수정 완료")
else:
    print("❌ 패턴 미일치")
    sys.exit(1)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
