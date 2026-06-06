import os
import sqlite3
import pandas as pd
import time
from anthropic import Anthropic
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed

load_dotenv()
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

if not ANTHROPIC_API_KEY:
    print("⚠️ .env 파일에 ANTHROPIC_API_KEY가 없습니다. 실행을 중단합니다.")
    exit()

client = Anthropic(api_key=ANTHROPIC_API_KEY)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "backtest", "data", "backtest_data.db")

def init_sector_table(conn):
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS daily_leading_sectors (
            date TEXT PRIMARY KEY,
            sectors TEXT
        )
    """)
    conn.commit()

def get_daily_top_stocks(conn, date_str, limit=20):
    query = f"""
        SELECT code, close, volume, change 
        FROM daily_ohlcv 
        WHERE date = '{date_str}' AND change > 0
        ORDER BY (close * volume) DESC 
        LIMIT {limit}
    """
    return pd.read_sql_query(query, conn)

def ask_claude_for_sectors(date_str, top_stocks_df):
    if top_stocks_df.empty:
        return "데이터없음"
    
    stock_list_text = "\n".join([
        f"- {row['code']} (변동: {row['change']})" 
        for _, row in top_stocks_df.iterrows()
    ])
    
    prompt = f"""
    아래는 {date_str} 한국 증시에서 거래대금이 크게 터지며 상승한 주도주 20개입니다.
    
    {stock_list_text}
    
    이 종목들을 분석하여 오늘 시장의 가장 강력한 주도 테마/섹터 3개만 추출하세요.
    반드시 쉼표(,)로 구분된 단어만 출력하세요. 다른 설명이나 서론은 절대 금지입니다. (예: 반도체, 화장품, 전력설비)
    """
    
    # ★ 429 에러 발생 시 자동 재시도 로직 (최대 5번)
    max_retries = 5
    for attempt in range(max_retries):
        try:
            response = client.messages.create(
                model="claude-haiku-4-5-20251001",  # 원석님이 찾으신 모델명 적용!
                system="당신은 한국 주식 모멘텀 트레이딩 전문가입니다. Output ONLY comma-separated keywords.",
                max_tokens=50,
                temperature=0.1,
                messages=[{"role": "user", "content": prompt}]
            )
            return response.content[0].text.strip()
        except Exception as e:
            err_msg = str(e)
            if '429' in err_msg or 'rate_limit' in err_msg:
                # 에러가 나면 5초 쉬고 다시 시도합니다.
                time.sleep(5)
                continue
            else:
                return f"추출실패:{e}"
                
    return "추출실패:RateLimit반복"

def build_sector_database():
    print("="*60)
    print("🚀 Claude Haiku 4.5 연동: 주도 섹터 DB 구축 (속도 조절 적용)")
    print("="*60)
    
    conn = sqlite3.connect(DB_PATH)
    init_sector_table(conn)
    
    done_dates = [row[0] for row in conn.execute("SELECT date FROM daily_leading_sectors").fetchall()]
    dates_df = pd.read_sql_query("SELECT DISTINCT date FROM market_meta ORDER BY date", conn)
    
    tasks = []
    print("📊 DB에서 주도주 데이터 추출 중...")
    for date_str in dates_df['date']:
        if date_str not in done_dates:
            df = get_daily_top_stocks(conn, date_str)
            if not df.empty:
                tasks.append((date_str, df))
                
    print(f"🔥 총 {len(tasks)}일치 데이터 분석을 시작합니다!")

    # ★ 1분에 50개 제한(약 1.2초당 1개)을 지키기 위해 동시에 2개씩만 천천히 쏩니다.
    MAX_WORKERS = 2  
    
    def worker(task):
        date_str, df = task
        sectors = ask_claude_for_sectors(date_str, df)
        # 안전장치: 요청 간에 1.5초를 강제로 쉬어줍니다.
        time.sleep(1.5)
        return date_str, sectors

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_date = {executor.submit(worker, task): task[0] for task in tasks}
        
        for future in as_completed(future_to_date):
            date_str = future_to_date[future]
            try:
                _, sectors = future.result()
                conn.execute("INSERT INTO daily_leading_sectors (date, sectors) VALUES (?, ?)", (date_str, sectors))
                conn.commit()
                print(f"✅ [{date_str}] {sectors}")
            except Exception as e:
                print(f"❌ [{date_str}] 에러 발생: {e}")

    conn.close()
    print("🎉 초고속 주도 섹터 DB 구축 완료!")

if __name__ == "__main__":
    build_sector_database()