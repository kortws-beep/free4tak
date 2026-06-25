import sys

path = sys.argv[1] if len(sys.argv) > 1 else "core/kis_api.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old = '''        for _retry in range(3):
            try:
                res = requests.get(url, headers=headers, params=params, timeout=10).json()
                pos = {}
                for item in res.get("output1", []):
                    qty = int(item.get("hldg_qty", 0))
                    if qty <= 0: continue
                    code = item.get("pdno")
                    name = item.get("prdt_name", "")
                    # ETF 필터 (코드 6자리 숫자 아니면 제외)
                    if not code.isdigit() or any(s in name for s in _etf_skip):
                        print(f'⚠️ 포지션 제외 (ETF/기타): {code} {name}')
                        continue
                    avg  = float(item.get("pchs_avg_pric", 0))
                    pos[code] = {"entry_price": avg, "qty": qty}
                if pos:
                    # ★ 정상값만 캐시 갱신
                    self._pos_cache = pos
                    self._pos_cache_ts = time.time()
                    return pos
                else:
                    if _retry < 2:
                        print(f"⚠️ 잔고 빈값 — 재시도 {_retry+1}/3")
                        time.sleep(1)
                    else:
                        if hasattr(self, '_pos_cache') and self._pos_cache:
                            print(f"⚠️ 잔고 빈값 — 이전 캐시 유지 ({len(self._pos_cache)}종목)")
                            return self._pos_cache
                        return {}'''

new = '''        for _retry in range(3):
            try:
                res = requests.get(url, headers=headers, params=params, timeout=10).json()
                # ★ API 응답 자체의 성공/실패 여부를 rt_cd로 먼저 판별
                #   (rt_cd!="0" = 진짜 API 실패 → 재시도/캐시유지, 호출측엔 None 반환
                #    rt_cd=="0"인데 output1이 비어있음 = 정상적으로 보유종목 0개 → {} 반환)
                rt_cd = res.get("rt_cd", "")
                if rt_cd != "0":
                    print(f"⚠️ 잔고조회 API 실패(rt_cd={rt_cd}): {res.get('msg1','')}")
                    if _retry < 2:
                        print(f"⚠️ 잔고 빈값 — 재시도 {_retry+1}/3")
                        time.sleep(1)
                        continue
                    else:
                        if hasattr(self, '_pos_cache') and self._pos_cache:
                            print(f"⚠️ 잔고조회 API 실패 — 이전 캐시 유지 ({len(self._pos_cache)}종목)")
                            return self._pos_cache
                        return None   # ★ 진짜 실패 — 호출측이 캐시 유지 판단할 수 있게 None

                pos = {}
                for item in res.get("output1", []):
                    qty = int(item.get("hldg_qty", 0))
                    if qty <= 0: continue
                    code = item.get("pdno")
                    name = item.get("prdt_name", "")
                    # ETF 필터 (코드 6자리 숫자 아니면 제외)
                    if not code.isdigit() or any(s in name for s in _etf_skip):
                        print(f'⚠️ 포지션 제외 (ETF/기타): {code} {name}')
                        continue
                    avg  = float(item.get("pchs_avg_pric", 0))
                    pos[code] = {"entry_price": avg, "qty": qty}

                # ★ rt_cd=="0"(API 정상응답) → pos가 비어도 "진짜 0종목"으로 확정, 캐시 갱신
                self._pos_cache = pos
                self._pos_cache_ts = time.time()
                return pos'''

if old in content:
    content = content.replace(old, new, 1)
    print("✅ 수정 완료")
else:
    print("❌ 패턴 미일치")
    sys.exit(1)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
