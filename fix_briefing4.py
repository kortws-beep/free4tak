import sys

path = sys.argv[1] if len(sys.argv) > 1 else "interface/kiki_briefing.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. 더미 폴백 함수 수정
old1 = "    def get_today_realized_all(): return {'nbot':0,'sbot':0,'cbot':0}"
new1 = "    def get_today_realized_all(): return {'sbot':0,'sbo2':0,'cbot':0}"

# 2. 저녁 브리핑 시작부 — nbot 읽기 제거
old2 = '''    now    = now_kst()
    state  = read_state("nbot")
    status = state.get("last_status", {})
    cbot_state  = read_state("cbot")
    cbot_status = cbot_state.get("last_status", {})
    today_date = now.strftime("%Y-%m-%d")
    searches = [
        ("📈 코스피/코스닥", "코스피 코스닥 오늘 마감 시황",                       "korea"),'''
new2 = '''    now    = now_kst()
    cbot_state  = read_state("cbot")
    cbot_status = cbot_state.get("last_status", {})
    today_date = now.strftime("%Y-%m-%d")
    searches = [
        ("📈 코스피/코스닥", "코스피 코스닥 오늘 마감 시황",                       "korea"),'''

# 3. 실현손익 합산 표시 — nbot → sbo2
old3 = '''    realized = get_today_realized_all()
    nbot_p = realized.get("nbot", 0)
    sbot_p = realized.get("sbot", 0)
    cbot_p = realized.get("cbot", 0)
    if nbot_p:
        msg += f"📈 단타봇: {int(nbot_p):+,}원\\n"
    if sbot_p:
        msg += f"📊 스윙봇: {int(sbot_p):+,}원\\n"
    if cbot_p:
        msg += f"🪙 코인봇: {int(cbot_p):+,}원\\n"
    total = nbot_p + sbot_p + cbot_p
    if total or any([nbot_p, sbot_p, cbot_p]):
        msg += f"━━━━━━━━━━━━━━━━━━━━\\n"
        msg += f"💰 **오늘 합계: {int(total):+,}원**\\n"
    else:
        # 평가손익 표시 (실현 매매가 없을 때)
        msg += f"📈 단타봇 평가: {status.get('total_profit', 0):+,}원\\n"
        msg += f"🪙 코인봇 평가: {cbot_status.get('total_profit', 0):+,}원\\n"'''
new3 = '''    realized = get_today_realized_all()
    sbot_p = realized.get("sbot", 0)
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
        msg += f"🪙 코인봇 평가: {cbot_status.get('total_profit', 0):+,}원\\n"'''

# 4. nbot 오전/오후 임계치 권장 텍스트 — sbot 기준으로 변경 (AI 프롬프트 부분)
old4 = '''                "2. nbot 오전(09-11시) 매수 임계치 권장: 70~85 중 숫자만\\n"
                "3. nbot 오후(11-15시) 매수 임계치 권장: 65~80 중 숫자만\\n"'''
new4 = '''                "2. sbot 오전(09-11시) 매수 임계치 권장: 70~85 중 숫자만\\n"
                "3. sbot 오후(11-15시) 매수 임계치 권장: 65~80 중 숫자만\\n"'''

results = []
for name, old, new in [("더미함수", old1, new1), ("저녁브리핑시작", old2, new2),
                         ("실현손익표시", old3, new3), ("AI프롬프트텍스트", old4, new4)]:
    if old in content:
        content = content.replace(old, new, 1)
        results.append(f"✅ {name}")
    else:
        results.append(f"❌ {name} 미일치")

print("\n".join(results))
with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
