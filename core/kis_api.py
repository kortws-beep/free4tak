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
    def _token_dat_path(self) -> str:
        """계좌별 토큰 캐시 파일 경로"""
        _base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(_base, f"token_{self.cano}.dat")

    def _issue_token(self) -> str:
        # 1) 계좌별 token.dat 캐시 확인
        try:
            import pickle as _pk
            _tdat = self._token_dat_path()
            if os.path.exists(_tdat):
                _d = _pk.load(open(_tdat, "rb"))
                _ok = (
                    _d.get("api_key", "") == self.appkey
                    and "access_token_token_expired" in _d
                    and datetime.datetime.now() <
                        datetime.datetime.strptime(
                            _d["access_token_token_expired"], "%Y-%m-%d %H:%M:%S")
                        - datetime.timedelta(hours=1)
                )
                if _ok and _d.get("access_token"):
                    print(f"✅ 토큰 캐시 사용 [{self.cano}] (만료:{_d['access_token_token_expired']})")
                    return _d["access_token"]
                else:
                    os.remove(_tdat)
                    print(f"⚠️ 토큰 캐시 만료/불일치 [{self.cano}] - 재발급")
        except Exception:
            pass

        # 2) 신규 발급
        url  = f"{self.base_url}/oauth2/tokenP"
        body = {"grant_type": "client_credentials",
                "appkey": self.appkey, "appsecret": self.secret}
        try:
            res = requests.post(url, json=body).json()
            if "error_code" in res:
                print(f"❌ 토큰 발급 오류 [{self.cano}]: {res.get('error_description', res['error_code'])}")
                return ""
            token = res.get("access_token", "")
            exp   = res.get("access_token_token_expired", "")
            if token:
                try:
                    import pickle as _pk2
                    _pk2.dump({"access_token": token,
                               "access_token_token_expired": exp,
                               "api_key": self.appkey,
                               "timestamp": int(time.time())},
                              open(self._token_dat_path(), "wb"))
                except Exception:
                    pass
                print(f"✅ 한투 토큰 발급 완료 [{self.cano}]")
            return token
        except Exception as e:
            print(f"❌ 토큰 발급 실패: {e}"); return ""

    def refresh_token_if_needed(self):
        if time.time() - self.token_issued_at > TOKEN_TTL - 3600:
            print("🔄 토큰 갱신 중...")
            try:
                _tdat = self._token_dat_path()
                if os.path.exists(_tdat): os.remove(_tdat)
            except Exception: pass
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
        params  = {
            "CANO": self.cano, "ACNT_PRDT_CD": self.acnt,
            "AFHR_FLPR_YN": "N", "OFL_YN": "",
            "INQR_DVSN": "01", "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N", "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "00", "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
        }
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
        # ★ 30초 캐시 (API 호출 횟수 제한 대응)
        _now = time.time()
        if hasattr(self, '_pos_cache') and self._pos_cache and _now - self._pos_cache_ts < 60:
            return self._pos_cache
        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-balance"
        headers = {"authorization": f"Bearer {self.token}",
                   "appkey": self.appkey, "appsecret": self.secret,
                   "tr_id": "TTTC8434R"}
        params  = {
            "CANO": self.cano, "ACNT_PRDT_CD": self.acnt,
            "AFHR_FLPR_YN": "N", "OFL_YN": "",
            "INQR_DVSN": "01", "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N", "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "00", "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
        }
        try:
            res = requests.get(url, headers=headers, params=params).json()
            pos = {}
            _etf_skip = ["KODEX","TIGER","KBSTAR","ARIRANG","HANARO",
                         "KOSEF","TREX","SOL","ACE","PLUS","RISE"]
            for item in res.get("output1", []):
                qty = int(item.get("hldg_qty", 0))
                if qty <= 0: continue
                code = item.get("pdno")
                name = item.get("prdt_name", "")
                # ETF 필터 (코드 6자리 숫자 아니면 제외)
                if not code.isdigit() or any(s in name for s in _etf_skip):
                    print(f'⚠️ 포지션 제외 (ETF/기타): {code} {name}')
                    continue
                avg  = float(item.get("pchs_avg_pric", 0))
                pos[code] = {"entry_price": avg, "qty": qty}
            if pos:
                # ★ 정상값만 캐시 갱신 (빈값이면 이전 캐시 유지)
                self._pos_cache = pos
                self._pos_cache_ts = time.time()
            elif hasattr(self, '_pos_cache') and self._pos_cache:
                print(f"⚠️ 잔고 빈값 — 이전 캐시 유지 ({len(self._pos_cache)}종목)")
                return self._pos_cache
            return pos
        except Exception as e:
            print(f"❌ 보유종목 조회 오류: {e}")
            if hasattr(self, '_pos_cache') and self._pos_cache:
                return self._pos_cache
            return {}

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

    def get_hoga(self, code: str) -> dict:
        """
        주식현재가 호가/예상체결 조회 (FHKST01010200)
        반환: {
            "total_ask_rsqn": 총 매도호가 잔량,
            "total_bid_rsqn": 총 매수호가 잔량,
            "ask_bid_ratio":  매도/매수 비율 (3이상 = 눌린 스프링),
            "ntby_rsqn":      순매수 잔량
        }
        """
        # 30초 캐시
        cache_key = f"hoga_{code}"
        if cache_key in self._mkt_cache:
            data, ts = self._mkt_cache[cache_key]
            if time.time() - ts < 30:
                return data

        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn"
        headers = {"authorization": f"Bearer {self.token}",
                   "appkey": self.appkey, "appsecret": self.secret,
                   "tr_id": "FHKST01010200"}
        params  = {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code}
        try:
            res = requests.get(url, headers=headers,
                               params=params, timeout=5).json()
            out = res.get("output1", {})
            if not out:
                return {}
            def safe_int(v):
                try: return int(str(v).replace(",", "") or 0)
                except: return 0

            total_ask = safe_int(out.get("total_askp_rsqn", 0))
            total_bid = safe_int(out.get("total_bidp_rsqn", 0))
            ntby      = safe_int(out.get("ntby_aspr_rsqn", 0))
            ratio     = round(total_ask / total_bid, 2) if total_bid > 0 else 0.0

            result = {
                "total_ask_rsqn": total_ask,
                "total_bid_rsqn": total_bid,
                "ask_bid_ratio":  ratio,
                "ntby_rsqn":      ntby,
            }
            self._mkt_cache[cache_key] = (result, time.time())
            return result
        except Exception as e:
            print(f"⚠️ 호가 조회 오류 {code}: {e}")
            return {}

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
        """
        코스피/코스닥 등락률 조회.
        ★ 네이버 금융 API 사용 (한투 지수 API 인증 문제로 대체)
        """
        result = {"kospi": 0.0, "kosdaq": 0.0}
        headers = {"User-Agent": "Mozilla/5.0"}
        for symbol, key in [("KOSPI", "kospi"), ("KOSDAQ", "kosdaq")]:
            try:
                res  = requests.get(
                    f"https://m.stock.naver.com/api/index/{symbol}/basic",
                    headers=headers, timeout=5,
                ).json()
                rate = float(res.get("fluctuationsRatio", 0) or 0)
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
        """
        ★ v2 — MACD / 볼린저밴드 / 스토캐스틱 추가
        기존 일봉 데이터 재사용 (추가 API 호출 없음)
        """
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
        start_date = (datetime.datetime.now() - datetime.timedelta(days=290)).strftime("%Y%m%d")  # ★ 200 MA 확보위해 290일 (영업일 ~200일)
        params = {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": code,
                  "fid_input_date_1": start_date, "fid_input_date_2": end_date,
                  "fid_period_div_code": "D", "fid_org_adj_prc": "0"}
        try:
            res     = requests.get(url, headers=headers, params=params).json()
            candles = res.get("output2", [])
            if len(candles) < 26: return {}

            closes = [int(x["stck_clpr"]) for x in candles if x.get("stck_clpr")]
            highs  = [int(x["stck_hgpr"]) for x in candles if x.get("stck_hgpr")]
            lows   = [int(x["stck_lwpr"]) for x in candles if x.get("stck_lwpr")]
            opens  = [int(x["stck_oprc"]) for x in candles if x.get("stck_oprc")]

            if len(closes) < 26: return {}

            # ── 기본 MA ─────────────────────────────────────
            def ma(n):
                return sum(closes[:n]) / n if len(closes) >= n else 0

            # ── RSI ─────────────────────────────────────────
            def rsi(period=14):
                if len(closes) < period + 1: return 50
                gains  = [closes[i]-closes[i+1] for i in range(period) if closes[i]>closes[i+1]]
                losses = [abs(closes[i]-closes[i+1]) for i in range(period) if closes[i]<=closes[i+1]]
                avg_g  = sum(gains)/period if gains else 0
                avg_l  = sum(losses)/period if losses else 1e-9
                return 100-(100/(1 + avg_g/avg_l))

            # ── MACD ─────────────────────────────────────────
            def ema(prices, period):
                k = 2 / (period + 1)
                e = prices[-1]
                for p in reversed(prices[:-1]):
                    e = p * k + e * (1 - k)
                return e

            ema12 = ema(closes[::-1][:26][::-1], 12)  # ★ 최신→과거→최신 순 정렬
            ema26 = ema(closes[::-1][:26][::-1], 26)
            macd  = ema12 - ema26

            # MACD 9일 시그널 (간이 계산)
            macd_vals = []
            for i in range(9, 26):
                e12 = ema(closes[i:i+12][::-1], 12)
                e26 = ema(closes[i:i+26][::-1], 26)
                macd_vals.append(e12 - e26)
            macd_signal = sum(macd_vals) / len(macd_vals) if macd_vals else 0
            macd_hist   = macd - macd_signal  # 양수=상승 모멘텀, 음수=하락

            # ── 볼린저밴드 (20일) ─────────────────────────────
            bb_mid = ma(20)
            if bb_mid > 0 and len(closes) >= 20:
                variance  = sum((c - bb_mid)**2 for c in closes[:20]) / 20
                bb_std    = variance ** 0.5
                bb_upper  = bb_mid + 2 * bb_std
                bb_lower  = bb_mid - 2 * bb_std
                bb_width  = (bb_upper - bb_lower) / bb_mid if bb_mid > 0 else 0
                bb_pct    = ((closes[0] - bb_lower) / (bb_upper - bb_lower)
                             if bb_upper > bb_lower else 0.5)
            else:
                bb_upper = bb_lower = bb_mid
                bb_width = bb_pct = 0

            # ── 스토캐스틱 (14,3) ────────────────────────────
            stoch_k = 50.0
            if len(highs) >= 14 and len(lows) >= 14:
                h14 = max(highs[:14])
                l14 = min(lows[:14])
                stoch_k = ((closes[0] - l14) / (h14 - l14) * 100
                           if h14 > l14 else 50)

            # ── 캔들 패턴 ────────────────────────────────────
            # 망치형: 아래꼬리 길고 몸통 작음 (반등 신호)
            candle_pattern = 0
            if opens and highs and lows and closes:
                body   = abs(closes[0] - opens[0])
                lower  = min(opens[0], closes[0]) - lows[0]
                upper  = highs[0] - max(opens[0], closes[0])
                total  = highs[0] - lows[0]
                if total > 0:
                    if lower > body * 2 and upper < body * 0.5:
                        candle_pattern = 1   # 망치형 (매수 신호)
                    elif upper > body * 2 and lower < body * 0.5:
                        candle_pattern = -1  # 역망치형 (매도 신호)

            result = {
                "ma5":           ma(5),
                "ma10":          ma(10),
                "ma20":          ma(20),
                "ma60":          ma(60),
                "ma120":         ma(120),   # ★ 추가
                "ma200":         ma(200),   # ★ sbot2 추세추종 핵심
                "rsi":           round(rsi(), 1),
                # ★ 신규
                "macd":          round(macd, 2),
                "macd_signal":   round(macd_signal, 2),
                "macd_hist":     round(macd_hist, 2),
                "bb_upper":      round(bb_upper, 2),
                "bb_lower":      round(bb_lower, 2),
                "bb_pct":        round(bb_pct, 3),   # 0=하단, 1=상단
                "bb_width":      round(bb_width, 4),  # 좁을수록 폭발 임박
                "stoch_k":       round(stoch_k, 1),
                "candle_pattern": candle_pattern,
            }
            cache[code] = (result, time.time())
            return result
        except Exception as e:
            print(f"⚠️ 기술지표 오류 {code}: {e}"); return {}

    def get_investor_trend(self, code: str, cache: dict) -> dict:
        """
        ★ v2 — 당일 실시간 + 5일 누적 수급 분석
        
        [당일 실시간]
        - foreign_today:  외국인 당일 순매수금액 (백만원)
        - orgn_today:     기관 당일 순매수금액
        - prsn_today:     개인 당일 순매수금액 (역지표)
        - foreign_ratio:  외국인 매수비율 (매수/전체거래)
        - orgn_ratio:     기관 매수비율

        [5일 누적]
        - foreign_5d:     외국인 5일 누적 순매수
        - institution_5d: 기관 5일 누적 순매수

        [강도 지수]
        - buy_pressure:   매수 압력 (외국인+기관 순매수 / 전체거래대금)
        """
        if code in cache:
            cached_data, cached_time = cache[code]
            if time.time() - cached_time < 300:  # ★ 5분 캐시 (당일 실시간)
                return cached_data

        def safe_int(v):
            try: return int(str(v).replace(",",""))
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

            # ── 당일 (items[0]) ──────────────────────────────
            # ★ KIS API 한계: 장중 당일 수급 미제공 → 전일 마감 기준 사용
            # 전일 마감 데이터가 오늘 매수 판단에 참고 지표로 활용됨
            d_today = items[0]  # 당일 (비어있음)
            d_prev  = items[1] if len(items) > 1 else {}  # 전일 (실제 데이터)

            # 전일 마감 기준 수급 (수량 우선, 없으면 금액)
            foreign_today = safe_int(
                d_prev.get("frgn_ntby_qty") or d_prev.get("frgn_ntby_tr_pbmn") or 0)
            orgn_today    = safe_int(
                d_prev.get("orgn_ntby_qty") or d_prev.get("orgn_ntby_tr_pbmn") or 0)
            prsn_today    = safe_int(
                d_prev.get("prsn_ntby_qty") or d_prev.get("prsn_ntby_tr_pbmn") or 0)

            # 외국인 매수비율 (매수량 / (매수+매도))
            frgn_buy  = safe_int(d_prev.get("frgn_shnu_vol", 0))
            frgn_sell = safe_int(d_prev.get("frgn_seln_vol", 0))
            frgn_total = frgn_buy + frgn_sell
            foreign_ratio = round(frgn_buy / frgn_total * 100, 1) if frgn_total > 0 else 50.0

            # 기관 매수비율
            orgn_buy  = safe_int(d_prev.get("orgn_shnu_vol", 0))
            orgn_sell = safe_int(d_prev.get("orgn_seln_vol", 0))
            orgn_total = orgn_buy + orgn_sell
            orgn_ratio = round(orgn_buy / orgn_total * 100, 1) if orgn_total > 0 else 50.0

            # 매수 압력 지수 (외국인+기관 순매수 합 / 전체 거래대금)
            frgn_pbmn = safe_int(d_prev.get("frgn_shnu_tr_pbmn", 0)) + safe_int(d_prev.get("frgn_seln_tr_pbmn", 0))
            orgn_pbmn = safe_int(d_prev.get("orgn_shnu_tr_pbmn", 0)) + safe_int(d_prev.get("orgn_seln_tr_pbmn", 0))
            total_pbmn = frgn_pbmn + orgn_pbmn
            net_buy    = foreign_today + orgn_today
            buy_pressure = round(net_buy / total_pbmn * 100, 1) if total_pbmn > 0 else 0.0

            # ── 5일 누적 ─────────────────────────────────────
            frgn_5d = sum(safe_int(x.get("frgn_ntby_qty") or x.get("frgn_ntby_tr_pbmn", 0)) for x in items[:5])
            orgn_5d = sum(safe_int(x.get("orgn_ntby_qty") or x.get("orgn_ntby_tr_pbmn", 0)) for x in items[:5])

            result = {
                # 당일 실시간
                "foreign_today":  foreign_today,
                "orgn_today":     orgn_today,
                "prsn_today":     prsn_today,
                "foreign_ratio":  foreign_ratio,   # 외국인 매수비율 %
                "orgn_ratio":     orgn_ratio,       # 기관 매수비율 %
                "buy_pressure":   buy_pressure,     # 매수압력 지수

                # 5일 누적 (기존 호환)
                "foreign_5d":     frgn_5d,
                "institution_5d": orgn_5d,
            }
            cache[code] = (result, time.time())
            return result
        except Exception as e:
            import traceback; traceback.print_exc()
            print(f"⚠️ 수급 조회 오류 {code}: {e}"); return {}

    # ============================================================
    # 주문
    # ============================================================
    def buy(self, code: str, price: float, amount: int,
            code_name_map: dict = None, psbl_cash: int = None) -> bool:
        psbl = psbl_cash if psbl_cash is not None else self.get_psbl_order_cash(code)
        if psbl <= 0:
            print(f"⚠️ 주문가능금액 없음: {code}"); return False, ""

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
            # ★ 1주라도 살 수 있는지 확인 (지정가로 최소 1주 시도)
            if order_cash >= limit_price:
                qty = 1
                print(f"⚠️ 예산 부족 → 최소 1주 매수 시도: {code} ({limit_price:,}원)")
            else:
                print(f"⚠️ 수량 부족: {code} | 주문가능:{order_cash:,} < 주가:{limit_price:,}")
                return False, ""

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
            res = requests.post(url, headers=headers, data=json.dumps(data), timeout=10).json()
            if res.get("rt_cd") == "0":
                out = res.get("output", {})
                orgno = out.get("KRX_FWDG_ORD_ORGNO", "")
                odno  = out.get("ODNO", "")
                return True, orgno, odno
            else:
                print(f"❌ 매수 실패 {code}: {res.get('msg1', '알 수 없는 오류')}"); return False, ""
        except Exception as e:
            print(f"❌ 매수 요청 예외 {code}: {e}"); return False, ""


    def cancel_order(self, orgno: str, odno: str, code: str, qty: int) -> bool:
        """미체결 주문 취소 (TTTC0803U)"""
        if not odno:
            return False, ""
        url  = f"{self.base_url}/uapi/domestic-stock/v1/trading/order-rvsecncl"
        data = {
            "CANO": self.cano, "ACNT_PRDT_CD": self.acnt,
            "KRX_FWDG_ORD_ORGNO": orgno,
            "ORGN_ODNO": odno,
            "ORD_DVSN": "00", "RVSE_CNCL_DVSN_CD": "02",
            "ORD_QTY": str(qty), "ORD_UNPR": "0",
            "QTY_ALL_ORD_YN": "Y", "PDNO": code,
        }
        headers = {"authorization": f"Bearer {self.token}",
                   "appkey": self.appkey, "appsecret": self.secret,
                   "tr_id": "TTTC0803U", "hashkey": self.get_hashkey(data)}
        try:
            res = requests.post(url, headers=headers, data=json.dumps(data), timeout=10).json()
            if res.get("rt_cd") == "0":
                print(f"✅ 주문취소: {code} (odno:{odno})")
                return True
            else:
                print(f"❌ 취소실패 {code}: {res.get('msg1', '')}")
                return False
        except Exception as e:
            print(f"❌ 취소예외 {code}: {e}")
            return False

    def sell(self, code: str, qty: int) -> bool:
        url  = f"{self.base_url}/uapi/domestic-stock/v1/trading/order-cash"
        data = {"CANO": self.cano, "ACNT_PRDT_CD": self.acnt,
                "PDNO": code, "ORD_QTY": str(qty),
                "ORD_UNPR": "0", "ORD_DVSN": "01"}
        headers = {"authorization": f"Bearer {self.token}",
                   "appkey": self.appkey, "appsecret": self.secret,
                   "tr_id": "TTTC0801U", "hashkey": self.get_hashkey(data)}
        try:
            res = requests.post(url, headers=headers, data=json.dumps(data), timeout=10).json()
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
    # 일봉 OHLC (ATR 계산용)
    # ============================================================
    def get_daily_ohlc(self, code: str, days: int = 20) -> list:
        """
        일봉 OHLC 데이터 조회 (ATR 변동성 계산용).

        반환: [{"high": int, "low": int, "close": int, "open": int, "volume": int}, ...]
        - 최신 → 과거 순서 (index 0이 가장 최근 일봉)
        - risk_manager.calc_atr_rate()에 그대로 전달 가능
        """
        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
        headers = {"Content-Type": "application/json",
                   "authorization": f"Bearer {self.token}",
                   "appKey": self.appkey, "appSecret": self.secret,
                   "tr_id": "FHKST03010100"}
        end_date   = datetime.datetime.now().strftime("%Y%m%d")
        # ATR period=14 + 여유분 → 30일 정도 가져옴
        fetch_days = max(days + 15, 30)
        start_date = (datetime.datetime.now()
                      - datetime.timedelta(days=fetch_days * 2)).strftime("%Y%m%d")
        params = {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": code,
                  "fid_input_date_1": start_date, "fid_input_date_2": end_date,
                  "fid_period_div_code": "D", "fid_org_adj_prc": "0"}
        try:
            res     = requests.get(url, headers=headers, params=params, timeout=5).json()
            candles = res.get("output2", [])
            ohlc    = []
            for c in candles[:days]:
                try:
                    ohlc.append({
                        "high":   int(c.get("stck_hgpr",  0) or 0),
                        "low":    int(c.get("stck_lwpr",  0) or 0),
                        "close":  int(c.get("stck_clpr",  0) or 0),
                        "open":   int(c.get("stck_oprc",  0) or 0),
                        "volume": int(c.get("acml_vol",   0) or 0),
                    })
                except Exception:
                    continue
            return ohlc
        except Exception as e:
            print(f"⚠️ 일봉 조회 오류 {code}: {e}"); return []

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
