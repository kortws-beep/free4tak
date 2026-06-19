import sys

path = sys.argv[1] if len(sys.argv) > 1 else "interface/kiki_briefing.py"

with open(path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

start_idx = None
end_idx = None
for i, line in enumerate(lines):
    if 'nbot_p = realized.get("nbot", 0)' in line:
        start_idx = i
    if '코인봇 평가:' in line:
        end_idx = i
        break

if start_idx is None or end_idx is None:
    print(f"❌ 범위를 찾을 수 없음 (start={start_idx}, end={end_idx})")
    sys.exit(1)

print(f"교체 범위: {start_idx+1} ~ {end_idx+1} 라인")

new_block = '''    sbot_p = realized.get("sbot", 0)
    sbo2_p = realized.get("sbo2", 0)
    cbot_p = realized.get("cbot", 0)
    if sbot_p:
        msg += f"📊 스윙봇: {int(sbot_p):+,}원\\n"
    if sbo2_p:
        msg += f"📊 스윙봇2: {int(sbo2_p):+,}원\\n"
    if cbot_p:
        msg += f"🪙 코인봇: {int(cbot_p):+,}원\\n"
    total = sbot_p + sbo2_p + cbot_p
    if total or any([sbot_p, sbo2_p, cbot_p]):
        msg += f"━━━━━━━━━━━━━━━━━━━━\\n"
        msg += f"💰 **오늘 합계: {int(total):+,}원**\\n"
    else:
        # 평가손익 표시 (실현 매매가 없을 때)
        sbot_state3  = read_state("sbot")
        sbot_status3 = sbot_state3.get("last_status", {})
        sbo2_state3  = read_state("sbo2")
        sbo2_status3 = sbo2_state3.get("last_status", {})
        msg += f"📊 스윙봇 평가: {sbot_status3.get('total_profit', 0):+,}원\\n"
        msg += f"📊 스윙봇2 평가: {sbo2_status3.get('total_profit', 0):+,}원\\n"
        msg += f"🪙 코인봇 평가: {cbot_status.get('total_profit', 0):+,}원\\n"
'''

new_lines = lines[:start_idx] + [new_block] + lines[end_idx+1:]

with open(path, 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print("✅ 교체 완료")
