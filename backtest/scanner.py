import sqlite3
import pandas as pd
import os

# 원석 님의 '진짜' 데이터가 들어있는 10MB 파일 경로
DB_PATH = "/home/free4tak/k-bot/stock_bot/backtest_data.db"

def scan_breakout_stocks():
    print(f"📡 연결 대상 DB: {DB_PATH}")
    
    if not os.path.exists(DB_PATH):
        print(f"❌ DB 파일이 존재하지 않습니다: {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    
    # 테이블 목록 확인
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()
    table_names = [t[0] for t in tables]
    print(f"📂 DB 내 테이블 목록: {table_names}")

    if 'daily_ohlcv' not in table_names:
        print("❌ 에러: 'daily_ohlcv' 테이블이 이 DB에 없습니다!")
        print("💡 수집기(fetch_history_fdr.py)가 다른 DB 파일을 쓰고 있는지 확인이 필요합니다.")
        conn.close()
        return

    # 데이터 로드
    try:
        query = "SELECT DISTINCT code FROM daily_ohlcv"
        codes = [row[0] for row in conn.execute(query).fetchall()]
        print(f"✅ 총 {len(codes)}개 종목 로드 완료. 스캔 시작...")

        results = []
        for code in codes:
            df = pd.read_sql(
                "SELECT date, high, close FROM daily_ohlcv WHERE code = ? ORDER BY date",
                conn, params=(code,)
            )
            if len(df) < 250: continue
            
            high_52w = df['high'].max()
            current_price = df['close'].iloc[-1]
            
            if (high_52w * 0.95) <= current_price <= high_52w:
                results.append({"code": code, "price": current_price, "gap": ((high_52w - current_price)/high_52w)*100})
        
        df_res = pd.DataFrame(results)
        if not df_res.empty:
            print("\n🎯 [발굴 결과] 신고가 근접 종목:")
            print(df_res.sort_values("gap"))
        else:
            print("\n📭 신고가 근접 종목이 없습니다.")

    except Exception as e:
        print(f"❌ 스캔 중 에러: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    scan_breakout_stocks()