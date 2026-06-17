import sys

path = sys.argv[1] if len(sys.argv) > 1 else "interface/kiki_data.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

results = []

old1 = '''TRADE_HIST_DB = os.path.join(_base, "trade_history.db")
SBOT_HIST_DB  = os.path.join(_base, "sbot_trade_history.db")'''
new1 = '''SBOT_HIST_DB  = os.path.join(_base, "sbot_trade_history.db")
SBO2_HIST_DB  = os.path.join(_base, "lina_bot", "sbo2_trades.db")'''
if old1 in content:
    content = content.replace(old1, new1, 1)
    results.append("✅ DB 경로 정의")
else:
    results.append("❌ DB 경로 정의 미일치")

old2 = '''def get_open_positions_from_db(bot: str = "nbot") -> list:
    """DB의 미청산 매수 건"""
    db = TRADE_HIST_DB if bot == "nbot" else SBOT_HIST_DB
    try:
        conn = _ro_connect(db)
        rows = conn.execute("""
            SELECT code, buy_price, qty, ai_score, buy_time
            FROM trades WHERE sell_price IS NULL
            ORDER BY buy_time DESC
        """).fetchall()
        conn.close()
        return rows
    except Exception:
        return []'''
new2 = '''def get_open_positions_from_db(bot: str = "sbot") -> list:
    """DB의 미청산 매수 건"""
    db        = SBO2_HIST_DB if bot == "sbo2" else SBOT_HIST_DB
    table     = "sbo2_trades" if bot == "sbo2" else "trades"
    score_col = "score" if bot == "sbo2" else "ai_score"
    try:
        conn = _ro_connect(db)
        rows = conn.execute(f"""
            SELECT code, buy_price, qty, {score_col}, buy_time
            FROM {table} WHERE sell_price IS NULL
            ORDER BY buy_time DESC
        """).fetchall()
        conn.close()
        return rows
    except Exception:
        return []'''
if old2 in content:
    content = content.replace(old2, new2, 1)
    results.append("✅ get_open_positions_from_db")
else:
    results.append("❌ get_open_positions_from_db 미일치")

print("\n".join(results))
with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
