import os
import time
import json
import requests
import datetime
import re
from dotenv import load_dotenv
from anthropic import Anthropic

load_dotenv()

class KIS10_RealTrade:

    def __init__(self):
        self.appkey = os.getenv("KIS_APPKEY")
        self.secret = os.getenv("KIS_SECRET")
        self.cano = os.getenv("KIS_CANO")
        self.acnt = os.getenv("KIS_ACNT_PRDT_CD")
        self.discord = os.getenv("DISCORD_WEBHOOK_URL")

        self.base_url = "https://openapi.koreainvestment.com:9443"

        self.llm = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

        self.token = self.get_token()
        self.positions = {}

        self.limit_per_stock = 190000

        print("🚀 [영암9 LIVE] 실전 엔진 최종버전 가동")

    # =========================
    # 🔑 인증
    # =========================
    def get_token(self):
        url = f"{self.base_url}/oauth2/tokenP"
        body = {
            "grant_type": "client_credentials",
            "appkey": self.appkey,
            "appsecret": self.secret
        }
        return requests.post(url, json=body).json().get("access_token", "")

    def get_hashkey(self, data):
        url = f"{self.base_url}/uapi/hashkey"
        headers = {
            "Content-Type": "application/json",
            "appkey": self.appkey,
            "appsecret": self.secret
        }
        res = requests.post(url, headers=headers, data=json.dumps(data))
        return res.json().get("HASH", "")

    # =========================
    # 💵 주문가능금액 (정답 API)
    # =========================
    def get_buyable_cash(self, test_code="005930"):
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-psbl-order"

        headers = {
            "authorization": f"Bearer {self.token}",
            "appkey": self.appkey,
            "appsecret": self.secret,
            "tr_id": "TTTC8908R"
        }

        params = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt,
            "PDNO": test_code,
            "ORD_UNPR": "0",
            "ORD_DVSN": "01",
            "CMA_EVLU_AMT_ICLD_YN": "N",
            "OVRS_ICLD_YN": "N"
        }

        try:
            res = requests.get(url, headers=headers, params=params).json()
            cash = res.get("output", {}).get("ord_psbl_cash", "0")
            return int(float(cash))
        except:
            return 0

    # =========================
    # 📦 보유 종목
    # =========================
    def get_current_positions(self):
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-balance"

        headers = {
            "authorization": f"Bearer {self.token}",
            "appkey": self.appkey,
            "appsecret": self.secret,
            "tr_id": "TTTC8434R"
        }

        params = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt,
            "AFHR_FLG": "N",
            "INQR_DVSN": "01",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "00"
        }

        try:
            res = requests.get(url, headers=headers, params=params).json()
            pos_dict = {}

            for item in res.get('output1', []):
                qty = int(item.get('hldg_qty', 0))
                if qty <= 0:
                    continue

                code = item.get('pdno')

                avg_price = float(item.get('pchs_avg_pric', 0))
                if avg_price <= 0:
                    pbuy_amt = float(item.get('pchs_amt', 0))
                    avg_price = pbuy_amt / qty if qty > 0 else 0

                pos_dict[code] = {
                    "entry_price": avg_price,
                    "qty": qty
                }

            return pos_dict

        except:
            return {}

    # =========================
    # 📊 시세 조회
    # =========================
    def get_market_data(self, code):
        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-price"

        headers = {
            "authorization": f"Bearer {self.token}",
            "appkey": self.appkey,
            "appsecret": self.secret,
            "tr_id": "FHKST01010100"
        }

        params = {
            "FID_COND_MRKT_DIV_CODE": "J",
            "FID_INPUT_ISCD": code
        }

        return requests.get(url, headers=headers, params=params).json().get("output")

    # =========================
    # 🤖 AI 분석
    # =========================
    def get_ai_score(self, code, data):
        model_id = "claude-haiku-4-5-20251001"
        prpr = data.get('stck_prpr'); ctrt = data.get('prdy_ctrt'); vol = data.get('acml_vol')
        prompt = f"종목:{code}, 현재가:{prpr}, 등락:{ctrt}%, 거래량:{vol}. 0~100 점수 JSON 출력: {{\"score\":0~100,\"reason\":\"\"}}"

        try:
            res = self.llm.messages.create(
                model=model_id,
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}]
            )
            text = res.content[0].text
            # 💡 정규식으로 점수와 이유를 모두 추출합니다.
            score_match = re.search(r'"score":\s*(\d+)', text)
            reason_match = re.search(r'"reason":\s*"([^"]+)"', text)
            
            return {
                "score": int(score_match.group(1)) if score_match else 0,
                "reason": reason_match.group(1) if reason_match else "분석중"
            }
        except:
            return {"score": 0, "reason": "AI 통신오류"}

    # =========================
    # 🟢 매수
    # =========================
    def buy(self, code, price):
        if len(self.positions) >= 5:
            return

        if code in self.positions:
            return

        qty = int(self.limit_per_stock / price)
        if qty <= 0:
            return

        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/order-cash"

        data = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt,
            "PDNO": code,
            "ORD_DVSN": "01",
            "ORD_QTY": str(qty),
            "ORD_UNPR": "0"
        }

        headers = {
            "Content-Type": "application/json",
            "authorization": f"Bearer {self.token}",
            "appkey": self.appkey,
            "appsecret": self.secret,
            "tr_id": "TTTC0802U",
            "custtype": "P",
            "hashkey": self.get_hashkey(data)
        }

        res = requests.post(url, headers=headers, data=json.dumps(data))

        if res.json().get("rt_cd") == "0":
            self.notify(f"✅ 매수 {code} {qty}주")

    # =========================
    # 🔴 매도
    # =========================
    def sell(self, code, qty, reason):
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/order-cash"

        data = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt,
            "PDNO": code,
            "ORD_DVSN": "01",
            "ORD_QTY": str(qty),
            "ORD_UNPR": "0"
        }

        headers = {
            "Content-Type": "application/json",
            "authorization": f"Bearer {self.token}",
            "appkey": self.appkey,
            "appsecret": self.secret,
            "tr_id": "TTTC0801U",
            "custtype": "P",
            "hashkey": self.get_hashkey(data)
        }

        res = requests.post(url, headers=headers, data=json.dumps(data))

        if res.json().get("rt_cd") == "0":
            self.notify(f"💰 매도 {code} | {reason}")

    # =========================
    def notify(self, msg):
        print(msg)
        if self.discord:
            requests.post(self.discord, json={"content": msg})

    def get_condition_codes(self):
        try:
            url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/psearch-title"

            headers = {
                "authorization": f"Bearer {self.token}",
                "appkey": self.appkey,
                "appsecret": self.secret,
                "tr_id": "HHKST03900400"
            }

            params = {
                "user_id": os.getenv("KIS_USER_ID")  # youngam9
            }

            res = requests.get(url, headers=headers, params=params).json()

            codes = []
            for item in res.get("output", []):
                code = item.get("pdno")
                if code:
                    codes.append(code)

            return codes

        except Exception as e:
            print("조건검색 오류:", e)
            return []            
        
    def get_turnover_top100(self):
        """
        거래대금 TOP100 생성 (실전형)
        거래대금 = 현재가 * 거래량
        """

        try:
            url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-price"

            headers = {
                "authorization": f"Bearer {self.token}",
                "appkey": self.appkey,
                "appsecret": self.secret,
                "tr_id": "FHKST01010100"
            }

            # ⚠️ 현실적으로 전체 종목 리스트 필요
            codes = self.get_all_stock_codes()

            results = []

            for i, code in enumerate(codes):
                try:
                    params = {
                        "FID_COND_MRKT_DIV_CODE": "J",
                        "FID_INPUT_ISCD": code
                    }

                    res = requests.get(url, headers=headers, params=params).json()
                    data = res.get("output")

                    if not data:
                        continue

                    price = float(data.get("stck_prpr", 0))
                    volume = float(data.get("acml_vol", 0))

                    if price <= 0 or volume <= 0:
                        continue

                    turnover = price * volume

                    results.append((code, turnover, price))

                    # 🔥 속도 제한 (API 보호)
                    if i % 10 == 0:
                        time.sleep(0.2)

                except:
                    continue

            # 🔥 거래대금 정렬
            results.sort(key=lambda x: x[1], reverse=True)

            top100 = results[:100]

            return [x[0] for x in top100]

        except Exception as e:
            print("거래대금 TOP100 실패:", e)
            return []

    def get_all_stock_codes(self):
        """
        거래대금 TOP100용 전체 종목 리스트 (안정 fallback)
        """

        try:
            # 🔥 가장 안정적인 방법: KRX 고정 리스트 (실전용)
            return [
            "005930","000660","035420","051910","006400",
            "005380","000270","035720","068270","207940",
            "105560","055550","012330","034730","096770",
            "017670","032830","003550","086790","090430",
            "247540","373220","323410","028260","091990",
            "018260","251270","068760","095910","035900",
            "039490","091700","036570","078600","196170",
            "293490","357780","402340","418470","000720",
            "064350","011200","010130","030200","010120",
            "042660","003490","034020","086280","011070",
            "352820","361610","402030","403870","412350",
            "417310","418550","424760","430690","432320",
            "450080","452260","456040","457190","460930",
            "462520","465320","468760","471080","475830" ]

        except:
            return []        

    def get_turnover_top100(self):
        """
        🚀 API 없이 TOP100 생성 (실전 안정형)
        - 거래대금 = price * volume
        - 변동성 + 거래량 기반 점수화
        """

        try:
            print("⚠️ API 없이 TOP100 생성 (stable mode)")

            codes = self.get_all_stock_codes()

            results = []

            for i, code in enumerate(codes):

                try:
                    data = self.get_market_data(code)
                    if not data:
                        continue

                    price = float(data.get("stck_prpr", 0))
                    volume = float(data.get("acml_vol", 0))
                    change = float(data.get("prdy_ctrt", 0) or 0)

                    if price <= 0 or volume <= 0:
                        continue

                    # 🔥 핵심: 거래대금 + 변동성 점수
                    turnover = price * volume
                    volatility = abs(change)

                    score = turnover * (1 + volatility / 10)

                    results.append((code, score))

                    # 🔥 속도 보호
                    if i % 10 == 0:
                        time.sleep(0.11)

                except:
                    continue

            # 🔥 정렬
            results.sort(key=lambda x: x[1], reverse=True)

            top100 = [x[0] for x in results[:100]]

            print(f"🔥 stable TOP100 생성 완료: {len(top100)}개")

            return top100

        except Exception as e:
            print("TOP100 stable 실패:", e)
            return []

    # =========================
    # 🚀 메인 루프 (튜닝 적용)
    # =========================
    def run(self):

        print("🚀 [영암9 LIVE] 최종 안정 엔진 가동")

        while True:
            try:
                now = datetime.datetime.now()

                if not (8 <= now.hour < 20 or (now.hour == 15 and now.minute <= 20)):
                    print("💤 장외")
                    time.sleep(60)
                    continue

                # =========================
                # 🔥 TOP100 → fallback 구조
                # =========================
                codes = self.get_turnover_top100()

                if not codes or len(codes) < 20:
                    print("⚠️ TOP100 실패 → fallback 150 사용")
                    codes = self.get_fallback_codes()

                print(f"\n⏰ {now.strftime('%H:%M:%S')} | 종목수:{len(codes)}")

                self.positions = self.get_current_positions()
                cash = self.get_buyable_cash()

                # =========================
                # 🔥 스캔
                # =========================
                for i, code in enumerate(codes):

                    try:
                        data = self.get_market_data(code)
                        if not data:
                            continue

                        price = float(data.get('stck_prpr', 0) or 0)

                        # =========================
                        # 💰 보유 종목 관리
                        # =========================
                        if code in self.positions:
                            entry = self.positions[code]["entry_price"]
                            qty = self.positions[code]["qty"]

                            if entry > 0:
                                profit = (price - entry) / entry

                                print(f"💰 {code} | {profit*100:+.2f}%")

                                if profit >= 0.05:
                                    self.sell(code, qty, "익절")
                                elif profit <= -0.03:
                                    self.sell(code, qty, "손절")

                        # =========================
                        # 👀 신규 매수
                        # =========================
                        else:

                            if cash < self.limit_per_stock:
                                continue

                            ai = self.get_ai_score(code, data)
                            score = ai["score"]
                            reason = ai.get("reason", "분석중")

                            print(f"👀 {code} | {score}점 | {reason[:20]}...")
                            
                            # 🔥 핵심: 점수 다양화 + 필터
                            if score >= 75:
                                self.buy(code, price)
                                cash -= self.limit_per_stock

                        time.sleep(0.1)

                    except Exception as e:
                        print("🚨 종목 오류:", e)
                        continue

                time.sleep(3)

            except Exception as e:
                print("🚨 루프 오류:", e)
                time.sleep(5)

if __name__ == "__main__":
    KIS10_RealTrade().run()                