import sys

path = sys.argv[1] if len(sys.argv) > 1 else "bots/sbot.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old = '''                new_pos = self.api.get_current_positions()
                if not new_pos and self.positions:
                    print("⚠️ 실계좌 잔고 빈값 — API 헬스체크")
                    self._check_api_health(False)
                else:
                    self._check_api_health(True)
                # ★ 수동매도 감지 — 이전 포지션에 있었는데 실계좌에 없으면 감지
                # ★ 수동매도는 재매수 허용 — sold_today 등록 안 함
                for _code in list(self.positions.keys()):
                    if _code not in new_pos and _code not in self.sold_today:
                        print(f"🔍 수동매도 감지: {_code} → 재매수 허용")
                self.positions.clear()
                self.positions.update(new_pos)'''

new = '''                new_pos = self.api.get_current_positions()
                # ★ None = 진짜 API 조회 실패 / {} = 정상응답인데 보유종목 0개 (구분 필수!)
                if new_pos is None:
                    print("⚠️ 실계좌 잔고 조회 실패 — 캐시(기존 positions) 유지, 이번 루프 동기화 스킵")
                    self._check_api_health(False)
                else:
                    self._check_api_health(True)
                    # ★ 수동매도 감지 — 이전 포지션에 있었는데 실계좌에 없으면 감지
                    # ★ 수동매도는 재매수 허용 — sold_today 등록 안 함
                    for _code in list(self.positions.keys()):
                        if _code not in new_pos and _code not in self.sold_today:
                            print(f"🔍 수동매도 감지: {_code} → 재매수 허용")
                    self.positions.clear()
                    self.positions.update(new_pos)'''

if old in content:
    content = content.replace(old, new, 1)
    print("✅ 수정 완료")
else:
    print("❌ 패턴 미일치")
    sys.exit(1)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
