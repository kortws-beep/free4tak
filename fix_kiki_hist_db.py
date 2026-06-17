import sys

path = sys.argv[1] if len(sys.argv) > 1 else "interface/kiki.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

results = []

old1 = '''TRADE_HIST_DB = "trade_history.db"
SBOT_HIST_DB  = "sbot_trade_history.db"'''
new1 = '''SBOT_HIST_DB  = "sbot_trade_history.db"
SBO2_HIST_DB  = os.path.join("lina_bot", "sbo2_trades.db")'''
if old1 in content:
    content = content.replace(old1, new1, 1)
    results.append("✅ DB 경로 정의")
else:
    results.append("❌ DB 경로 정의 미일치")

old2 = '''def get_open_positions_from_db(bot: str = "nbot") -> list:
    """DB의 미청산 매수 건"""
    db = TRADE_HIST_DB if bot == "nbot" else SBOT_HIST_DB'''
new2 = '''def get_open_positions_from_db(bot: str = "sbot") -> list:
    """DB의 미청산 매수 건"""
    db = SBO2_HIST_DB if bot == "sbo2" else SBOT_HIST_DB
    table = "sbo2_trades" if bot == "sbo2" else "trades"'''
if old2 in content:
    content = content.replace(old2, new2, 1)
    results.append("✅ get_open_positions_from_db 시그니처")
else:
    results.append("❌ get_open_positions_from_db 미일치")

print("\n".join(results))
with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
