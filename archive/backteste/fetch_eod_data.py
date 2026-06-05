import os
import sqlite3
import pandas as pd
from datetime import datetime

# 경로 설정
DB_PATH = "backteste/eod_backtest.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS eod_targets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT,
            code TEXT,
            name TEXT,
            price REAL,
            change_rate REAL,
            volume REAL,
            value REAL
        )
    """)
    conn.commit()
    conn.close()

def save_targets(targets):
    """키움 검색식에서 넘어온 종목들을 저장"""
    if not targets: return
    
    init_db()
    conn = sqlite3.connect(DB_PATH)
    today = datetime.now().strftime('%Y-%m-%d')
    
    for t in targets:
        conn.execute("""
            INSERT INTO eod_targets (date, code, name, price, change_rate, volume, value)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (today, t['code'], t['name'], t['price'], t['change'], t['volume'], t['value']))
    
    conn.commit()
    conn.close()
    print(f"✅ {len(targets)}개 종목 백테스트 DB 저장 완료")