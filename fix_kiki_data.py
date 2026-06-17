import sys

path = sys.argv[1] if len(sys.argv) > 1 else "interface/kiki_data.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old = '''def get_today_realized_all() -> dict:
    """오늘 실현손익 — 봇별 합산"""
    import sqlite3, datetime
    today  = datetime.date.today().strftime("%Y-%m-%d")
    result = {"nbot": 0, "sbot": 0, "cbot": 0}
    dbs    = {
        "nbot": os.path.join(_base, "trade_history.db"),
        "sbot": os.path.join(_base, "sbot_trade_history.db"),
        "cbot": os.path.join(_base, "cbot_trade_history.db"),
    }
    for bot, db_path in dbs.items():
        if not os.path.exists(db_path):
            continue
        try:
            conn = sqlite3.connect(db_path, timeout=5)
            conn.execute("PRAGMA query_only = ON")
            rows = conn.execute("""
                SELECT buy_price, sell_price, qty FROM trades
                WHERE sell_price IS NOT NULL
                  AND sell_price > 0
                  AND date(sell_time) = ?
            """, (today,)).fetchall()
            conn.close()
            result[bot] = sum((r[1]-r[0])*r[2] for r in rows)
        except Exception:
            pass'''

new = '''def get_today_realized_all() -> dict:
    """오늘 실현손익 — 봇별 합산"""
    import sqlite3, datetime
    today  = datetime.date.today().strftime("%Y-%m-%d")
    result = {"sbot": 0, "sbo2": 0, "cbot": 0}
    dbs    = {
        "sbot": (os.path.join(_base, "sbot_trade_history.db"), "trades",
                 "buy_price", "sell_price", "qty", "sell_time"),
        "sbo2": (os.path.join(_base, "lina_bot", "sbo2_trades.db"), "sbo2_trades",
                 "buy_price", "sell_price", "qty", "sell_time"),
        "cbot": (os.path.join(_base, "cbot_trade_history.db"), "trades",
                 "buy_price", "sell_price", "qty", "sell_time"),
    }
    for bot, (db_path, table, buy_col, sell_col, qty_col, time_col) in dbs.items():
        if not os.path.exists(db_path):
            continue
        try:
            conn = sqlite3.connect(db_path, timeout=5)
            conn.execute("PRAGMA query_only = ON")
            rows = conn.execute(f"""
                SELECT {buy_col}, {sell_col}, {qty_col} FROM {table}
                WHERE {sell_col} IS NOT NULL
                  AND {sell_col} > 0
                  AND date({time_col}) = ?
            """, (today,)).fetchall()
            conn.close()
            result[bot] = sum((r[1]-r[0])*r[2] for r in rows)
        except Exception:
            pass'''

if old in content:
    content = content.replace(old, new, 1)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("✅ get_today_realized_all 수정 완료")
else:
    print("❌ 패턴 미일치")
