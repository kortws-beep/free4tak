import sqlite3
import pandas as pd
from feature_builder import DataLoader

def get_hot_sectors(conn):
    """현재 거래대금이 가장 많은 상위 3개 테마를 DB에서 가져옴"""
    query = "SELECT theme_nm FROM sector_flow ORDER BY flow_rate DESC LIMIT 3"
    return [row[0] for row in conn.execute(query).fetchall()]

def run_realtime_scanner(db_path):
    conn = sqlite3.connect(db_path)
    loader = DataLoader(db_path)
    
    # 1. 핫한 섹터 3개 뽑기
    hot_sectors = get_hot_sectors(conn)
    print(f"🔥 현재 주도 섹터: {hot_sectors}")
    
    # 2. 신고가 돌파 후보 찾기
    for sector in hot_sectors:
        codes = get_codes_in_theme(sector) # 기존 theme_codes 활용
        for code in codes:
            df = loader.load_ohlcv(code)
            if df.empty: continue
            
            # 52주 신고가 로직
            high_52w = df['high'].rolling(250).max().iloc[-1]
            current = df['close'].iloc[-1]
            
            # 신고가 5% 이내 접근 시 출력
            if current >= high_52w * 0.95:
                print(f"🎯 [발굴] 섹터:{sector} | 종목:{code} | 신고가근접({current:.0f}원)")

    conn.close()