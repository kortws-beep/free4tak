import sys

path = sys.argv[1] if len(sys.argv) > 1 else "interface/kiki.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old = '''    db = SBO2_HIST_DB if bot == "sbo2" else SBOT_HIST_DB
    table = "sbo2_trades" if bot == "sbo2" else "trades"
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

new = '''    db = SBO2_HIST_DB if bot == "sbo2" else SBOT_HIST_DB
    table = "sbo2_trades" if bot == "sbo2" else "trades"
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

if old in content:
    content = content.replace(old, new, 1)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("✅ 수정 완료")
else:
    print("❌ 패턴 미일치")
