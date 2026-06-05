import sqlite3
import pandas as pd
import time
import requests
import json
import os
from dotenv import load_dotenv
from datetime import datetime

# 1. 열쇠 가져오기
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
APP_KEY = os.getenv("KIS_APPKEY")
APP_SECRET = os.getenv("KIS_SECRET") 
URL_BASE = "https://openapi.koreainvestment.com:9443"

def get_access_token():
    """티켓 창구에서 티켓 한 장 사오기"""
    headers = {"content-type": "application/json"}
    body = {"grant_type": "client_credentials", "appkey": APP_KEY, "appsecret": APP_SECRET}
    res = requests.post(f"{URL_BASE}/oauth2/tokenP", headers=headers, data=json.dumps(body))
    data = res.json()
    return data.get("access_token")

def fetch_and_save(code, start_date, token):
    conn = sqlite3.connect('data/backtest_data.db')
    headers = {
        "Content-Type": "application/json",
        "authorization": f"Bearer {token}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
        "tr_id": "FHKST01010400"
    }
    
    # 📅 기간을 아주 넉넉하게, 그리고 오늘 날짜를 자동으로 계산합니다.
    today = datetime.now().strftime("%Y%m%d")
    params = {
        "fid_cond_mrkt_div_code": "J",
        "fid_input_iscd": code,
        "fid_input_date_1": start_date.replace("-", ""),
        "fid_input_date_2": today,
        "fid_org_adj_prc": "1",
        "fid_period_div_code": "D"
    }

    res = requests.get(f"{URL_BASE}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice", 
                       headers=headers, params=params)
    
    res_json = res.json()
    
    # 🔍 [특수 조치] 주머니(output2)가 비어있으면 아예 서버 응답을 그대로 사용
    data_list = res_json.get('output2', [])
    
    # 만약 output2가 비어있다면, 서버가 보낸 원본 데이터에서 리스트 형태인 걸 강제로 찾습니다.
    if not data_list:
        for key, value in res_json.items():
            if isinstance(value, list) and len(value) > 0:
                data_list = value
                break

    if not data_list:
        print(f"❌ {code}: 여전히 데이터를 못 찾았습니다. 응답내용: {res_json}")
        return

    print(f"📦 {code}: {len(data_list)}건의 데이터를 DB에 쏟아붓는 중...")
    
    clean_start_date = start_date.replace("-", "")

    for d in data_list:
        # 날짜 키값이 다를 경우를 대비해 여러 이름을 체크합니다.
        dt = d.get('stck_bsop_date') or d.get('stck_cntg_hour') 
        if not dt or dt < clean_start_date: continue
        
        # 1. 가격 데이터 (daily_ohlcv)
        conn.execute("""
            INSERT OR REPLACE INTO daily_ohlcv (code, date, open, high, low, close, volume, change)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (code, dt, 
              int(d.get('stck_oprc', 0)), int(d.get('stck_hgpr', 0)), 
              int(d.get('stck_lwpr', 0)), int(d.get('stck_clpr', 0)), 
              int(d.get('acml_vol', 0)), float(d.get('prdy_ctrt', 0))))

        # 2. 수급 데이터 (daily_flow)
        # 외국인 순매수(frgn_ntby_qty)가 없으면 0으로 처리하는 방어 로직
        f_qty = d.get('frgn_ntby_qty') or d.get('frgn_ntsav_cntg_qty') or 0
        conn.execute("""
            INSERT OR REPLACE INTO daily_flow (code, date, foreign_qty, orgn_qty, prsn_qty)
            VALUES (?, ?, ?, ?, ?)
        """, (code, dt, int(f_qty), 0, 0))

    conn.commit()
    conn.close()
    print(f"✅ {code}: 드디어 성공! DB 저장 완료.")

if __name__ == "__main__":
    # 🏁 1. 토큰은 딱 한 번만!
    print("🎫 토큰 발급 시도 중...")
    my_token = get_access_token()
    
    if my_token:
        # 🏁 2. 그 토큰으로 종목들 릴레이!
        target_codes = ["137400", "078600", "005930"] 
        for c in target_codes:
            fetch_and_save(c, "20230101", my_token)
            time.sleep(1) # 한투 서버 진정용
    else:
        print("🚨 여전히 단속 중입니다! 1분만 딱 참고 다시 실행해 주세요!")