import sys

path = sys.argv[1] if len(sys.argv) > 1 else "interface/kiki.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old = '''def get_today_realized_all() -> dict:
    """★ 신규: 모든 봇의 오늘 실현손익 합계"""
    today  = today_str()
    result = {"nbot": 0, "sbot": 0, "cbot": 0}
    db_map = {
        "nbot": TRADE_HIST_DB,
        "sbot": SBOT_HIST_DB,
        "cbot": CBOT_HIST_DB,
    }
    for bot_name, db in db_map.items():
        if not os.path.exists(db):
            continue
        try:
            conn = _ro_connect(db)
            if bot_name == "cbot":
                # cbot은 profit_krw 컬럼 사용
                rows = conn.execute("""
                    SELECT profit_krw FROM trades
                    WHERE sell_price IS NOT NULL AND sell_time >= ?
                """, (today,)).fetchall()
                result[bot_name] = sum(int(r[0] or 0) for r in rows)
            else:
                rows = conn.execute("""
                    SELECT buy_price, sell_price, qty FROM trades
                    WHERE sell_price IS NOT NULL AND sell_time >= ?
                """, (today,)).fetchall()
                result[bot_name] = sum(
                    int((sp - bp) * qty) for bp, sp, qty in rows
                    if sp is not None and bp is not None
                )
            conn.close()
        except Exception:
            pass'''

new = '''def get_today_realized_all() -> dict:
    """★ 신규: 모든 봇의 오늘 실현손익 합계"""
    today  = today_str()
    result = {"sbot": 0, "sbo2": 0, "cbot": 0}
    db_map = {
        "sbot": (SBOT_HIST_DB, "trades"),
        "sbo2": (SBO2_HIST_DB, "sbo2_trades"),
        "cbot": (CBOT_HIST_DB, "trades"),
    }
    for bot_name, (db, table) in db_map.items():
        if not os.path.exists(db):
            continue
        try:
            conn = _ro_connect(db)
            if bot_name == "cbot":
                # cbot은 profit_krw 컬럼 사용
                rows = conn.execute(f"""
                    SELECT profit_krw FROM {table}
                    WHERE sell_price IS NOT NULL AND sell_time >= ?
                """, (today,)).fetchall()
                result[bot_name] = sum(int(r[0] or 0) for r in rows)
            else:
                rows = conn.execute(f"""
                    SELECT buy_price, sell_price, qty FROM {table}
                    WHERE sell_price IS NOT NULL AND sell_time >= ?
                """, (today,)).fetchall()
                result[bot_name] = sum(
                    int((sp - bp) * qty) for bp, sp, qty in rows
                    if sp is not None and bp is not None
                )
            conn.close()
        except Exception:
            pass'''

if old in content:
    content = content.replace(old, new, 1)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("✅ 수정 완료")
else:
    print("❌ 패턴 미일치")
