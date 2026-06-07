import sqlite3
conn = sqlite3.connect("/home/free4tak/k-bot/stock_bot/data/backtest_data.db")
cursor = conn.cursor()
cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
print(f"📂 발견된 테이블들: {cursor.fetchall()}")
conn.close()