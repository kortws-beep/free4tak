"""
kis_websocket.py — 한국투자증권 웹소켓 실시간 잔고/체결 수신
================================================================
[하는 일]
- 웹소켓 접속키 발급 (24시간 유효, 세션 유지 시 재발급 불필요)
- 실시간 체결 통보 (H0STCNI0) → 포지션 자동 업데이트
- 실시간 잔고 조회로 REST API 호출 횟수 제한 문제 해결

[사용법]
    from kis_websocket import KisWebSocket

    ws = KisWebSocket(appkey=..., secret=..., cano=..., acnt=...)
    ws.start()  # 백그라운드 스레드로 실행

    # 실시간 포지션 참조
    positions = ws.positions  # {"종목코드": {"entry_price": ..., "qty": ...}}
    cash      = ws.cash       # 예수금

[한투 웹소켓 API]
- 접속키 발급: POST /oauth2/Approval
- 웹소켓 URL:  wss://ops.koreainvestment.com:21000
- 체결 통보:   H0STCNI0 (실전) / H0STCNI9 (모의)
================================================================
"""
import os
import json
import time
import threading
import datetime
import requests

try:
    import websocket
    WS_AVAILABLE = True
except ImportError:
    WS_AVAILABLE = False
    print("⚠️ websocket-client 미설치 → pip install websocket-client")


# ── 상수 ───────────────────────────────────────────────────
WS_URL      = "ws://ops.koreainvestment.com:21000"
WS_URL_VTS  = "ws://ops.koreainvestment.com:31000"   # 모의
REST_URL    = "https://openapi.koreainvestment.com:9443"

# 체결 통보 TR ID
TR_체결통보  = "H0STCNI0"   # 실전
TR_체결통보모의 = "H0STCNI9"  # 모의


