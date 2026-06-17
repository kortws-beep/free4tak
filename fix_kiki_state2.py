import sys

path = sys.argv[1] if len(sys.argv) > 1 else "interface/kiki.py"

with open(path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

start_idx = None
end_idx = None
for i, line in enumerate(lines):
    if 'def read_state(bot: str = "nbot")' in line:
        start_idx = i
    if 'def update_state(bot: str = "nbot"' in line:
        # 이 함수의 본문 끝(다음 def 또는 빈 줄 패턴)까지 찾기
        for j in range(i+1, len(lines)):
            if lines[j].strip().startswith('def ') and 'update_state' not in lines[j]:
                end_idx = j
                break
        break

if start_idx is None or end_idx is None:
    print(f"❌ 범위를 찾을 수 없음 (start={start_idx}, end={end_idx})")
    sys.exit(1)

print(f"교체 범위: {start_idx+1} ~ {end_idx} 라인")
for i in range(start_idx, min(end_idx, start_idx+20)):
    print(f"  [{i+1}] {lines[i].rstrip()}")

new_block = '''def read_state(bot: str = "sbot") -> dict:
    """봇 상태 파일 읽기 (없으면 기본값)"""
    fname = BOT_STATE_FILES.get(bot)
    if not fname:
        return {}
    return _read_state_atomic(fname, default={
        "paused":      False,
        "score_enter": 70,
        "pending_cmd": None,
        "cmd_result":  None,
        "last_status": None,
    })
def write_state(bot: str = "sbot", state: dict = None):
    """봇 상태 파일 쓰기 (★ atomic — 중간에 죽어도 안 깨짐)"""
    if state is None: state = {}
    fname = BOT_STATE_FILES.get(bot)
    if not fname:
        return
    _write_state_atomic(fname, state)
def update_state(bot: str = "sbot", **kwargs):
    """봇 상태 부분 업데이트"""
    fname = BOT_STATE_FILES.get(bot)
    if not fname:
        return
    _update_state_atomic(fname, **kwargs)
'''

new_lines = lines[:start_idx] + [new_block] + lines[end_idx:]

with open(path, 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print("✅ 교체 완료")
