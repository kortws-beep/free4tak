"""
sell_at_breakeven.py — 대장이 미리 OK한 '본전 이상이면 매도' 1회성 감시 (2026-11-11, 대장 B안)
================================================================
[하는 일 — 비개발자용]
지정한 봇(sbot 등)이 들고 있는 종목 하나를, 정규장(09:00~15:20) 동안 30초마다 현재가를 보다가
기준가(수수료·세금 포함 본전) 이상이 되는 순간 키키 "!s매도"와 똑같은 방식(pending_cmd)으로
그 봇에 매도 명령을 넣고 결과를 기다린 뒤 끝남.
- 봇이 먼저 팔았거나(손절 등) 대장이 직접 팔아서 보유가 없어지면 그냥 끝남.
- 15:20까지 기준가에 못 닿으면 아무것도 안 하고 끝남(대장이 직접 판단).
실행: venv/bin/python scripts/sell_at_breakeven.py sbot 000120 67900 [날짜 YYYY-MM-DD, 기본 오늘 — 그날 장까지 기다림]
"""
import os
import sys
import time
import datetime

import requests

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "core"))
from common_utils import read_state, update_state  # noqa: E402

STATE = {"sbot": "sbot_state.json", "cbot": "cbot_state.json", "daybot": "daybot_state.json"}


def held(bot: str, code: str) -> bool:
    st = read_state(os.path.join(BASE, STATE[bot]), default={})
    pos = dict((st.get("last_status") or {}).get("positions_detail", {}))
    pos.update(st.get("positions", {}) or {})
    return code in pos


def price(code: str) -> float:
    d = requests.get(f"https://m.stock.naver.com/api/stock/{code}/basic",
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=5).json()
    return float(str(d.get("closePrice", "0")).replace(",", "") or 0)


def log(msg: str):
    print(f"[{datetime.datetime.now():%H:%M:%S}] {msg}", flush=True)


def main():
    bot, code, target = sys.argv[1], sys.argv[2], float(sys.argv[3])
    day = sys.argv[4] if len(sys.argv) > 4 else datetime.date.today().isoformat()
    path = os.path.join(BASE, STATE[bot])
    log(f"감시 시작 — {day} {bot} {code} 기준가 {target:,.0f}원 이상이면 매도")
    while datetime.date.today().isoformat() < day:
        time.sleep(60)
    while True:
        now = datetime.datetime.now().strftime("%H%M")
        if now >= "1520":
            log("15:20 — 기준가 못 닿음, 매도 안 함(대장 판단)")
            return
        if not held(bot, code):
            log("보유 없음 — 이미 팔림(봇 규칙이나 대장 직접 매도), 끝")
            return
        if now >= "0900":
            try:
                p = price(code)
            except Exception as e:
                log(f"시세 조회 오류: {e}")
                p = 0
            if p >= target:
                log(f"현재가 {p:,.0f} ≥ {target:,.0f} — 매도 명령 전달")
                update_state(path, pending_cmd={"type": "sell", "code": code}, cmd_result=None)
                for _ in range(40):
                    time.sleep(3)
                    r = read_state(path, default={}).get("cmd_result")
                    if r:
                        log(f"봇 응답: {r}")
                        return
                log("봇 응답 없음(2분) — 대장 확인 필요")
                return
        time.sleep(30)


if __name__ == "__main__":
    main()