class KisWebSocket:
    """
    한투 웹소켓 실시간 잔고/체결 수신.

    nbot/sbot에서:
        self.ws = KisWebSocket(appkey=..., secret=..., cano=..., acnt=...)
        self.ws.start()
        # 이후 self.ws.positions, self.ws.cash 참조
    """

    def __init__(self,
                 appkey: str = None,
                 secret: str  = None,
                 cano: str    = None,
                 acnt: str    = None,
                 mock: bool   = False):

        self.appkey  = appkey or os.getenv("KIS_APPKEY")
        self.secret  = secret or os.getenv("KIS_SECRET")
        self.cano    = cano   or os.getenv("KIS_CANO")
        self.acnt    = acnt   or os.getenv("KIS_ACNT_PRDT_CD")
        self.hts_id  = os.getenv("KIS_HTS_ID") or os.getenv("KIS_USER_ID", "")
        self.mock    = mock

        self.ws_url  = WS_URL_VTS if mock else WS_URL
        self.tr_체결  = TR_체결통보모의 if mock else TR_체결통보

        # ── 실시간 데이터 ──────────────────────────────────
        self.positions: dict = {}   # {"코드": {"entry_price": ..., "qty": ...}}
        self.cash: int       = 0
        self.connected: bool = False
        self.last_update: float = 0.0

        # ── 내부 ──────────────────────────────────────────
        self._approval_key: str = ""
        self._ws = None
        self._thread: threading.Thread = None
        self._stop_event = threading.Event()
        self._reconnect_delay = 5   # 재연결 대기 (초)

    # ============================================================
    # 웹소켓 접속키 발급
    # ============================================================
    def _get_approval_key(self) -> str:
        """웹소켓 접속키 발급 (24시간 유효)"""
        url  = f"{REST_URL}/oauth2/Approval"
        body = {
            "grant_type": "client_credentials",
            "appkey":     self.appkey,
            "secretkey":  self.secret,
        }
        try:
            res = requests.post(url, json=body, timeout=10).json()
            key = res.get("approval_key", "")
            if key:
                print(f"✅ [WS] 웹소켓 접속키 발급 완료 [{self.cano}]")
                return key
            else:
                print(f"❌ [WS] 접속키 발급 실패: {res}")
                return ""
        except Exception as e:
            print(f"❌ [WS] 접속키 발급 오류: {e}")
            return ""

    # ============================================================
    # 구독 메시지 생성
    # ============================================================
    def _build_subscribe(self, tr_id: str, tr_key: str) -> str:
        """웹소켓 구독 요청 메시지"""
        return json.dumps({
            "header": {
                "approval_key": self._approval_key,
                "custtype":     "P",
                "tr_type":      "1",   # 1=등록, 2=해제
                "content-type": "utf-8",
            },
            "body": {
                "input": {
                    "tr_id":  tr_id,
                    "tr_key": tr_key,
                }
            }
        })

    # ============================================================
    # 메시지 파싱
    # ============================================================
    def _parse_message(self, msg: str):
        """수신 메시지 파싱 → positions/cash 업데이트"""
        try:
            # PINGPONG 처리
            if msg == "PINGPONG":
                if self._ws:
                    self._ws.send("PINGPONG")
                return

            # JSON 형태 (구독 응답 등)
            if msg.startswith("{"):
                data = json.loads(msg)
                rt_cd = data.get("header", {}).get("tr_id", "")
                body  = data.get("body", {})
                if body.get("rt_cd") == "0":
                    print(f"✅ [WS] 구독 완료: {rt_cd}")
                elif body.get("rt_cd"):
                    print(f"⚠️ [WS] 구독 응답: {body.get('msg1', '')}")
                return

            # 실시간 데이터 (|로 구분)
            # 형식: TR_ID|암호화여부|데이터수|필드1|필드2|...
            parts = msg.split("|")
            if len(parts) < 4:
                return

            tr_id   = parts[0]
            enc_yn  = parts[1]
            data_cnt = int(parts[2]) if parts[2].isdigit() else 1
            data    = parts[3]

            if tr_id in (TR_체결통보, TR_체결통보모의):
                self._parse_체결통보(data)

        except Exception as e:
            print(f"⚠️ [WS] 메시지 파싱 오류: {e}")

    def _parse_체결통보(self, data: str):
        """
        체결 통보 파싱 → positions 업데이트
        H0STCNI0 필드 순서 (주요):
        0:  계좌번호
        1:  주문번호
        2:  원주문번호
        3:  매도매수구분 (01=매도, 02=매수)
        7:  종목코드
        8:  종목명
        9:  체결수량
        10: 체결단가
        11: 체결금액
        13: 주문수량
        14: 주문단가
        19: 체결구분 (0=정상, 1=정정, 2=취소)
        20: 매수평균가
        23: 예수금
        24: 주문가능금액
        """
        try:
            fields = data.split("^")
            if len(fields) < 25:
                return

            acct     = fields[0]
            if acct != self.cano:
                return  # 다른 계좌 무시

            side     = fields[3]   # 01=매도, 02=매수
            code     = fields[7]
            name     = fields[8]
            qty      = int(fields[9])   if fields[9].isdigit()  else 0
            price    = float(fields[10]) if fields[10] else 0
            avg_price = float(fields[20]) if fields[20] else 0
            cash_str = fields[23] if len(fields) > 23 else "0"
            cash_val = int(cash_str.replace(",", "")) if cash_str.replace(",","").isdigit() else 0

            if cash_val > 0:
                self.cash = cash_val

            if qty <= 0:
                return

            if side == "02":  # 매수 체결
                if code in self.positions:
                    # 추가 매수 → 평단 재계산
                    old = self.positions[code]
                    total_qty  = old["qty"] + qty
                    total_cost = old["entry_price"] * old["qty"] + price * qty
                    self.positions[code] = {
                        "entry_price": total_cost / total_qty,
                        "qty":         total_qty,
                        "name":        name,
                    }
                else:
                    self.positions[code] = {
                        "entry_price": avg_price or price,
                        "qty":         qty,
                        "name":        name,
                    }
                print(f"✅ [WS] 매수 체결: {code}({name}) {qty}주 @{price:,.0f}원")

            elif side == "01":  # 매도 체결
                if code in self.positions:
                    remaining = self.positions[code]["qty"] - qty
                    if remaining <= 0:
                        del self.positions[code]
                        print(f"✅ [WS] 전량 매도: {code}({name})")
                    else:
                        self.positions[code]["qty"] = remaining
                        print(f"✅ [WS] 일부 매도: {code}({name}) 잔여 {remaining}주")

            self.last_update = time.time()

        except Exception as e:
            print(f"⚠️ [WS] 체결통보 파싱 오류: {e} | {data[:100]}")

    # ============================================================
    # 웹소켓 이벤트 핸들러
    # ============================================================
    def _on_open(self, ws):
        print(f"✅ [WS] 웹소켓 연결됨 [{self.cano}]")
        self.connected = True

        # 체결 통보 구독 (HTS ID로 구독)
        ws.send(self._build_subscribe(self.tr_체결, self.hts_id))

    def _on_message(self, ws, message):
        self._parse_message(message)

    def _on_error(self, ws, error):
        print(f"⚠️ [WS] 오류: {error}")
        self.connected = False

    def _on_close(self, ws, close_status_code, close_msg):
        print(f"🔌 [WS] 연결 종료 [{self.cano}] code={close_status_code}")
        self.connected = False

    # ============================================================
    # 초기 잔고 로드 (REST API로 시작 시 1회)
    # ============================================================
    def _load_initial_positions(self):
        """시작 시 REST API로 현재 잔고 로드 (웹소켓 연결 전 초기값)"""
        try:
            from kis_api import KisAPI
            api = KisAPI(
                appkey=self.appkey, secret=self.secret,
                cano=self.cano,     acnt=self.acnt
            )
            pos  = api.get_current_positions()
            cash = api.get_buyable_cash()
            if pos:
                self.positions = pos
                print(f"✅ [WS] 초기 잔고 로드: {len(pos)}종목")
            if cash:
                self.cash = cash
        except Exception as e:
            print(f"⚠️ [WS] 초기 잔고 로드 오류: {e}")

    # ============================================================
    # 연결 실행
    # ============================================================
    def _run(self):
        """웹소켓 연결 루프 (재연결 포함)"""
        if not WS_AVAILABLE:
            print("❌ [WS] websocket-client 미설치")
            return

        # 초기 잔고 로드
        self._load_initial_positions()

        # 접속키 발급
        self._approval_key = self._get_approval_key()
        if not self._approval_key:
            print("❌ [WS] 접속키 발급 실패 — 웹소켓 연결 불가")
            return

        while not self._stop_event.is_set():
            try:
                print(f"🔌 [WS] 연결 시도... [{self.cano}]")
                self._ws = websocket.WebSocketApp(
                    self.ws_url,
                    on_open    = self._on_open,
                    on_message = self._on_message,
                    on_error   = self._on_error,
                    on_close   = self._on_close,
                )
                self._ws.run_forever(
                    ping_interval=30,
                    ping_timeout=10,
                )
            except Exception as e:
                print(f"⚠️ [WS] 연결 오류: {e}")

            if self._stop_event.is_set():
                break

            print(f"🔄 [WS] {self._reconnect_delay}초 후 재연결...")
            time.sleep(self._reconnect_delay)

    def start(self):
        """백그라운드 스레드로 웹소켓 시작"""
        if not WS_AVAILABLE:
            print("❌ websocket-client 없음 → pip install websocket-client")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        print(f"🚀 [WS] 웹소켓 시작 [{self.cano}]")

        # 연결 대기 (최대 10초)
        for _ in range(20):
            if self.connected:
                break
            time.sleep(0.5)

    def stop(self):
        """웹소켓 종료"""
        self._stop_event.set()
        if self._ws:
            self._ws.close()
        print(f"🛑 [WS] 웹소켓 종료 [{self.cano}]")

    def is_healthy(self) -> bool:
        """웹소켓 연결 상태 확인"""
        return self.connected and (time.time() - self.last_update < 300 or len(self.positions) == 0)


# ============================================================
# 테스트
# ============================================================
if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    ws = KisWebSocket()
    ws.start()

    print("웹소켓 실행 중... (Ctrl+C로 종료)")
    try:
        while True:
            time.sleep(5)
            print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] "
                  f"연결:{ws.connected} | 포지션:{len(ws.positions)}종목 | 예수금:{ws.cash:,}원")
            for code, pos in ws.positions.items():
                print(f"  {code} {pos['qty']}주 @{pos['entry_price']:,.0f}원")
    except KeyboardInterrupt:
        ws.stop()
