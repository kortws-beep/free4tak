import sys

path = sys.argv[1] if len(sys.argv) > 1 else "interface/kiki_cmd.py"

with open(path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

start_idx = None
end_idx = None
for i, line in enumerate(lines):
    if line.strip().startswith("async def cmd_buy(ctx"):
        start_idx = i
    if start_idx is not None and line.strip().startswith("async def cmd_analyze"):
        end_idx = i
        break

if start_idx is None or end_idx is None:
    print(f"❌ 범위를 찾을 수 없음 (start={start_idx}, end={end_idx})")
    sys.exit(1)

print(f"제거 범위: {start_idx+1} ~ {end_idx} 라인")

new_lines = lines[:start_idx] + lines[end_idx:]

with open(path, 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print("✅ cmd_buy 함수 제거 완료")
