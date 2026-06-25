import sys

path = sys.argv[1] if len(sys.argv) > 1 else "lina_bot/sbo2.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old = '''        try:
            new_pos = self.api.get_current_positions()
            if not new_pos:
                # 캐시 무효화 후 재시도
                if hasattr(self.api, '_pos_cache'):
                    self.api._pos_cache = {}
                    self.api._pos_cache_ts = 0
                new_pos = self.api.get_current_positions()
            if not new_pos and self.positions:
                print("⚠️ 실계좌 잔고 빈값 — 동기화 스킵 (캐시 유지)")
                self._check_api_health(False)   # ★ API 실패 카운트
                return
            self._check_api_health(True)        # ★ API 정상'''

new = '''        try:
            new_pos = self.api.get_current_positions()
            # ★ None = 진짜 API 조회 실패 / {} = 정상응답인데 보유종목 0개(구분 필요!)
            if new_pos is None:
                # 캐시 무효화 후 재시도
                if hasattr(self.api, '_pos_cache'):
                    self.api._pos_cache = {}
                    self.api._pos_cache_ts = 0
                new_pos = self.api.get_current_positions()
            if new_pos is None:
                print("⚠️ 실계좌 잔고 조회 실패 — 동기화 스킵 (캐시 유지)")
                self._check_api_health(False)   # ★ API 실패 카운트
                return
            self._check_api_health(True)        # ★ API 정상
            # new_pos가 {} (빈 딕셔너리)인 경우 → 진짜로 보유종목 0개. 정상 진행.'''

if old in content:
    content = content.replace(old, new, 1)
    print("✅ 수정 완료")
else:
    print("❌ 패턴 미일치")
    sys.exit(1)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
