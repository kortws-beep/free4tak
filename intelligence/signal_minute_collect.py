"""
signal_minute_collect.py — 리나 신호 종목 분봉을 신호 날부터 3거래일 수집 (2026-11-14, 대장 아이디어)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
대장: "리나가 [단타000]·[주도주검색식3] 같은 걸 보내오잖아. 나중에 검증하려면 그 시점부터 해당 종목만 분봉을 모으면?
3일까지 보유로 보면 3일 동안." → 장중엔 아무것도 안 하고, 장 마감 뒤(리나 16:05) 한 번에:
  · 대상 = 최근 3거래일(오늘 포함) 안에 신호가 뜬 종목
      - 리나 검색식 통과 기록(three_month_leader_log.db: 주도주·단타000·3개월수급·0909)
      - daybot 후보 로그·실매수(daybot_trade_history.db)
  · 그 종목들의 '오늘' 1분봉을 한투에서 받아 backtest/data/minute_cache.db에 저장
  · ★ 출처 구분(대장): 종목마다 '왜 받았나'(검색식 이름·daybot 후보/매수, 신호 날짜·시각)를 signal_targets 표에 따로 남김
    (daybot_replay.py 분봉 재현 엔진이 쓰는 그 캐시 — 이미 받은 날은 건너뜀)
규모(11월 기준): 신호 종목 하루 평균 약 55개 × 3일 ≈ 165종목, 종목당 한투 4회 안팎 → 1~2분, 하루 수 MB.
실행: venv/bin/python intelligence/signal_minute_collect.py [YYYY-MM-DD]   (리나가 평일 16:05에 자동 호출)
"""
import datetime
import os
import sqlite3
import sys

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("core", "backtest"):
    if os.path.join(_BASE, _p) not in sys.path:
        sys.path.insert(0, os.path.join(_BASE, _p))

LEADER_DB = os.path.join(_BASE, "lina_bot", "three_month_leader_log.db")
DAY_DB = os.path.join(_BASE, "daybot_trade_history.db")
OBS_TABLES = ("leader_obs", "danta_obs", "tml_obs", "obs0909")
TRACK_DAYS = 3          # 신호 날 포함 3거래일


def recent_trading_days(store, today: str, n: int) -> list:
    """오늘 포함 최근 n거래일(평일 중 분봉 캐시나 지수 기록이 있는 날 — 휴장일 건너뜀). 오늘은 무조건 포함."""
    days, d = [today], datetime.date.fromisoformat(today)
    conn = sqlite3.connect(LEADER_DB)
    try:
        known = {r[0] for r in conn.execute("SELECT DISTINCT date FROM market_index")}
    except sqlite3.OperationalError:
        known = set()
    finally:
        conn.close()
    while len(days) < n:
        d -= datetime.timedelta(days=1)
        if d.weekday() < 5 and (not known or d.isoformat() in known):
            days.append(d.isoformat())
    return days


OBS_LABEL = {"leader_obs": "주도주검색식3", "danta_obs": "단타000", "tml_obs": "3개월수급", "obs0909": "090930타점"}


def signal_rows(since: str) -> list:
    """★ 11-14 대장 "출처는 명확히 구분" — [(code, source, signal_date, signal_time)] 종목·출처·날짜별 첫 신호.
    source: 리나 검색식 이름(주도주검색식3·단타000·3개월수급·090930타점) / daybot후보:<소스> / daybot매수:<소스>."""
    out = []
    conn = sqlite3.connect(LEADER_DB)
    for t in OBS_TABLES:
        try:
            out += [(c, OBS_LABEL[t], d, tm) for c, d, tm in conn.execute(
                f"SELECT code, date, MIN(time) FROM {t} WHERE passed=1 AND date>=? GROUP BY code, date", (since,))]
        except sqlite3.OperationalError:
            pass
    conn.close()
    conn = sqlite3.connect(DAY_DB)
    for sql, lab in (("SELECT code, substr(ts,1,10), MIN(substr(ts,12,8)), COALESCE(source_tier,'') FROM candidate_log "
                      "WHERE substr(ts,1,10)>=? GROUP BY code, substr(ts,1,10), source_tier", "daybot후보"),
                     ("SELECT code, substr(buy_time,1,10), MIN(substr(buy_time,12,8)), COALESCE(buy_tag,'') FROM trades "
                      "WHERE substr(buy_time,1,10)>=? GROUP BY code, substr(buy_time,1,10), buy_tag", "daybot매수")):
        try:
            out += [(c, f"{lab}:{tag}" if tag else lab, d, tm) for c, d, tm, tag in conn.execute(sql, (since,))]
        except sqlite3.OperationalError:
            pass
    conn.close()
    return [r for r in out if r[0] and len(r[0]) == 6]


def signal_codes(since: str) -> set:
    return {r[0] for r in signal_rows(since)}


def save_targets(store, today: str, rows: list):
    """minute_cache.db signal_targets — 그날 분봉을 '왜' 받았는지(출처·신호 날짜·시각). 분봉(bars)은 전부 한투 1분봉
    (FHKST03010230)이고, daybot_replay가 백테스트 중에 받은 것은 여기 기록이 없음(= 신호 수집이 아님)."""
    store.conn.execute("CREATE TABLE IF NOT EXISTS signal_targets (collect_date TEXT, code TEXT, source TEXT, "
                       "signal_date TEXT, signal_time TEXT, PRIMARY KEY(collect_date, code, source, signal_date))")
    store.conn.executemany("INSERT OR IGNORE INTO signal_targets VALUES (?,?,?,?,?)",
                           [(today, c, src, d, tm) for c, src, d, tm in rows])
    store.conn.commit()


def collect(today: str = None, api=None) -> tuple:
    """→ (대상 종목 수, 새로 받은 종목 수, 한투 호출 수)."""
    import daybot_replay as dr
    today = today or datetime.date.today().isoformat()
    if api is None:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(_BASE, ".env"))
        from kis_api import KisAPI
        api = KisAPI()
    store = dr.MinuteStore(api)
    days = recent_trading_days(store, today, TRACK_DAYS)
    rows = signal_rows(min(days))
    save_targets(store, today, rows)
    codes = sorted({r[0] for r in rows})
    got = 0
    for code in codes:
        if store.conn.execute("SELECT 1 FROM done WHERE code=? AND date=?", (code, today)).fetchone():
            continue
        if store.day(code, today):
            got += 1
    return len(codes), got, store.calls


if __name__ == "__main__":
    day = sys.argv[1] if len(sys.argv) > 1 else None
    n, got, calls = collect(day)
    print(f"✅ 신호 종목 분봉: 대상 {n}개 · 새로 받음 {got}개 · 한투 호출 {calls}회")
