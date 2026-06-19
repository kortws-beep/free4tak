import sys

path = sys.argv[1] if len(sys.argv) > 1 else "core/kis_api.py"

with open(path, 'r') as f:
    content = f.read()

old = '''    def sell(self, code: str, qty: int, price: int = 0) -> bool:
        """
        시간대별 매도:
        - 08:00~08:30 프리장  : ORD_DVSN=61 (시간외단일가)
        - 09:00~15:30 정규장  : ORD_DVSN=01 (시장가)
        - 15:40~16:00 애프터  : ORD_DVSN=62 (시간외단일가)
        """
        now_t = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime("%H%M")
        if "0800" <= now_t < "0830":
            ord_dvsn = "61"   # 프리장 시간외단일가
            ord_unpr = str(price) if price > 0 else "0"
        elif "1540" <= now_t < "1600":
            ord_dvsn = "62"   # 애프터 시간외단일가
            ord_unpr = str(price) if price > 0 else "0"
        else:
            ord_dvsn = "01"   # 정규장 시장가
            ord_unpr = "0"
        url  = f"{self.base_url}/uapi/domestic-stock/v1/trading/order-cash"
        data = {"CANO": self.cano, "ACNT_PRDT_CD": self.acnt,
                "PDNO": code, "ORD_QTY": str(qty),
                "ORD_UNPR": ord_unpr, "ORD_DVSN": ord_dvsn}
        headers = {"authorization": f"Bearer {self.token}",
                   "appkey": self.appkey, "appsecret": self.secret,
                   "tr_id": "TTTC0801U", "hashkey": self.get_hashkey(data)}
        try:
            res = requests.post(url, headers=headers, data=json.dumps(data), timeout=10).json()
            if res.get("rt_cd") == "0":
                print(f"✅ 매도 성공 {code} | {ord_dvsn} | {qty}주")
                return True
            else:
                print(f"❌ 매도 실패 {code}: {res.get('msg1', '알 수 없는 오류')}"); return False
        except Exception as e:
            print(f"❌ 매도 요청 예외 {code}: {e}"); return False'''

new = '''    def sell(self, code: str, qty: int, price: int = 0) -> bool:
        """
        시간대별 매도 (실제 장 시간 기준):
        - 08:00~09:00 프리장      : NEXT종목만 거래 가능 — ORD_DVSN=62 (시간외단일가)
        - 09:00~15:30 정규장      : ORD_DVSN=01 (시장가)
        - 15:30~18:00 시간외단일가: ORD_DVSN=62 (가격 필요)
        - 18:00~20:00 시간외프리장: NEXT종목만 거래 가능 — ORD_DVSN=62
        """
        now_t = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime("%H%M")
        if "0800" <= now_t < "0900":
            ord_dvsn = "62"   # 프리장 — NEXT 종목만 (시간외단일가)
            ord_unpr = str(price) if price > 0 else "0"
        elif "1530" <= now_t < "1800":
            ord_dvsn = "62"   # 시간외단일가
            ord_unpr = str(price) if price > 0 else "0"
        elif "1800" <= now_t < "2000":
            ord_dvsn = "62"   # 시간외프리장 — NEXT 종목만
            ord_unpr = str(price) if price > 0 else "0"
        else:
            ord_dvsn = "01"   # 정규장 시장가 (09:00~15:30)
            ord_unpr = "0"
        url  = f"{self.base_url}/uapi/domestic-stock/v1/trading/order-cash"
        data = {"CANO": self.cano, "ACNT_PRDT_CD": self.acnt,
                "PDNO": code, "ORD_QTY": str(qty),
                "ORD_UNPR": ord_unpr, "ORD_DVSN": ord_dvsn}
        headers = {"authorization": f"Bearer {self.token}",
                   "appkey": self.appkey, "appsecret": self.secret,
                   "tr_id": "TTTC0801U", "hashkey": self.get_hashkey(data)}
        try:
            res = requests.post(url, headers=headers, data=json.dumps(data), timeout=10).json()
            if res.get("rt_cd") == "0":
                print(f"✅ 매도 성공 {code} | {ord_dvsn} | {qty}주")
                return True
            else:
                print(f"❌ 매도 실패 {code}: {res.get('msg1', '알 수 없는 오류')}"); return False
        except Exception as e:
            print(f"❌ 매도 요청 예외 {code}: {e}"); return False'''

if old in content:
    content = content.replace(old, new, 1)
    with open(path, 'w') as f:
        f.write(content)
    print("✅ 패치 완료")
else:
    print("❌ 패턴 미일치 — 기존 sell() 함수 내용이 다릅니다. 수동 확인 필요")
