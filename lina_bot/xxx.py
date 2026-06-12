import sqlite3
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE_DIR, "us_kr_mapping.db")

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()
cursor.execute("DELETE FROM us_kr_mapping WHERE us_ticker = 'X'")
conn.commit()
print(f"X 티커 제거 완료! ({cursor.rowcount}건 삭제)")
conn.close()