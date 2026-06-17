import sys

path = sys.argv[1] if len(sys.argv) > 1 else "interface/kiki_briefing.py"

with open(path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# "nbot 권장" 라인부터 "nbot 권장 임계치 저장 완료" 라인까지 찾아서 제거
start_idx = None
end_idx = None
for i, line in enumerate(lines):
    if 'nbot 권장**: 오전' in line:
        start_idx = i
    if 'nbot 권장 임계치 저장 완료' in line:
        end_idx = i
        break

if start_idx is None or end_idx is None:
    print(f"❌ 범위를 찾을 수 없음 (start={start_idx}, end={end_idx})")
    sys.exit(1)

print(f"제거 범위: {start_idx+1} ~ {end_idx+1} 라인 ({end_idx-start_idx+1}줄)")
for i in range(start_idx, end_idx+1):
    print(f"  [{i+1}] {lines[i].rstrip()}")

new_lines = lines[:start_idx] + lines[end_idx+1:]

with open(path, 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print("✅ 제거 완료")
