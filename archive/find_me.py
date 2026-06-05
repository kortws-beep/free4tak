import os
import requests
import json
import time
from anthropic import Anthropic
from dotenv import load_dotenv

# 1. 환경 변수 로드
load_dotenv()

APP_KEY = os.getenv("KIS_APPKEY")
APP_SECRET = os.getenv("KIS_SECRET")
USER_ID = "youngam9" # 재미나니님의 한투 ID (대소문자 확인 필수!)
DISCORD_WEBHOOK = os.getenv("DISCORD_WEBHOOK_URL")
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY")

URL_BASE = "https://openapi.koreainvestment.com:9443"

class KikiBot:
    def __init__(self):
        self.access_token = self.get_access_token()
        self.ai_client = Anthropic(api_key=ANTHROPIC_KEY)

    def get_access_token(self):
        """한투 실전 Access Token 발급"""
        url = f"{URL_BASE}/oauth2/tokenP"
        body = {"grant_type": "client_credentials", "appkey": APP_KEY, "appsecret": APP_SECRET}
        res = requests.post(url, json=body)
        return res.json().get("access_token")

    def fetch_search_results(self, seq="0"):
        """종목조건검색 결과 조회 (tr_id를 0400으로 교체하여 정밀 타격)"""
        path = "/uapi/domestic-stock/v1/quotations/psearch-result"
        url = f"{URL_BASE}/{path}"
        
        headers = {
            "Content-Type": "application/json",
            "authorization": f"Bearer {self.access_token}",
            "appKey": APP_KEY,
            "appSecret": APP_SECRET,
            "tr_id": "HHKST03900400", # 실시간용 TR_ID로 테스트
            "custtype": "P"
        }
        
        params = {
            "user_id": USER_ID,
            "seq": seq
        }
        
        print(f"📡 [{seq}번] 검색식 데이터 요청 중...")
        res = requests.get(url, headers=headers, params=params)
        data = res.json()
        
        # [중요] 서버의 응답을 날것 그대로 출력해서 원인을 파악합니다.
        print(f"🔍 서버 응답 원본: {data}")
        
        if data.get('rt_cd') != '0':
            print(f"❌ 실패 사유: {data.get('msg1')}")
            return []
            
        return data.get('output2', []) # 0400 사용 시 결과는 output2에 담길 수 있음

    def analyze_and_report(self, stocks):
        """Claude에게 분석 맡기고 디스코드 쏘기"""
        if not stocks:
            print("💡 분석할 종목이 없어 종료합니다.")
            return

        stock_info = ", ".join([f"{s['itms_nm']}({s['stck_prpr']}원)" for s in stocks[:10]])
        prompt = f"당신은 주식 고수 youngam9님의 비서입니다. [{stock_info}] 종목 중 대장주를 골라 리포트를 써주세요."
        
        print("🤖 Claude AI 분석 중...")
        message = self.ai_client.messages.create(
            model="claude-3-5-sonnet-20240620",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}]
        )
        
        # 디스코드 전송
        report = message.content[0].text
        payload = {"content": f"🎯 **오늘의 키키봇 리포트**\n\n{report}"}
        requests.post(DISCORD_WEBHOOK, json=payload)
        print("✨ 디스코드 전송 완료!")

    def run(self):
        print("="*50)
        print(f"🚀 {USER_ID} 키키봇 통합 가동")
        print("="*50)
        
        # 0번부터 2번까지 순차적으로 찔러봅니다.
        for s in ["0", "1", "2"]:
            stocks = self.fetch_search_results(seq=s)
            if stocks:
                print(f"✅ {s}번에서 {len(stocks)}개 종목 발견!")
                self.analyze_and_report(stocks)
                break # 하나라도 찾으면 종료
            time.sleep(1.5) # TPS 방지

if __name__ == "__main__":
    bot = KikiBot()
    bot.run()
