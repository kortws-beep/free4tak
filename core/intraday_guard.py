"""
intraday_guard.py — 장중 '하락폭을 키우는 날' 감지기 (2026-10-19, cbot 급락 감지기의 주식판)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
10:00~14:30 사이 1분마다 KODEX200(069500)·KODEX 코스닥150(229200)의 지금가를 시가와 비교해서, 어느 하나라도
시가 대비 -1.5% 아래면 '하락 확대'로 보고 그날 장 끝까지 켜 둔다(backtest/intraday_decline_detector.py R2:
04-01~ 코스피200 하락 확대 날 12/13일 잡음·울린 뒤 평균 -2.56% 더 빠짐 · sbot이 울린 뒤 산 20건 평균 -8% PF 0.20).
켜진 사실은 intraday_guard.json에 저장 — 봇이 재시작돼도, 다른 봇·키키가 읽어도 같은 판단. 조회 실패면 안 켬(막지 않음).
"""
import os
import json
import time
import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(BASE, "intraday_guard.json")
ETFS = {"069500": "코스피200", "229200": "코스닥150"}
DROP = -1.5
START, END = "1000", "1430"
EVERY = 60


class IntradayGuard:
    def __init__(self, api, path: str = STATE):
        self.api, self.path, self._last = api, path, 0.0

    def _read(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def fired(self, today: str) -> dict:
        st = self._read()
        return st if st.get("date") == today else {}

    def check(self, today: str, hhmm: str) -> tuple:
        """(켜짐 사유 문구 또는 "", 이번에 새로 켜졌는지)."""
        st = self.fired(today)
        if st:
            return self.reason(st), False
        if not (START <= hhmm[:4] <= END) or time.time() - self._last < EVERY:
            return "", False
        self._last = time.time()
        for code, name in ETFS.items():
            try:
                d = self.api.get_market_data(code) or {}
                px, op = float(d.get("stck_prpr") or 0), float(d.get("stck_oprc") or 0)
            except Exception as e:
                print(f"⚠️ 장중 하락감지 조회 오류 {code}: {e}")
                continue
            if px > 0 and op > 0 and (px / op - 1) * 100 <= DROP:
                st = {"date": today, "at": hhmm[:4], "etf": name, "drop": round((px / op - 1) * 100, 2)}
                tmp = self.path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(st, f, ensure_ascii=False)
                os.replace(tmp, self.path)
                return self.reason(st), True
        return "", False

    @staticmethod
    def reason(st: dict) -> str:
        return f"장중 하락 확대({st['etf']} 시가 대비 {st['drop']:+.1f}%, {st['at'][:2]}:{st['at'][2:]}부터)"


def today_str() -> str:
    return datetime.date.today().isoformat()
