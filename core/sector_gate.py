"""
sector_gate.py — 데이봇 하락장 슬롯 '주도 섹터 안 종목만' 관문 (2026-10-30, 대장 결정)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
10-30 검증(backtest/daybot_entry_timing.py 이어서): 10-06~29 데이봇 후보 97건 중 '그날 프리장 상위 5 테마' 소속은
11건 7승 2패 기대 +0.59%/건(앞 +0.50·뒤 +0.63), 소속 아니면 86건 -0.48% — 같은 검색식 후보라도 주도 섹터 안이면 플러스.
대장: "프리장에선 섹터를 잡고 본장에선 그 섹터 종목을" + 매수 마감 09:40 → 15:20으로 풀면서 본장에선 섹터가 바뀌면 따라감.
  09:15 전: 오늘 프리장(09시 전) 마지막 기록의 상위 TOP 테마
  09:15부터: 오늘 본장 기록 중 지금 시각까지의 마지막 것(리나가 15분마다 남김) — 아직 없으면 프리장
  테마 순위는 theme_open_obs(최고 1종목 빼고 평균·5종목↑·프리장은 NXT 아닌 종목 0%).
순위 기록이 아예 없으면 막지 않음(None — 데이터 문제로 매매가 멈추지 않게, 로그로만 알림).
"""
import os
import sqlite3
import datetime

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_DB = os.path.join(_BASE, "lina_bot", "three_month_leader_log.db")
TOP = 5
SWITCH_AT = "0915"


def pick_time(times: list, now_hhmm: str):
    """오늘 기록 시각들(HHMM) → 지금 쓸 기록 시각. 09:15 전엔 프리장(09시 전) 마지막, 뒤엔 지금까지의 본장 마지막(없으면 프리장)."""
    pre = [t for t in times if t < "0900"]
    reg = [t for t in times if "0900" <= t <= now_hhmm]
    if now_hhmm >= SWITCH_AT and reg:
        return max(reg)
    return max(pre) if pre else None


class SectorGate:
    def __init__(self, log_db: str = LOG_DB, themes_loader=None, top: int = TOP):
        self.log_db, self.top = log_db, top
        self._loader = themes_loader
        self._members = None            # {테마: {코드}}
        self._cache = (None, None, None)  # (날짜, 기록시각, (테마들, 코드들))

    def _themes(self) -> dict:
        if self._members is None:
            if self._loader is None:
                import theme_open_obs
                self._loader = theme_open_obs.load_themes
            self._members = {th: {c for c, _ in ms} for th, ms in self._loader().items()}
        return self._members

    def leading(self, now: datetime.datetime = None):
        """→ (기록 시각, [상위 테마], {소속 코드}) / 기록 없으면 (None, None, None)."""
        now = now or datetime.datetime.now()
        date, hhmm = now.date().isoformat(), now.strftime("%H%M")
        try:
            conn = sqlite3.connect(self.log_db, timeout=5)
            times = [t for (t,) in conn.execute("SELECT DISTINCT time FROM theme_open_obs WHERE date=?", (date,))]
            t = pick_time(times, hhmm)
            if t is None:
                conn.close()
                return None, None, None
            if self._cache[0] == date and self._cache[1] == t:
                conn.close()
                return (t,) + self._cache[2]
            themes = [th for (th,) in conn.execute("SELECT theme FROM theme_open_obs WHERE date=? AND time=? AND rank<=? "
                                                   "ORDER BY rank", (date, t, self.top))]
            conn.close()
        except sqlite3.Error:
            return None, None, None
        mem = self._themes()
        codes = set().union(*(mem.get(th, set()) for th in themes)) if themes else set()
        self._cache = (date, t, (themes, codes))
        return t, themes, codes

    def allows(self, code: str, now: datetime.datetime = None):
        """→ (통과?, 사유). 기록 없으면 통과(사유에 표시)."""
        t, themes, codes = self.leading(now)
        if t is None:
            return True, "섹터순위 없음(막지 않음)"
        if code in codes:
            return True, f"{t[:2]}:{t[2:]} 주도섹터"
        return False, f"섹터밖({t[:2]}:{t[2:]} 상위{self.top}: {'·'.join(themes)})"
