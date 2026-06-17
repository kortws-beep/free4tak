import sys

path = sys.argv[1] if len(sys.argv) > 1 else "core/kis_api.py"

with open(path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

# "def sell(self, code: str, qty: int, price: int = 0) -> bool:" 라인 찾기
start_idx = None
for i, line in enumerate(lines):
    if "def sell(self, code: str, qty: int, price: int = 0)" in line:
        start_idx = i
        break

if start_idx is None:
    print("❌ sell 함수 시작 라인을 찾을 수 없습니다")
    sys.exit(1)

# 함수 끝 찾기 — 다음 "    def " 또는 "    # ====" 라인 전까지
end_idx = None
for i in range(start_idx + 1, len(lines)):
    stripped = lines[i].rstrip("\n")
    if stripped.startswith("    def ") or stripped.startswith("    # ==="):
        end_idx = i
        break

if end_idx is None:
    print("❌ sell 함수 끝을 찾을 수 없습니다")
    sys.exit(1)

print(f"sell 함수 범위: {start_idx+1} ~ {end_idx} 라인 ({end_idx - start_idx}줄)")

new_func = '''    def sell(self, code: str, qty: int, price: int = 0) -> bool:
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
            print(f"❌ 매도 요청 예외 {code}: {e}"); return False

'''

new_lines = lines[:start_idx] + [new_func] + lines[end_idx:]

with open(path, 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print("✅ 패치 완료")
