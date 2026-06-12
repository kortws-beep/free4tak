import sqlite3
import pandas as pd
import os

# DB 경로 설정 (collect_daily_data.py와 동일한 위치)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE_DIR, "kr_theme_finance.db")

def view_recent_data(limit=15):
    if not os.path.exists(DB_PATH):
        print(f"❌ DB 파일을 찾을 수 없습니다: {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    
    # 가장 최근 날짜 기준으로 정렬하여 데이터 가져오기
    query = f"""
        SELECT date, stock_name, close_price, foreign_net_buy, institution_net_buy 
        FROM kr_stock_daily_data 
        WHERE stock_name = '이마트'
        ORDER BY date DESC 
        LIMIT 100;
    """
    
    # pandas를 이용해 데이터프레임으로 읽기 (출력이 깔끔함)
    try:
        df = pd.read_sql_query(query, conn)
        print(f"📊 최근 적재된 데이터 Top {limit}\n")
        print(df.to_string(index=False))
    except Exception as e:
        print(f"❌ 데이터 조회 중 오류 발생: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    view_recent_data(1000) # 15줄 출력 (원하는 만큼 숫자 변경 가능)