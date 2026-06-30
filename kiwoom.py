import os
import time
import json
import asyncio
import requests
import websockets
from dotenv import load_dotenv

load_dotenv()

class KiwoomAPI:
    def __init__(self):
        self.appkey    = os.getenv("KIWOOM_APPKEY", "")
        self.secretkey = os.getenv("KIWOOM_SECRETKEY", "")
        self.account_no = os.getenv("KIWOOM_CANO", "")
        self.token     = ""
        self.token_at  = 0

    def get_token(self) -> str:
        if self.token and time.time() - self.token_at < 82800: return self.token
        res = requests.post("https://api.kiwoom.com/oauth2/token",
                            json={"grant_type": "client_credentials", "appkey": self.appkey, "secretkey": self.secretkey}).json()
        self.token = res.get("token", "")
        self.token_at = time.time()
        print("✅ 토큰 발급 완료")
        return self.token

    def get_account_balance(self) -> list:
        url = "https://api.kiwoom.com/api/dostk/acnt"
        headers = {"authorization": f"Bearer {self.get_token()}", "api-id": "kt00018", "acnt_no": self.account_no}
        body = {"qry_tp": "2", "dmst_stex_tp": "KRX", "acnt_no": self.account_no}
        res = requests.post(url, headers=headers, json=body).json()
        return res.get("acnt_evlt_remn_indv_tot", [])

    async def run_trading_bot(self):
        portfolio = self.get_account_balance()
        if not portfolio:
            print("❌ 보유 종목이 없습니다."); return

        stock_info = {s['stk_cd'].replace('A', ''): {'name': s.get('stk_nm'), 'buy': int(s.get('pur_pric', 0)), 'curr': 0} for s in portfolio}

        print(f"🚀 실시간 모니터링 시작: {list(stock_info.keys())}")

        while True: # 연결 끊김을 대비해 전체 루프를 돔
            try:
                async with websockets.connect("wss://api.kiwoom.com:10000/api/dostk/websocket") as ws:
                    await ws.send(json.dumps({"trnm": "LOGIN", "token": self.get_token()}))
                    await ws.recv() # 로그인 응답 대기
                    res = json.loads(await ws.recv())
                    print(f"서버 초기 응답: {res}")
                    await asyncio.sleep(1)
                    real_codes = [f"A{code}" for code in stock_info.keys()]
                    await ws.send(json.dumps({"trnm": "REAL", "type": "주식체결", "codes": real_codes}))
                    print("✅ 웹소켓 연결 및 구독 성공")
                    
                    last_check = time.time()
                    while True:
                        # 1. 30초 로직 체크
                        if time.time() - last_check >= 30:
                            print(f"\n🕒 {time.strftime('%H:%M:%S')} - 로직 체크:")
                            for code, info in stock_info.items():
                                if info['curr'] > 0:
                                    rate = ((info['curr'] - info['buy']) / info['buy']) * 100
                                    print(f"  - {info['name']}: {info['curr']:,}원 | 수익률 {rate:+.2f}%")
                                else:
                                    print(f"  - {info['name']}: 시세 대기중...")
                            last_check = time.time()

                        # 2. 실시간 시세 수신
                        res = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                        if res.get("type") == "주식체결":
                            code = res.get("code").replace('A', '')
                            curr = abs(int(res.get("price", 0)))
                            if code in stock_info: stock_info[code]['curr'] = curr
            
            except (websockets.exceptions.ConnectionClosed, asyncio.TimeoutError):
                print("⚠️ 연결 끊김, 5초 후 재연결 시도...")
                await asyncio.sleep(5)
            except Exception as e:
                print(f"❌ 오류 발생: {e}"); break

if __name__ == "__main__":
    bot = KiwoomAPI()
    asyncio.run(bot.run_trading_bot())