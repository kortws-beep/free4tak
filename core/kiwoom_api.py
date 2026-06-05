"""
kiwoom_api.py — 키움 OpenAPI 래퍼 (조건검색 / 테마 / 관심그룹)
"""
import os
import time
import json
import asyncio
import requests


KIWOOM_WS_URL = "wss://api.kiwoom.com:10000/api/dostk/websocket"


class KiwoomAPI:

    def __init__(self):
        self.appkey    = os.getenv("KIWOOM_APPKEY", "")
        self.secretkey = os.getenv("KIWOOM_SECRETKEY", "")
        self.token     = ""
        self.token_at  = 0
        self.enabled   = bool(self.appkey and self.secretkey)

    # ============================================================
    # 토큰
    # ============================================================
    def get_token(self) -> str:
        if self.token and time.time() - self.token_at < 82800:
            return self.token
        # ★ 재시도 3회 + exponential backoff
        import time as _t
        for _retry in range(3):
            try:
                res = requests.post(
                    "https://api.kiwoom.com/oauth2/token",
                    json={"grant_type": "client_credentials",
                          "appkey": self.appkey, "secretkey": self.secretkey},
                    timeout=10,
                ).json()
                self.token    = res.get("token", "")
                self.token_at = time.time()
                print("✅ 키움 토큰 발급 완료")
                return self.token
            except Exception as e:
                wait = 5 * (2 ** _retry)
                print(f"⚠️ 키움 토큰 발급 실패({_retry+1}/3): {e} — {wait}초 후 재시도")
                _t.sleep(wait)
        print("❌ 키움 토큰 발급 3회 실패")
        self.enabled = False  # ★ 실패 시 비활성화 → 한투 폴백
        return ""

    def reset_token(self):
        """토큰 강제 초기화 — API 오류 시 재발급 유도"""
        self.token    = ""
        self.token_at = 0
        self.enabled  = bool(self.appkey and self.secretkey)
        print("🔄 키움 토큰 초기화 — 다음 호출 시 재발급")

    # ============================================================
    # 조건검색 (WebSocket)
    # ============================================================
    async def get_condition_codes(self, use_keywords: list = None,
                                   code_name_map: dict = None,
                                   skip_keywords: list = None) -> list:
        """
        키움 조건검색식으로 종목 조회.
        use_keywords:  이 키워드 포함된 검색식만 사용 (None이면 전체)
        skip_keywords: 이 키워드 포함된 검색식 제외 (단타봇 조건식 제외 등)
        """
        import websockets as _ws
        token = self.get_token()
        if not token: return []

        codes = []
        seen  = set()
        try:
            async with _ws.connect(KIWOOM_WS_URL) as ws:
                await ws.send(json.dumps({"trnm": "LOGIN", "token": token}))
                res = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                if res.get("return_code") != 0:
                    print(f"⚠️ 키움 로그인 실패: {res.get('return_msg')}"); return []

                await ws.send(json.dumps({"trnm": "CNSRLST"}))
                cond_list = []
                while True:
                    try:
                        res = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                        if res.get("trnm") == "PING":
                            await ws.send(json.dumps(res)); continue
                        if res.get("trnm") == "CNSRLST":
                            cond_list = res.get("data", []); break
                    except asyncio.TimeoutError: break

                print(f"  🔍 키움 조건검색식: {len(cond_list)}개")

                for cond in cond_list:
                    seq  = cond[0] if isinstance(cond, list) else cond.get("seq", "")
                    name = cond[1] if isinstance(cond, list) else cond.get("name", "")

                    # 사용할 조건검색식 필터링
                    if use_keywords and not any(kw in name for kw in use_keywords):
                        print(f"  ⏭️ 제외: [{seq}]{name}")
                        continue
                    # ★ skip_keywords: 이 키워드 포함 검색식 제외
                    if skip_keywords and any(kw in name for kw in skip_keywords):
                        print(f"  ⏭️ 스킵: [{seq}]{name}")
                        continue

                    await ws.send(json.dumps({
                        "trnm": "CNSRREQ", "seq": seq,
                        "search_type": "0", "stex_tp": "K",
                    }))
                    fetched = 0
                    while True:
                        try:
                            res = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                            if res.get("trnm") == "PING":
                                await ws.send(json.dumps(res)); continue
                            if res.get("return_code") != 0: break
                            for item in (res.get("data") or []):
                                raw   = item.get("9001", "") if isinstance(item, dict) else (item[0] if item else "")
                                code  = raw.lstrip("A") if raw.startswith("A") else raw
                                iname = item.get("302", "") if isinstance(item, dict) else (item[1] if len(item) > 1 else "")
                                if code and code not in seen:
                                    seen.add(code); codes.append(code)
                                    if code_name_map is not None:
                                        code_name_map[code] = iname
                                    fetched += 1
                            if res.get("cont_yn") != "Y": break
                        except asyncio.TimeoutError: break
                    print(f"  📊 조건검색 [{seq}]{name}: +{fetched}개")

        except Exception as e:
            print(f"⚠️ 키움 WebSocket 오류: {e}")
        return codes

    # ============================================================
    # 테마 API (ka90001 / ka90002)
    # ============================================================
    def get_theme_top(self, top_n: int = 5) -> list:
        token = self.get_token()
        if not token: return []
        headers = {
            "Content-Type":  "application/json;charset=UTF-8",
            "authorization": f"Bearer {token}",
            "cont-yn": "N", "next-key": "",
            "api-id":  "ka90001",
        }
        body = {"qry_tp": "0", "date_tp": "1",
                "flu_pl_amt_tp": "3", "stex_tp": "1"}
        try:
            res = requests.post(
                "https://api.kiwoom.com/api/dostk/thme",
                headers=headers, json=body, timeout=10
            ).json()
            if res.get("return_code") != 0:
                print(f"  ⚠️ ka90001 오류: {res.get('return_msg')}"); return []
            items = res.get("thema_grp", res.get("output", res.get("data", [])))
            if not items: return []
            result = items[:top_n]
            for item in result:
                nm = item.get("thema_nm", item.get("thema_grp_nm", ""))
                cd = item.get("thema_grp_cd", item.get("thma_grp_cd", ""))
                rt = item.get("flu_rt", item.get("prdy_ctrt", ""))
                print(f"  🎯 테마: [{cd}]{nm} ({rt}%)")
            return result
        except Exception as e:
            print(f"  ⚠️ ka90001 예외: {e}"); return []

    def get_theme_stocks(self, thema_grp_cd: str,
                         code_name_map: dict = None) -> list:
        token = self.get_token()
        if not token: return []
        headers = {
            "Content-Type":  "application/json;charset=UTF-8",
            "authorization": f"Bearer {token}",
            "cont-yn": "N", "next-key": "",
            "api-id":  "ka90002",
        }
        body = {"thema_grp_cd": thema_grp_cd, "stex_tp": "1", "date_tp": "1"}
        try:
            res = requests.post(
                "https://api.kiwoom.com/api/dostk/thme",
                headers=headers, json=body, timeout=10
            ).json()
            if res.get("return_code") != 0:
                print(f"  ⚠️ ka90002 오류: {res.get('return_msg')}"); return []
            items = res.get("thema_comp_stk", res.get("stk_list", res.get("output", res.get("data", []))))
            if not items: return []
            result = []
            for item in items:
                raw  = item.get("stk_cd", item.get("shtn_iscd", item.get("9001", "")))
                name = item.get("stk_nm", item.get("hts_kor_isnm", item.get("302", "")))
                code = raw.lstrip("A") if raw.startswith("A") else raw
                if code and code.isdigit():
                    result.append((code, name.strip()))
                    if code_name_map is not None:
                        code_name_map[code] = name.strip()
            return result
        except Exception as e:
            print(f"  ⚠️ ka90002 예외: {e}"); return []

    # ============================================================
    # 관심그룹 (WebSocket — kiki용)
    # ============================================================
    def get_investor_trend(self, code: str) -> dict:
        """
        ka10009 — 기관외국인연속매매현황
        반환: {foreign_today, orgn_today, foreign_5d, institution_5d}
        """
        try:
            token = self.get_token()
            headers = {
                "authorization": f"Bearer {token}",
                "api-id": "ka10008",
                "Content-Type": "application/json;charset=UTF-8",
            }
            body = {"stk_cd": code}
            res = requests.post(
                "https://api.kiwoom.com/api/dostk/frgnistt",
                headers=headers, json=body, timeout=10
            ).json()

            def safe_int(v):
                try: return int(str(v).replace(",", "").replace("+", "").strip() or 0)
                except: return 0

            items = res.get("stk_frgnr", [])
            if not items:
                return {}

            # 당일(items[0])은 장중 0 → 전일(items[1]) 사용
            # chg_qty = 외국인 변동수량 (순매수)
            d_prev = items[1] if len(items) > 1 else items[0]
            foreign_today = safe_int(d_prev.get("chg_qty", 0))

            # 5일 누적 (전일 기준 5일)
            foreign_5d = sum(
                safe_int(x.get("chg_qty", 0))
                for x in items[1:6]  # items[0]=당일(0), items[1~5]=전일~5일전
            )

            return {
                "foreign_today": foreign_today,
                "foreign_5d":    foreign_5d,
            }
        except Exception as e:
            print(f"⚠️ kiwoom investor_trend 오류: {e}")
            return {}

    async def get_watchlist_groups_ws(self) -> list:
        """
        키움 WebSocket으로 관심그룹 읽기.
        반환: [(종목코드, 종목명, source), ...]
        source: hts_sector / hts_theme / hts_new
        """
        import websockets as _ws
        token = self.get_token()
        if not token: return []

        codes = []
        seen  = set()
        try:
            async with _ws.connect(KIWOOM_WS_URL, ping_interval=None) as ws:
                await ws.send(json.dumps({"trnm": "LOGIN", "token": token}))
                res = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                if res.get("return_code") != 0:
                    print(f"⚠️ 키움 로그인 실패"); return []
                print("✅ 키움 WebSocket 로그인 (관심그룹)")

                await ws.send(json.dumps({"trnm": "INTSLST"}))
                grp_list = []
                while True:
                    try:
                        res = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                        if res.get("trnm") == "PING":
                            await ws.send(json.dumps(res)); continue
                        if res.get("trnm") == "INTSLST":
                            grp_list = res.get("data", []); break
                    except asyncio.TimeoutError: break

                print(f"  📂 키움 관심그룹 전체: {len(grp_list)}개")

                for grp in grp_list:
                    if isinstance(grp, dict):
                        grp_no   = str(grp.get("grp_no", grp.get("intstock_grp_no", "")))
                        grp_name = grp.get("grp_name", grp.get("intstock_grp_name", ""))
                    elif isinstance(grp, list):
                        grp_no   = str(grp[0]) if len(grp) > 0 else ""
                        grp_name = grp[1]      if len(grp) > 1 else ""
                    else:
                        continue

                    is_sector = grp_name.startswith("업종")
                    is_theme  = grp_name == "테마" or grp_name.startswith("테마")
                    is_new    = grp_name.lower() in ("new", "신규추천", "신규")

                    if not (is_sector or is_theme or is_new):
                        print(f"  ⏭️ [{grp_no}]{grp_name} 제외")
                        continue

                    source = ("hts_sector" if is_sector
                              else "hts_theme" if is_theme
                              else "hts_new")

                    await ws.send(json.dumps({
                        "trnm": "INTSTKL", "intstock_grp_no": grp_no,
                    }))
                    fetched = 0
                    while True:
                        try:
                            res = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                            if res.get("trnm") == "PING":
                                await ws.send(json.dumps(res)); continue
                            if res.get("return_code") != 0: break
                            for item in (res.get("data") or []):
                                if isinstance(item, dict):
                                    raw  = item.get("stk_code", item.get("9001", ""))
                                    name = item.get("stk_name", item.get("302", ""))
                                elif isinstance(item, list):
                                    raw  = item[0] if item else ""
                                    name = item[1] if len(item) > 1 else ""
                                else: continue
                                code = raw.lstrip("A") if raw.startswith("A") else raw
                                if code and code.isdigit() and code not in seen:
                                    seen.add(code)
                                    codes.append((code, name.strip(), source))
                                    fetched += 1
                            if res.get("cont_yn") != "Y": break
                        except asyncio.TimeoutError: break

                    label = "🏭업종" if is_sector else ("🎯테마" if is_theme else "🆕new")
                    print(f"  {label} [{grp_no}]{grp_name}: +{fetched}개")

        except Exception as e:
            print(f"⚠️ 키움 WebSocket 관심그룹 오류: {e}")

        s = sum(1 for _, _, src in codes if src == "hts_sector")
        t = sum(1 for _, _, src in codes if src == "hts_theme")
        n = sum(1 for _, _, src in codes if src == "hts_new")
        print(f"✅ 키움 관심그룹 총 {len(codes)}개 (업종:{s} 테마:{t} new:{n})")
        return codes
