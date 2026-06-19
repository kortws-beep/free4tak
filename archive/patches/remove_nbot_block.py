import sys

path = sys.argv[1] if len(sys.argv) > 1 else "interface/kiki_briefing.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old = '''        msg += f"⚙️ **nbot 권장**: 오전 {am_thresh}점 / 오후 {pm_thresh}점\\n"
        # ★ nbot 자동 조정 (bot_state.json에 권장 임계치 저장)
        state = read_state("nbot")
        state["am_score_recommend"] = am_thresh
        state["pm_score_recommend"] = pm_thresh
        state["market_forecast"]    = forecast
        state["forecast_date"]      = today_date
        write_state("nbot", state)
        msg += f"✅ nbot 권장 임계치 저장 완료\\n"'''

new = ''  # 완전 제거

if old in content:
    content = content.replace(old, new, 1)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("✅ nbot 블록 제거 완료")
else:
    print("❌ 패턴 미일치 — 수동 확인 필요")
