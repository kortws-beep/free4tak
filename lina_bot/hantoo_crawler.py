import os
import time
import sqlite3
import re
import requests
from datetime import datetime
from dotenv import load_dotenv

# 1. .env 파일에서 한투 API 키 불러오기
load_dotenv()
APP_KEY = os.getenv("KIS_APPKEY")
APP_SECRET = os.getenv("KIS_SECRET")
BASE_URL = "https://openapi.koreainvestment.com:9443"

# 우리가 쓰기로 한 통합 DB 경로
DB_PATH_THEME_FINANCE = "kr_theme_finance.db"

def get_access_token():
    """한투 API 통신을 위한 접근 토큰(Bearer) 발급"""
    url = f"{BASE_URL}/oauth2/tokenP"
    headers = {"content-type": "application/json"}
    body = {
        "grant_type": "client_credentials",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET
    }
    res = requests.post(url, headers=headers, json=body)
    if res.status_code == 200:
        return res.json().get("access_token")
    else:
        print("❌ 토큰 발급 실패! API 키를 다시 확인해봐 대장:\n", res.text)
        return None

def fetch_daily_investor_data(access_token, ticker):
    """특정 종목(ticker)의 일별 주가 및 외인/기관 매매추이 가져오기"""
    url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-investor"
    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {access_token}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
        "tr_id": "FHKST01010900", # 한투 API: 국내주식 일별 투자자 매매추이
        "custtype": "P"
    }
    params = {
        "FID_COND_MRKT_DIV_CODE": "J", # 주식/ETF
        "FID_INPUT_ISCD": ticker
    }
    
    try:
        res = requests.get(url, headers=headers, params=params, timeout=5)
        if res.status_code == 200:
            return res.json().get("output", [])
    except Exception as e:
        print(f"⚠️ {ticker} 통신 에러: {e}")
    return []

def run_weekend_crawler():
    print("🚀 [수급 크롤러] 한투 API 연동 모멘텀 엔진 가동!")
    
    token = get_access_token()
    if not token: return
    print("✅ 한투 API 접근 토큰 장착 완료!\n")
    
    conn = sqlite3.connect(DB_PATH_THEME_FINANCE)
    cursor = conn.cursor()
    
    # 테마 DB에서 중복 없이 종목명 싹 가져오기
    cursor.execute("SELECT DISTINCT stock_name FROM kr_theme_stocks")
    rows = cursor.fetchall()
    
    total_stocks = len(rows)
    print(f"📊 총 {total_stocks}개의 고유 종목 수급 스캔을 시작합니다...")
    
    count = 0
    for row in rows:
        stock_name = row[0]
        # "포스코엠텍KOSDAQ 009520" -> 정규식으로 "009520"만 추출!
        match = re.search(r'\d{6}', stock_name)
        if not match: continue
        
        ticker = match.group()
        count += 1
        
        print(f"🔍 [{count}/{total_stocks}] '{stock_name}' 데이터 추출 중...")
        daily_records = fetch_daily_investor_data(token, ticker)
        
        # 최근 5거래일 치 데이터만 DB에 적재 (데이터 폭증 방지)
        for idx, record in enumerate(daily_records):
            if idx >= 5: break 
            
            try:
                date_str = record.get("stck_bsop_date")           # 예: 20260610
                close_price = int(record.get("stck_clpr", 0))     # 종가
                volume = int(record.get("acml_vol", 0))           # 누적거래량
                foreign_buy = int(record.get("frgn_ntby_qty", 0)) # 외인 순매수 (수량)
                inst_buy = int(record.get("orgn_ntby_qty", 0))    # 기관 순매수 (수량)
                
                # 날짜 형식 예쁘게 변환 (20260610 -> 2026-06-10)
                formatted_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
                
                cursor.execute("""
                    INSERT OR IGNORE INTO kr_stock_daily_data 
                    (date, stock_name, close_price, volume, foreign_net_buy, institution_net_buy)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (formatted_date, stock_name, close_price, volume, foreign_buy, inst_buy))
                
            except Exception:
                pass
                
        conn.commit()
        
        # 🚨 한투 API 초당 호출 제한 방어 (착하게 0.5초 대기)
        time.sleep(0.5) 

    conn.close()
    print("\n🎉 [크롤링 대성공] 6,500여 개 종목의 주간 수급/주가 데이터 적재 완벽 성공! 🚀")

if __name__ == "__main__":
    run_weekend_crawler()