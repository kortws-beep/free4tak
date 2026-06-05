"""
fetch_history_kis.py — 한국투자증권(KIS) API 기반 과거 데이터 수집
================================================================
[설명]
- 마스터의 기존 KIS API 인증 구조를 활용하여 과거 일봉 데이터 수집.
- 한투 API는 1회당 최대 100영업일만 제공하므로, 과거로 거슬러 올라가는 페이징 로직 포함.
- 수정주가(액면분할 등 반영) 데이터를 기본으로 가져옴.
"""

import os
import time
import sqlite3
import requests
import argparse
import datetime
from dotenv import load_dotenv

# ============================================================
# 상위 폴더의 .env 파일 명시적 로드
# ============================================================
# 1. 현재 파일(fetch_history_kis.py)이 있는 폴더 경로
_current_dir = os.path.dirname(os.path.abspath(__file__))

# 2. 한 단계 상위 폴더 경로
_parent_dir = os.path.dirname(_current_dir)

# 3. 상위 폴더의 .env 경로 지정
_env_path = os.path.join(_parent_dir, ".env")

# 4. 해당 경로의 .env 로드
load_dotenv(dotenv_path=_env_path)

# ============================================================
# 설정 및 DB 초기화
# ============================================================
DB_PATH = "data/backtest_data.db"
BASE_URL = "https://openapi.koreainvestment.com:9443"

def init_db(db_path):
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_ohlcv (
            code     TEXT NOT NULL,
            date     TEXT NOT NULL,
            open     REAL,
            high     REAL,
            low      REAL,
            close    REAL,
            volume   INTEGER,
            value    INTEGER,
            change   REAL,
            PRIMARY KEY (code, date)
        )
    """)
    conn.commit()
    return conn

# ============================================================
# KIS API 인증 및 데이터 수집
# ============================================================
def get_kis_token():
    appkey = os.getenv("KIS_APPKEY")
    secret = os.getenv("KIS_SECRET")
    url = f"{BASE_URL}/oauth2/tokenP"
    res = requests.post(url, json={
        "grant_type": "client_credentials", 
        "appkey": appkey, 
        "appsecret": secret
    }).json()
    return res.get("access_token"), appkey, secret

def fetch_kis_ohlcv(code: str, start_date: str, end_date: str, token: str, appkey: str, secret: str):
    """
    한투 API (FHKST03010100) 호출하여 일봉 데이터 가져오기 (최대 100일 분량)
    날짜 형식: YYYYMMDD
    """
    url = f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {token}",
        "appKey": appkey,
        "appSecret": secret,
        "tr_id": "FHKST03010100"
    }
    params = {
        "fid_cond_mrkt_div_code": "J",
        "fid_input_iscd": code,
        "fid_input_date_1": start_date,  # 시작일
        "fid_input_date_2": end_date,    # 종료일
        "fid_period_div_code": "D",      # D: 일봉
        "fid_org_adj_prc": "0"           # 0: 수정주가 반영
    }
    
    try:
        res = requests.get(url, headers=headers, params=params, timeout=5).json()
        if res.get("rt_cd") != "0":
            print(f"  ⚠️ API 응답 에러 ({code}): {res.get('msg1')}")
            return []
        return res.get("output2", [])
    except Exception as e:
        print(f"  ⚠️ 요청 예외 ({code}): {e}")
        return []

def collect_data_with_pagination(conn, code, start_date, end_date, token, appkey, secret):
    """100일 단위 한계를 극복하기 위해 과거로 이동하며 데이터 수집 및 DB 저장"""
    current_end = end_date.replace("-", "")
    target_start = start_date.replace("-", "")
    
    total_saved = 0
    
    while current_end >= target_start:
        # API 호출
        data_chunk = fetch_kis_ohlcv(code, target_start, current_end, token, appkey, secret)
        if not data_chunk:
            break
            
        oldest_date_in_chunk = current_end
        rows_to_insert = []
        
        for item in data_chunk:
            dt_str = item.get("stck_bsop_date")
            if not dt_str: continue
            
            # 응답 중 가장 과거 날짜 추적
            if dt_str < oldest_date_in_chunk:
                oldest_date_in_chunk = dt_str
                
            # 목표 시작일보다 과거면 스킵
            if dt_str < target_start:
                continue
                
            # DB 형식에 맞게 변환 (YYYY-MM-DD)
            formatted_date = f"{dt_str[:4]}-{dt_str[4:6]}-{dt_str[6:]}"
            
            rows_to_insert.append((
                code,
                formatted_date,
                float(item.get("stck_oprc", 0)),   # 시가
                float(item.get("stck_hgpr", 0)),   # 고가
                float(item.get("stck_lwpr", 0)),   # 저가
                float(item.get("stck_clpr", 0)),   # 종가
                int(item.get("acml_vol", 0)),      # 거래량
                int(item.get("acml_tr_pbmn", 0)),  # 거래대금
                float(item.get("prdy_ctrt", 0))    # 등락률
            ))
            
        # DB 저장 (UPSERT)
        if rows_to_insert:
            conn.executemany("""
                INSERT INTO daily_ohlcv (code, date, open, high, low, close, volume, value, change)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(code, date) DO UPDATE SET
                    open=excluded.open, high=excluded.high, low=excluded.low, 
                    close=excluded.close, volume=excluded.volume, 
                    value=excluded.value, change=excluded.change
            """, rows_to_insert)
            conn.commit()
            total_saved += len(rows_to_insert)
        
        # 100개를 다 못 채웠다는 것은 지정된 기간의 데이터를 모두 받았다는 의미
        if len(data_chunk) < 100:
            break
            
        # 다음 루프를 위해 종료일을 '가장 과거 날짜의 전날'로 설정
        oldest_dt = datetime.datetime.strptime(oldest_date_in_chunk, "%Y%m%d")
        next_end_dt = oldest_dt - datetime.timedelta(days=1)
        current_end = next_end_dt.strftime("%Y%m%d")
        
        # API 초당 호출 제한 방지 (한투 1초당 20건 제한 고려)
        time.sleep(0.1)
        
    return total_saved

# ============================================================
# 메인 실행부
# ============================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--codes", type=str, required=True, help="종목코드 콤마구분 (예: 005930,000660)")
    parser.add_argument("--start", type=str, default="2023-01-01", help="시작일 (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, default=datetime.datetime.now().strftime("%Y-%m-%d"), help="종료일 (YYYY-MM-DD)")
    args = parser.parse_args()

    codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    
    print("🔑 KIS 토큰 발급 중...")
    token, appkey, secret = get_kis_token()
    if not token:
        print("❌ 토큰 발급 실패. .env 파일을 확인하세요.")
        exit(1)
        
    conn = init_db(DB_PATH)
    print(f"📡 한투 API 데이터 수집 시작: {args.start} ~ {args.end}")
    
    for i, code in enumerate(codes, 1):
        print(f"  [{i}/{len(codes)}] {code} 수집 중...", end="", flush=True)
        try:
            saved_cnt = collect_data_with_pagination(conn, code, args.start, args.end, token, appkey, secret)
            print(f" ✅ {saved_cnt}일치 저장 완료")
        except Exception as e:
            print(f" ❌ 실패: {e}")
            
    print("\n🎉 모든 데이터 수집 및 DB 저장 완료!")