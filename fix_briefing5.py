import sys

path = sys.argv[1] if len(sys.argv) > 1 else "interface/kiki_briefing.py"

with open(path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# "state  = read_state(\"nbot\")" 라인과 다음 "status = state.get(...)" 라인 찾아서 제거
remove_idx = []
for i, line in enumerate(lines):
    if 'state  = read_state("nbot")' in line:
        remove_idx.append(i)
        # 다음 줄이 status = state.get(...) 인지 확인
        if i+1 < len(lines) and 'status = state.get("last_status"' in lines[i+1]:
            remove_idx.append(i+1)

print(f"제거 대상 라인: {[i+1 for i in remove_idx]}")
for i in remove_idx:
    print(f"  [{i+1}] {lines[i].rstrip()}")

new_lines = [line for i, line in enumerate(lines) if i not in remove_idx]

with open(path, 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print("✅ 제거 완료")
