import os
import requests
from dotenv import load_dotenv

load_dotenv()
APP_KEY = os.getenv("KIS_APPKEY")
APP_SECRET = os.getenv("KIS_SECRET")
URL_BASE = "https://openapi.koreainvestment.com:9443"

def get_token():
    url = f"{URL_BASE}/oauth2/tokenP"
    body = {"grant_type": "client_credentials", "appkey": APP_KEY, "appsecret": APP_SECRET}
    return requests.post(url, json=body).json().get("access_token")

def check_list():
    token = get_token()
    # 내 조건식 목록을 가져오는 TR ID입니다.
    headers = {
        "authorization": f"Bearer {token}",
        "appKey": APP_KEY, "appSecret": APP_SECRET,
        "tr_id": "HHKST03900300", # 목록 조회용
        "custtype": "P"
    }
    params = {"user_id": "youngam9"}
    
    res = requests.get(f"{URL_BASE}/uapi/domestic-stock/v1/quotations/psearch-title", headers=headers, params=params)
    data = res.json()
    
    print("\n🔍 [영암9님의 검색식 리스트 현황]")
    print("-" * 40)
    for item in data.get('output', []):
        # 여기서 'seq' 번호와 'title' 이름을 확인하세요!
        print(f"번호(seq): {item['seq']}  |  이름: {item['p_snm']}")
    print("-" * 40)

if __name__ == "__main__":
    check_list()
