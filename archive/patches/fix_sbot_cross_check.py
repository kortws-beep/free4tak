import sys

path = sys.argv[1] if len(sys.argv) > 1 else "bots/sbot.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old = '''            # ★ nbot 교차 보유 방지
            try:
                from common_utils import read_state as _read_state
                nbot_st  = _read_state("nbot")
                nbot_pos = set(nbot_st.get("last_status", {}).get("positions_detail", {}).keys())
            except Exception:
                nbot_pos = set()
            if code in nbot_pos:
                print(f"⛔ {code} nbot 보유 중 — sbot 매수 제외")
                continue'''

new = '''            # ★ sbo2 교차 보유 방지 (구 nbot 참조 — 경로/구조 모두 sbo2에 맞게 수정)
            try:
                import os as _os, json as _json
                _sbo2_state_path = _os.path.join(
                    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                    "lina_bot", "sbo2_state.json")
                sbo2_pos = set()
                if _os.path.exists(_sbo2_state_path):
                    with open(_sbo2_state_path, "r", encoding="utf-8") as _f:
                        sbo2_pos = set(_json.load(_f).get("positions", {}).keys())
            except Exception as _e:
                print(f"⚠️ sbo2 포지션 조회 오류: {_e}")
                sbo2_pos = set()
            if code in sbo2_pos:
                print(f"⛔ {code} sbo2 보유 중 — sbot 매수 제외")
                continue'''

if old in content:
    content = content.replace(old, new, 1)
    print("✅ 수정 완료")
else:
    print("❌ 패턴 미일치")
    sys.exit(1)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
