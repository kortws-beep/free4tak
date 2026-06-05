import os
import time
import sqlite3
import pandas as pd
import requests
from datetime import datetime

# 경로 설정
BASE_DIR = "."
DB_PATH = "coin_backtest.db"

def get_all_krw_symbols():
    url = "https://api.upbit.com/v1/market/all"
    res = requests.get(url).json()
    return [item['market'] for item in res if item['market'].startswith("KRW-")]

def fetch_ohlcv(symbol, interval="minutes/240", count=200):
    url = f"https://api.upbit.com/v1/candles/{interval}"
    params = {"market": symbol, "count": count}
    try:
        res = requests.get(url, params=params)
        data = res.json()
        df = pd.DataFrame(data)
        df = df[['market', 'candle_date_time_kst', 'opening_price', 'high_price', 'low_price', 'trade_price', 'candle_acc_trade_volume']]
        df.columns = ['code', 'date', 'open', 'high', 'low', 'close', 'volume']
        return df
    except:
        return None

def save_to_db(df):
    if df is None or df.empty: return
    conn = sqlite3.connect(DB_PATH)
    df.to_sql('daily_ohlcv', conn, if_exists='append', index=False, method='multi')
    conn.execute("DELETE FROM daily_ohlcv WHERE rowid NOT IN (SELECT MIN(rowid) FROM daily_ohlcv GROUP BY code, date)")
    conn.commit()
    conn.close()

if __name__ == "__main__":
    print("🚀 코인 데이터 수집 공장 가동!")
    symbols = get_all_krw_symbols()
    for i, symbol in enumerate(symbols):
        print(f"[{i+1}/{len(symbols)}] {symbol} 가져오는 중...", end="\r")
        df = fetch_ohlcv(symbol)
        save_to_db(df)
        time.sleep(0.15)
    print("\n✅ 수집 완료! coin_backtest.db 파일이 생성되었습니다.")
