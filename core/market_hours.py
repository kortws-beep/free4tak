"""
market_hours.py — 개장·마감 시각이 평소와 다른 날(수능일 등) 달력 (2026-11-19, 대장 "수능이라 4시30분까지인데 리나는 3시20분까지로 생각")
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
한국 증시는 보통 09:00~15:30인데, 수능일엔 1시간 늦게(10:00~16:30), 새해 첫 거래일엔 10:00에 열리는 등 시각이 바뀌는 날이 있음.
봇·리나 곳곳에 "0900"·"1520" 같은 시각이 박혀 있어서 그런 날엔 장이 열려 있는데 매수를 거절하거나, 장 열리기 전에 움직였음.
→ 그런 날만 여기 적어 두고, 시각을 쓰는 곳은 shifted("0900") 처럼 감싸서 그날 맞게 밀어 씀.
날짜는 거래소(KRX) 공지가 나오면 추가(수능일은 보통 11월 셋째 목요일 근처, 해마다 교육부 발표).
"""
import datetime

# 날짜: (개장 지연 분, 마감 지연 분, 이유)
SPECIAL_DAYS = {
    "2026-11-19": (60, 60, "수능일 10:00~16:30"),
    "2027-01-04": (60, 0, "새해 첫 거래일 10:00 개장"),
}


def shift_for(day: str = None) -> tuple:
    """(개장 지연 분, 마감 지연 분, 이유) — 평소엔 (0, 0, "")."""
    day = day or datetime.date.today().isoformat()
    return SPECIAL_DAYS.get(day, (0, 0, ""))


def _add(hhmm: str, minutes: int) -> str:
    h, m = int(hhmm[:2]), int(hhmm[2:4])
    t = h * 60 + m + minutes
    return f"{t // 60:02d}{t % 60:02d}"


def shifted(hhmm: str, day: str = None, edge: str = "auto") -> str:
    """평소 기준 시각 'HHMM'을 그날에 맞게 민 값. edge='open'이면 개장 지연만, 'close'면 마감 지연만,
    'auto'면 12시 전 시각은 개장 쪽, 12시 이후는 마감 쪽 지연을 적용(장중 시각 대부분은 이걸로 맞음)."""
    o, c, _ = shift_for(day)
    if edge == "open" or (edge == "auto" and hhmm < "1200"):
        return _add(hhmm, o) if o else hhmm
    return _add(hhmm, c) if c else hhmm


def note(day: str = None) -> str:
    o, c, why = shift_for(day)
    return why
