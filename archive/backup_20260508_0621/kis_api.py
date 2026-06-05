"""
kis_api.py — 한국투자증권 OpenAPI 래퍼
"""
import os
import time
import json
import requests
import datetime


TOKEN_TTL = 86400


class KisAPI:

    def __init__(self, appkey=None, secret=None, cano=None, acnt=None):
        self.appkey   = appkey   or os.getenv("KIS_APPKEY")
        self.secret   = secret   or os.getenv("KIS_SECRET")
        self.cano     = cano     or os.getenv("KIS_CANO")
        self.acnt     = acnt     or os.getenv("KIS_ACNT_PRDT_CD")
        self.base_url = "https://openapi.koreainvestment.com:9443"

        self.token           = self._issue_token()
        self.token_issued_at = time.time()
        self._mkt_cache      = {}

    # ============================================================
    # 토큰
    # ============================================================
    def _issue_token(self) -> str:
        url  = f"{self.base_url}/oauth2/tokenP"
        body = {"grant_type": "client_credentials",
                "appkey": self.appkey, "appsecret": self.secret}
        try:
            token = requests.post(url, json=body).json().get("access_token", "")
            print("✅ 한투 토큰 발급 완료")
            return token
        except Exception as e:
            print(f"❌ 토큰 발급 실패: {e}"); return ""

    def refresh_token_if_needed(self):
        if time.time() - self.token_issued_at > TOKEN_TTL - 3600:
            print("🔄 토큰 갱신 중...")
            self.token           = self._issue_token()
            self.token_issued_at = time.time()

    def get_hashkey(self, data: dict) -> str:
        url = f"{self.base_url}/uapi/hashkey"
        headers = {"Content-Type": "application/json",
                   "appkey": self.appkey, "appsecret": self.secret}
        try:
            return requests.post(url, headers=headers,
                                 data=json.dumps(data)).json().get("HASH", "")
        except Exception as e:
            print(f"⚠️ 해시키 발급 실패: {e}"); return ""

    # ============================================================
    # 계좌 조회
    # ============================================================
    def get_buyable_cash(self) -> int:
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-balance"
        headers = {"authorization": f"Bearer {self.token}",
                   "appkey": self.appkey, "appsecret": self.secret,
                   "tr_id": "TTTC8434R"}
        params  = {"CANO": self.cano, "ACNT_PRDT_CD": self.acnt, "INQR_DVSN": "01"}
        try:
            res     = requests.get(url, headers=headers, params=params).json()
            output2 = res.get("output2", [{}])[0] if res.get("output2") else {}
            cash    = (output2.get("dnca_tot_amt") or
                       output2.get("prvs_rcdl_excc_amt") or
                       output2.get("tot_evlu_amt") or 0)
            return int(float(cash))
        except Exception as e:
            print(f"❌ 예수금 조회 오류: {e}"); return 0

    def get_psbl_order_cash(self, code: str, price: float = 0) -> int:
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-psbl-order"
        headers = {"authorization": f"Bearer {self.token}",
                   "appkey": self.appkey, "appsecret": self.secret,
                   "tr_id": "TTTC8908R"}
        params  = {"CANO": self.cano, "ACNT_PRDT_CD": self.acnt,
                   "PDNO": code, "ORD_UNPR": str(int(price)),
                   "ORD_DVSN": "01", "CMA_EVLU_AMT_ICLD_YN": "N",
                   "OVRS_ICLD_YN": "N"}
        try:
            res    = requests.get(url, headers=headers, params=params).json()
            output = res.get("output", {})
            cash   = (output.get("nrcvb_buy_amt") or
                      output.get("max_buy_amt") or
                      output.get("ord_psbl_cash") or 0)
            result = int(float(cash))
            print(f"   💰 주문가능 {code}: {result:,}원")
            return result
        except Exception as e:
            print(f"❌ 주문가능금액 조회 오류: {e}"); return 0

    def get_current_positions(self) -> dict:
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-balance"
        headers = {"authorization": f"Bearer {self.token}",
                   "appkey": self.appkey, "appsecret": self.secret,
                   "tr_id": "TTTC8434R"}
        params  = {"CANO": self.cano, "ACNT_PRDT_CD": self.acnt, "INQR_DVSN": "01"}
        try:
            res = requests.get(url, headers=headers, params=params).json()
            pos = {}
            for item in res.get("output1", []):
                qty = int(item.get("hldg_qty", 0))
                if qty <= 0: continue
                code = item.get("pdno")
                avg  = float(item.get("pchs_avg_pric", 0))
                pos[code] = {"entry_price": avg, "qty": qty}
            return pos
        except Exception as e:
            print(f"❌ 보유종목 조회 오류: {e}"); return {}

    # ============================================================
    # 시세 조회
    # ============================================================
    def get_market_data(self, code: str) -> dict:
        if code in self._mkt_cache:
            data, ts = self._mkt_cache[code]
            if time.time() - ts < 30:
                return data
        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-price"
        headers = {"authorization": f"Bearer {self.token}",
                   "appkey": self.appkey, "appsecret": self.secret,
                   "tr_id": "FHKST01010100"}
        params  = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}
        try:
            result = requests.get(url, headers=headers, params=params).json().get("output")
            if result:
                self._mkt_cache[code] = (result, time.time())
            return result
        except Exception as e:
            print(f"⚠️ 시세 조회 오류 {code}: {e}"); return None

    def is_market_open(self) -> bool:
        """오늘 장 개설 여부 체크 (공휴일/휴장일 포함)"""
        try:
            url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/chk-holiday"
            headers = {"authorization": f"Bearer {self.token}",
                       "appkey": self.appkey, "appsecret": self.secret,
                       "tr_id": "CTCA0903R"}
            today  = datetime.datetime.now().strftime("%Y%m%d")
            params = {"BASS_DT": today, "CTX_AREA_NK": "", "CTX_AREA_FK": ""}
            res    = requests.get(url, headers=headers, params=params, timeout=5).json()
            output = res.get("output", [])
            if not output: return True
            is_open = output[0].get("bzdy_yn", "Y") == "Y"
            if not is_open:
                print(f"🎌 오늘은 휴장일입니다")
            return is_open
        except Exception as e:
            print(f"⚠️ 휴장일 체크 오류: {e}"); return True

    def get_market_index(self) -> dict:
        result = {"kospi": 0.0, "kosdaq": 0.0}
        for market_code, key in [("0001", "kospi"), ("1001", "kosdaq")]:
            url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-index-price"
            headers = {"authorization": f"Bearer {self.token}",
                       "appkey": self.appkey, "appsecret": self.secret,
                       "tr_id": "FHPUP03500100"}
            params  = {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": market_code}
            try:
                res  = requests.get(url, headers=headers, params=params).json()
                rate = float(res.get("output", {}).get("bstp_nmix_prdy_ctrt", 0) or 0)
                result[key] = rate
            except Exception as e:
                print(f"⚠️ 지수 조회 오류 {key}: {e}")
        return result

    def get_sector_change_rates(self, sector_code_map: dict) -> dict:
        result  = {}
        url     = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-index-price"
        headers = {"authorization": f"Bearer {self.token}",
                   "appkey": self.appkey, "appsecret": self.secret,
                   "tr_id": "FHPUP03500100"}
        for code in sector_code_map.keys():
            params = {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": code}
            try:
                res  = requests.get(url, headers=headers, params=params, timeout=3).json()
                rate = float(res.get("output", {}).get("bstp_nmix_prdy_ctrt", 0) or 0)
                result[code] = rate
                time.sleep(0.05)
            except Exception:
                pass
        return result

    def get_technical_indicators(self, code: str, cache: dict) -> dict:
        if code in cache:
            cached_data, cached_time = cache[code]
            if time.time() - cached_time < 300:
                return cached_data

        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
        headers = {"Content-Type": "application/json",
                   "authorization": f"Bearer {self.token}",
                   "appKey": self.appkey, "appSecret": self.secret,
                   "tr_id": "FHKST03010100"}
        end_date   = datetime.datetime.now().strftime("%Y%m%d")
        start_date = (datetime.datetime.now() - datetime.timedelta(days=120)).strftime("%Y%m%d")
        params = {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": code,
                  "fid_input_date_1": start_date, "fid_input_date_2": end_date,
                  "fid_period_div_code": "D", "fid_org_adj_prc": "0"}
        try:
            res     = requests.get(url, headers=headers, params=params).json()
            candles = res.get("output2", [])
            closes  = [int(x["stck_clpr"]) for x in candles if x.get("stck_clpr")]
            if len(closes) < 20: return {}

            def ma(n): return sum(closes[:n]) / n if len(closes) >= n else 0
            def rsi(period=14):
                if len(closes) < period + 1: return 50
                gains  = [closes[i]-closes[i+1] for i in range(period) if closes[i]>closes[i+1]]
                losses = [abs(closes[i]-closes[i+1]) for i in range(period) if closes[i]<=closes[i+1]]
                avg_g  = sum(gains)/period if gains else 0
                avg_l  = sum(losses)/period if losses else 1
                rs     = avg_g/avg_l if avg_l else 0
                return 100-(100/(1+rs))

            result = {"ma5": ma(5), "ma10": ma(10), "ma20": ma(20), "ma60": ma(60), "rsi": rsi()}
            cache[code] = (result, time.time())
            return result
        except Exception as e:
            print(f"⚠️ 기술지표 오류 {code}: {e}"); return {}

    def get_investor_trend(self, code: str, cache: dict) -> dict:
        if code in cache:
            cached_data, cached_time = cache[code]
            if time.time() - cached_time < 600:
                return cached_data

        def safe_int(v):
            try: return int(v)
            except: return 0

        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-investor"
        headers = {"Content-Type": "application/json",
                   "authorization": f"Bearer {self.token}",
                   "appKey": self.appkey, "appSecret": self.secret,
                   "tr_id": "FHKST01010900"}
        params  = {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": code}
        try:
            res   = requests.get(url, headers=headers, params=params).json()
            items = res.get("output", [])
            if not items: return {}
            frgn = sum(safe_int(x.get("frgn_ntby_tr_pbmn")) for x in items[:5])
            orgn = sum(safe_int(x.get("orgn_ntby_tr_pbmn")) for x in items[:5])
            result = {"foreign_5d": frgn, "institution_5d": orgn}
            cache[code] = (result, time.time())
            return result
        except Exception as e:
            print(f"⚠️ 수급 조회 오류 {code}: {e}"); return {}

    # ============================================================
    # 주문
    # ============================================================
    def buy(self, code: str, price: float, amount: int,
            code_name_map: dict = None, psbl_cash: int = None) -> bool:
        psbl = psbl_cash if psbl_cash is not None else self.get_psbl_order_cash(code)
        if psbl <= 0:
            print(f"⚠️ 주문가능금액 없음: {code}"); return False

        order_cash = min(psbl, amount)

        if   price < 1000:   hoga = 1
        elif price < 5000:   hoga = 5
        elif price < 10000:  hoga = 10
        elif price < 50000:  hoga = 50
        elif price < 100000: hoga = 100
        elif price < 500000: hoga = 500
        else:                hoga = 1000
        limit_price = int(price / hoga) * hoga + hoga

        qty = int(order_cash / (limit_price * 1.00015))
        if qty <= 0:
            print(f"⚠️ 수량 부족: {code}"); return False

        name = (code_name_map or {}).get(code, code)
        print(f"💡 매수계산 {code}({name}) | {order_cash:,}원 | {qty}주 | 지정가:{limit_price:,}")

        url  = f"{self.base_url}/uapi/domestic-stock/v1/trading/order-cash"
        data = {"CANO": self.cano, "ACNT_PRDT_CD": self.acnt,
                "PDNO": code, "ORD_QTY": str(qty),
                "ORD_UNPR": str(limit_price), "ORD_DVSN": "00"}
        headers = {"authorization": f"Bearer {self.token}",
                   "appkey": self.appkey, "appsecret": self.secret,
                   "tr_id": "TTTC0802U", "hashkey": self.get_hashkey(data)}
        try:
            res = requests.post(url, headers=headers, data=json.dumps(data)).json()
            if res.get("rt_cd") == "0":
                return True
            else:
                print(f"❌ 매수 실패 {code}: {res.get('msg1', '알 수 없는 오류')}"); return False
        except Exception as e:
            print(f"❌ 매수 요청 예외 {code}: {e}"); return False

    def sell(self, code: str, qty: int) -> bool:
        url  = f"{self.base_url}/uapi/domestic-stock/v1/trading/order-cash"
        data = {"CANO": self.cano, "ACNT_PRDT_CD": self.acnt,
                "PDNO": code, "ORD_QTY": str(qty),
                "ORD_UNPR": "0", "ORD_DVSN": "01"}
        headers = {"authorization": f"Bearer {self.token}",
                   "appkey": self.appkey, "appsecret": self.secret,
                   "tr_id": "TTTC0801U", "hashkey": self.get_hashkey(data)}
        try:
            res = requests.post(url, headers=headers, data=json.dumps(data)).json()
            if res.get("rt_cd") == "0":
                return True
            else:
                print(f"❌ 매도 실패 {code}: {res.get('msg1', '알 수 없는 오류')}"); return False
        except Exception as e:
            print(f"❌ 매도 요청 예외 {code}: {e}"); return False

    # ============================================================
    # 한투 관심그룹 (watchlist)
    # ============================================================
    def get_watchlist_groups(self, hts_id: str) -> dict:
        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/intstock-grouplist"
        headers = {"authorization": f"Bearer {self.token}",
                   "appkey": self.appkey, "appsecret": self.secret,
                   "tr_id": "HHKCM113004C7", "custtype": "P"}
        params  = {"TYPE": "1", "FID_ETC_CLS_CODE": "00", "USER_ID": hts_id}
        try:
            res = requests.get(url, headers=headers, params=params, timeout=5).json()
            if res.get("rt_cd") != "0":
                print(f"  ⚠️ 한투 관심그룹 오류: {res.get('msg1')}"); return {}
            groups = {}
            for item in res.get("output2", []):
                grp_code = item.get("inter_grp_code", "").strip()
                grp_name = item.get("inter_grp_name", "").strip()
                if grp_code and grp_name:
                    groups[grp_code] = grp_name
            return groups
        except Exception as e:
            print(f"  ⚠️ 한투 관심그룹 예외: {e}"); return {}

    def get_watchlist_stocks(self, grp_code: str, hts_id: str,
                             code_name_map: dict = None) -> list:
        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/intstock-stocklist-by-group"
        headers = {"authorization": f"Bearer {self.token}",
                   "appkey": self.appkey, "appsecret": self.secret,
                   "tr_id": "HHKCM113004C6", "custtype": "P"}
        params  = {"TYPE": "1", "USER_ID": hts_id, "DATA_RANK": "",
                   "INTER_GRP_CODE": grp_code, "INTER_GRP_NAME": "",
                   "HTS_KOR_ISNM": "", "CNTG_CLS_CODE": "",
                   "FID_ETC_CLS_CODE": "4"}
        try:
            res  = requests.get(url, headers=headers, params=params, timeout=5).json()
            if res.get("rt_cd") != "0":
                print(f"  ⚠️ [{grp_code}] 그룹종목 오류: {res.get('msg1')}"); return []
            result = []
            for item in res.get("output2", []):
                code = item.get("jong_code", "").strip()
                name = item.get("hts_kor_isnm", "").strip()
                if code and code.isdigit():
                    result.append((code, name))
                    if code_name_map is not None:
                        code_name_map[code] = name
            return result
        except Exception as e:
            print(f"  ⚠️ [{grp_code}] 그룹종목 예외: {e}"); return []

    # ============================================================
    # 거래량 순위 (폴백)
    # ============================================================
    def get_volume_rank_codes(self, seen: set, code_name_map: dict = None) -> list:
        url  = f"{self.base_url}/uapi/domestic-stock/v1/quotations/volume-rank"
        skip = ["KODEX","TIGER","KBSTAR","ARIRANG","HANARO",
                "KOSEF","TREX","SOL","ACE","PLUS","인버스","레버리지","ETN","선물"]
        headers = {"Content-Type": "application/json",
                   "authorization": f"Bearer {self.token}",
                   "appKey": self.appkey, "appSecret": self.secret,
                   "tr_id": "FHPST01710000"}
        params  = {"FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "20171",
                   "FID_INPUT_ISCD": "0000", "FID_DIV_CLS_CODE": "0",
                   "FID_BLNG_CLS_CODE": "0", "FID_TRGT_CLS_CODE": "111111111",
                   "FID_TRGT_EXLS_CLS_CODE": "000000",
                   "FID_INPUT_PRICE_1": "0", "FID_INPUT_PRICE_2": "0",
                   "FID_VOL_CNT": "0", "FID_INPUT_DATE_1": "0"}
        codes = []
        try:
            res = requests.get(url, headers=headers, params=params).json()
            if res.get("rt_cd") == "0":
                for item in res.get("output", []):
                    code = item.get("mksc_shrn_iscd", "").strip()
                    name = item.get("hts_kor_isnm",   "").strip()
                    if not code or code in seen or not code.isdigit(): continue
                    if any(kw in name for kw in skip): continue
                    seen.add(code); codes.append(code)
                    if code_name_map is not None:
                        code_name_map[code] = name
            print(f"  📊 거래량 순위 보완: +{len(codes)}개")
        except Exception as e:
            print(f"❌ 거래량 순위 API 예외: {e}")
        return codes
