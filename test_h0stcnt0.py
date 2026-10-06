"""
test_h0stcnt0.py — H0STCNT0(실시간 체결가) 필드 레이아웃 검증용 1회성 스크립트
================================================================
daybot 배포 전 최우선 검증 항목: core/kis_websocket.py의 _parse_체결가()가
가정한 "필드 index 2 = STCK_PRPR(현재가)"가 실제로 맞는지 확인.

사용법 (장중 09:00~15:30에 실행, 유동성 좋은 종목이라 삼성전자로 테스트):
    cd /home/free4tak/k-bot/stock_bot
    venv/bin/python3 /tmp/.../test_h0stcnt0.py

하는 일:
1. sbo2 계좌 credential로 KisWebSocket 연결(주문 없음, 순수 시세구독만
   — 계좌에 어떤 영향도 없음)
2. 삼성전자(005930) 실시간 체결가(H0STCNT0) 구독
3. RAW 메시지 전체를 그대로 출력 (필드 순서 육안 확인용)
4. 파싱된 현재가(live_prices)와 REST API(get_market_data) 현재가를
   나란히 출력해서 서로 일치하는지 대조

Ctrl+C로 종료. 매도/매수 주문은 전혀 안 나감 — 100% 안전.
"""
import os
import sys
import time

_BASE = "/home/free4tak/k-bot/stock_bot"
for _d in ["core", ""]:
    _p = os.path.join(_BASE, _d)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from dotenv import load_dotenv
load_dotenv(os.path.join(_BASE, ".env"))

import kis_websocket
from kis_websocket import KisWebSocket
from kis_api import KisAPI

TEST_CODE = "005930"  # 삼성전자 — 유동성 높아 틱이 자주 옴


class DebugKisWebSocket(KisWebSocket):
    """RAW 메시지를 먼저 찍고 나서 원래 파싱 로직을 그대로 태움."""

    def _parse_message(self, msg: str):
        # ★ 라우팅 단계 자체를 확인 — tr_id가 뭐로 오는지, 우리가
        #   기대하는 상수(H0STCNT0)와 실제로 매칭되는지 직접 확인.
        if msg == "PINGPONG":
            print("[MSG] PINGPONG")
        elif msg.startswith("{"):
            print(f"[MSG-JSON-FULL] {msg}")
        else:
            parts = msg.split("|")
            tr_id = parts[0] if parts else "?"
            print(f"[MSG-REALTIME] tr_id={tr_id!r} (기대값 H0STCNT0과 "
                  f"일치: {tr_id == kis_websocket.TR_체결가}) | 전체앞부분: {msg[:150]}")
        super()._parse_message(msg)

    def _parse_체결가(self, data: str):
        fields = data.split("^")
        print(f"\n[RAW] 필드수={len(fields)}")
        for i, f in enumerate(fields[:15]):   # 앞쪽 15개 필드만 (너무 길어서)
            print(f"  [{i}] {f}")
        super()._parse_체결가(data)


def main():
    ws = DebugKisWebSocket(
        appkey=os.getenv("KIS_APPKEY"),
        secret=os.getenv("KIS_SECRET"),
        cano  =os.getenv("KIS_CANO"),
        acnt  =os.getenv("KIS_ACNT_PRDT_CD"),
    )
    ws.start()

    for _ in range(20):
        if ws.connected:
            break
        time.sleep(0.5)

    if not ws.connected:
        print("❌ 웹소켓 연결 실패 — 접속키/네트워크 확인 필요")
        return

    print(f"✅ 연결됨 — {TEST_CODE}(삼성전자) 실시간 체결가 구독 시작")
    ws.subscribe_price(TEST_CODE)

    api = KisAPI(
        appkey=os.getenv("KIS_APPKEY"), secret=os.getenv("KIS_SECRET"),
        cano=os.getenv("KIS_CANO"), acnt=os.getenv("KIS_ACNT_PRDT_CD"),
    )

    print("Ctrl+C로 종료. 5초마다 [실시간파싱값] vs [REST API값] 대조 출력...")
    try:
        while True:
            time.sleep(5)
            tick = ws.live_prices.get(TEST_CODE)
            ws_price = tick["price"] if tick else None
            rest = api.get_market_data(TEST_CODE) or {}
            rest_price = rest.get("stck_prpr")
            match = "✅ 일치" if (ws_price and rest_price and
                                  abs(float(ws_price) - float(rest_price)) < 1) else "⚠️ 불일치/미수신"
            print(f"[대조] WS파싱:{ws_price} | REST:{rest_price} | {match}")
    except KeyboardInterrupt:
        ws.stop()


if __name__ == "__main__":
    main()
