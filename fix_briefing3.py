import sys

path = sys.argv[1] if len(sys.argv) > 1 else "interface/kiki_briefing.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. _build_briefing_msg 시작부 — nbot 읽기 제거
old1 = '''    now    = now_kst()
    state  = read_state("nbot")
    status = state.get("last_status", {})
    cbot_state  = read_state("cbot")'''
new1 = '''    now    = now_kst()
    cbot_state  = read_state("cbot")'''

# 2. "단타봇" 표시 줄 제거
old2 = '''    paused_str = "⏸️" if state.get("paused") else "▶️"
    msg += f"📈 단타봇: {paused_str} | 기준:{state.get('score_enter', 55)}점"
    if status:
        msg += f" | 주문가능:{status.get('psbl_cash', 0):,}원"
    # ★ sbot 추가
    sbot_state2  = read_state("sbot")'''
new2 = '''    # ★ sbot 추가
    sbot_state2  = read_state("sbot")'''

# 3. 스윙봇 다음에 sbo2 줄 추가
old3 = '''    msg += (f"\\n📊 스윙봇: {sbot_paused2} | 포지션:{sbot_pos2}개"
            f"{f' | 평가손익:{sbot_profit2:+,}원' if sbot_profit2 else ''}\\n")
    msg += (f"\\n🪙 코인봇: {'⏸️' if cbot_state.get('paused') else '▶️'} | "
            f"KRW:{cbot_status.get('krw', 0):,}원\\n")'''
new3 = '''    msg += (f"\\n📊 스윙봇: {sbot_paused2} | 포지션:{sbot_pos2}개"
            f"{f' | 평가손익:{sbot_profit2:+,}원' if sbot_profit2 else ''}\\n")
    # ★ sbo2 추가
    sbo2_state  = read_state("sbo2")
    sbo2_status = sbo2_state.get("last_status", {})
    sbo2_pos    = sbo2_status.get("positions", 0)
    sbo2_profit = sbo2_status.get("total_profit", 0)
    sbo2_paused = "⏸️" if sbo2_state.get("paused") else "▶️"
    msg += (f"\\n📊 스윙봇2: {sbo2_paused} | 포지션:{sbo2_pos}개"
            f"{f' | 평가손익:{sbo2_profit:+,}원' if sbo2_profit else ''}\\n")
    msg += (f"\\n🪙 코인봇: {'⏸️' if cbot_state.get('paused') else '▶️'} | "
            f"KRW:{cbot_status.get('krw', 0):,}원\\n")'''

results = []
for name, old, new in [("nbot읽기제거", old1, new1), ("단타봇줄제거", old2, new2), ("sbo2줄추가", old3, new3)]:
    if old in content:
        content = content.replace(old, new, 1)
        results.append(f"✅ {name}")
    else:
        results.append(f"❌ {name} 미일치")

print("\n".join(results))
with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
