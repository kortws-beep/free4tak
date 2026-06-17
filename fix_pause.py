import sys

path = sys.argv[1] if len(sys.argv) > 1 else "interface/kiki_cmd.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

results = []

old1 = '''    labels = {"sbot2": "단타봇", "sbot": "스윙봇", "cbot": "코인봇"}'''
new1 = '''    labels = {"sbot": "스윙봇", "sbo2": "스윙봇2", "cbot": "코인봇"}'''
if old1 in content:
    content = content.replace(old1, new1, 1)
    results.append("✅ labels 딕셔너리")
else:
    results.append("❌ labels 미일치")

old2 = '''        sbot2_st = read_state("sbot2")
        mkt_status = sbot2_st.get("last_status", {}).get("market_status", "normal")
        mkt_rate   = sbot2_st.get("last_status", {}).get("market_rate", 0)'''
new2 = '''        sbot_st = read_state("sbot")
        mkt_status = sbot_st.get("last_status", {}).get("market_status", "normal")
        mkt_rate   = sbot_st.get("last_status", {}).get("market_rate", 0)'''
if old2 in content:
    content = content.replace(old2, new2, 1)
    results.append("✅ 시장상태 조회 sbot2→sbot")
else:
    results.append("❌ 시장상태 조회 미일치")

print("\n".join(results))
with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
