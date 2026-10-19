"""
theme_open_obs.py — 장 초반 '오늘의 주도 테마' 기록 (2026-10-19, 관찰 전용)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
대장 아이디어: "프리장~09:15 상황을 봐서 daybot 후보 중 오늘의 주도 섹터만 09:16~09:50에 사면 범위도 좁고 안정적이지
않을까 — 섹터 변화 확인은 필수." 지금 섹터 순위 기록(리나 sector_obs)은 관심그룹 28개뿐이라 daybot 후보 대부분이 빠지고,
게다가 지금까지 데이터는 하락장뿐 → 보합·상승장까지 쌓아서 나중에 검증하려고, 리나 테마 DB 전체(약 320테마·2,100종목)의
테마별 강도를 09:15·09:30·09:45에 한 번씩 three_month_leader_log.db의 theme_open_obs에 남긴다.
  테마 강도 = 시세 받은 소속 종목(5개 이상)의 전일 대비 등락률 평균 → 순위 1~30위 + 오른 종목 비율 + 대장(가장 많이 오른 종목)
매매·봇 설정은 건드리지 않음. 휴장일이면 아무것도 안 함. 시세는 복수시세(KRX 기준, 프리장 NXT 가격은 안 잡힘).
cron 평일 09:15·09:30·09:45.  실행:  venv/bin/python intelligence/theme_open_obs.py
"""
import os
import re
import sys
import sqlite3
import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "core"))
sys.path.insert(0, os.path.join(BASE, "intelligence"))
THEME_DB = os.path.join(BASE, "lina_bot", "kr_theme_finance.db")
LOG_DB = os.path.join(BASE, "lina_bot", "three_month_leader_log.db")
TOP, MIN_MEMBERS = 30, 5


def load_themes(theme_db: str = THEME_DB) -> dict:
    """{테마: [(코드, 종목명)]} — 이름 끝의 'KOSPI 005930' 꼴에서 코드를 뽑음."""
    conn = sqlite3.connect(f"file:{theme_db}?mode=ro", uri=True)
    out = {}
    for theme, raw in conn.execute("SELECT theme_name, stock_name FROM kr_theme_stocks"):
        m = re.search(r"([0-9A-Z]{6})$", (raw or "").strip())
        if m:
            name = re.sub(r"\s*KOS(?:PI|DAQ)\s*[0-9A-Z]{6}$", "", raw).strip()
            out.setdefault(theme, []).append((m.group(1), name))
    conn.close()
    return out


def rank_themes(themes: dict, quotes: dict) -> list:
    """[(순위, 테마, 평균등락, 오른비율, 종목수, 대장, 대장등락)] 상위 TOP."""
    rows = []
    for theme, members in themes.items():
        q = [(n, quotes[c]["chg"]) for c, n in members if c in quotes]
        if len(q) < MIN_MEMBERS:
            continue
        avg = sum(ch for _, ch in q) / len(q)
        up = sum(1 for _, ch in q if ch > 0) / len(q) * 100
        lead = max(q, key=lambda x: x[1])
        rows.append((avg, theme, up, len(q), lead[0], lead[1]))
    rows.sort(reverse=True)
    return [(i + 1, t, round(a, 2), round(u, 1), n, ln, round(lc, 2)) for i, (a, t, u, n, ln, lc) in enumerate(rows[:TOP])]


def save(date: str, hhmm: str, ranked: list, log_db: str = LOG_DB):
    conn = sqlite3.connect(log_db, timeout=30)
    conn.execute("CREATE TABLE IF NOT EXISTS theme_open_obs (date TEXT, time TEXT, rank INTEGER, theme TEXT, avg_chg REAL, "
                 "up_ratio REAL, n INTEGER, leader TEXT, leader_chg REAL, PRIMARY KEY(date, time, theme))")
    conn.executemany("INSERT OR REPLACE INTO theme_open_obs VALUES (?,?,?,?,?,?,?,?,?)",
                     [(date, hhmm, r, t, a, u, n, ln, lc) for r, t, a, u, n, ln, lc in ranked])
    conn.commit()
    conn.close()


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE, ".env"))
    from kis_api import KisAPI
    import capitulation_alert as C
    now = datetime.datetime.now()
    api = KisAPI()
    if api.is_market_open() is False:
        print(f"{now:%Y-%m-%d %H:%M} 휴장일 — 기록 안 함")
        sys.exit(0)
    themes = load_themes()
    codes = sorted({c for ms in themes.values() for c, _ in ms})
    quotes = C.snapshot(api, codes, pause=0.15)            # 30개씩 복수시세 — 장중 다른 봇과 호출 한도 나눠 쓰니 천천히
    ranked = rank_themes(themes, quotes)
    if not ranked:
        print(f"{now:%H:%M} 시세 부족 — 기록 안 함 ({len(quotes)}/{len(codes)}종목)")
        sys.exit(1)
    save(now.date().isoformat(), now.strftime("%H%M"), ranked)
    print(f"{now:%Y-%m-%d %H:%M} 주도 테마 기록 {len(ranked)}개 (시세 {len(quotes)}/{len(codes)}종목) — "
          + " · ".join(f"{r}.{t}({a:+.1f}%)" for r, t, a, *_ in ranked[:5]))
