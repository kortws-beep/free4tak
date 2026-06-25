import sys

path = sys.argv[1] if len(sys.argv) > 1 else "lina_bot/sbo2.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old = '''            # 코드 or 종목명으로 이중 체크
            already_held = (
                code in self.positions or
                any(p.get("name") == name for p in self.positions.values())
            )
            if already_held:
                print(f"⏭️ 이미 보유중: {name}({code}) - 스킵")
                continue'''

new = '''            # 코드 or 종목명으로 이중 체크
            already_held = (
                code in self.positions or
                any(p.get("name") == name for p in self.positions.values())
            )
            if already_held:
                print(f"⏭️ 이미 보유중: {name}({code}) - 스킵")
                continue

            # ★ sbot 교차 보유 방지
            try:
                import os as _os, json as _json
                _sbot_state_path = _os.path.join(
                    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                    "sbot_state.json")
                sbot_pos = set()
                if _os.path.exists(_sbot_state_path):
                    with open(_sbot_state_path, "r", encoding="utf-8") as _f:
                        _sbot_st = _json.load(_f)
                    sbot_pos = set(_sbot_st.get("last_status", {})
                                       .get("positions_detail", {}).keys())
                if code in sbot_pos:
                    print(f"⛔ {name}({code}) sbot 보유 중 — sbo2 매수 제외")
                    continue
            except Exception as _e:
                print(f"⚠️ sbot 포지션 조회 오류: {_e}")'''

if old in content:
    content = content.replace(old, new, 1)
    print("✅ 수정 완료")
else:
    print("❌ 패턴 미일치")
    sys.exit(1)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
