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
★ 2026-10-26 대장: "프리장(08~09시)에서 이미 그날 daybot 먹잇감 섹터가 움직인다 — 프리장에선 종목이 아니라 섹터를 잡고,
  본장에서 그 섹터 1~3위를 후보로." → 09시 전에 돌면 NXT 프리장 시세(NXT 거래 종목만, 3종목 이상 테마)로 같은 순위를 남기고,
  상위 10개 테마의 1~3위 종목도 theme_pre_top에 남김(나중에 본장 성적과 맞춰 보려고). cron 평일 08:30·08:45·08:55 추가.
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
PRE_MIN_MEMBERS, PRE_TOP_THEMES, PRE_TOP_STOCKS = 5, 10, 3
# ★ 2026-10-28 순위 방식 통일(대장 인포스탁 [0659] 캡처 3장 대조 + 10-27·28 프리장 대조):
#   ① 테마 평균에서 가장 많이 오른 1종목은 뺌 — 나무가·엠씨넥스(카메라)·케이아이엔엑스·가비아(상한가) 같은 1~2종목이
#      3종목짜리 테마(드론·데이터센터 등)를 1위로 끌어올리던 왜곡. 그런 테마의 1~3위 매수 가정은 대부분 손실·제자리,
#      고르게 오른 반도체(소캠·CXL·DDR5)는 이틀 다 전부 +2.5% 먼저.
#   ② 프리장은 NXT 안 되는 종목도 0%로 넣고 평균(인포스탁 방식 — 프리장 소캠 3.44% vs 인포스탁 3.32%), 테마는 5종목 이상만.
TRIM_TOP = True


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


def rank_themes(themes: dict, quotes: dict, min_members: int = MIN_MEMBERS, fill_zero: bool = False) -> list:
    """[(순위, 테마, 평균등락, 오른비율, 종목수, 대장, 대장등락)] 상위 TOP.
    fill_zero: 시세 없는 소속 종목을 0%로 넣음(프리장 — NXT 안 되는 종목). 평균은 TRIM_TOP이면 최고 1종목 뺌."""
    rows = []
    for theme, members in themes.items():
        q = [(n, quotes[c]["chg"]) for c, n in members if c in quotes]
        if fill_zero:
            if len(members) < min_members or not q:
                continue
            q = q + [(n, 0.0) for c, n in members if c not in quotes]
        elif len(q) < min_members:
            continue
        chs = [ch for _, ch in q]
        avg = (sum(chs) - max(chs)) / (len(chs) - 1) if TRIM_TOP and len(chs) >= 3 else sum(chs) / len(chs)
        up = sum(1 for _, ch in q if ch > 0) / len(q) * 100
        lead = max(q, key=lambda x: x[1])
        rows.append((avg, theme, up, len(q), lead[0], lead[1]))
    rows.sort(reverse=True)
    return [(i + 1, t, round(a, 2), round(u, 1), n, ln, round(lc, 2)) for i, (a, t, u, n, ln, lc) in enumerate(rows[:TOP])]


def pre_top(themes: dict, quotes: dict, ranked: list) -> list:
    """프리장 상위 PRE_TOP_THEMES개 테마의 등락 1~PRE_TOP_STOCKS위 → [(테마순위, 테마, 종목순위, 코드, 이름, 등락, 거래대금, 가격)]."""
    out = []
    for r, theme, *_ in ranked[:PRE_TOP_THEMES]:
        ms = sorted(((c, n) for c, n in themes[theme] if c in quotes), key=lambda x: -quotes[x[0]]["chg"])
        for k, (c, n) in enumerate(ms[:PRE_TOP_STOCKS], 1):
            out.append((r, theme, k, c, n, quotes[c]["chg"], quotes[c].get("value", 0), quotes[c].get("price", 0)))
    return out


def save_pre_top(date: str, hhmm: str, rows: list, log_db: str = LOG_DB):
    conn = sqlite3.connect(log_db, timeout=30)
    conn.execute("CREATE TABLE IF NOT EXISTS theme_pre_top (date TEXT, time TEXT, theme_rank INTEGER, theme TEXT, "
                 "stock_rank INTEGER, code TEXT, name TEXT, chg REAL, value REAL, price REAL, PRIMARY KEY(date, time, theme, code))")
    conn.executemany("INSERT OR REPLACE INTO theme_pre_top VALUES (?,?,?,?,?,?,?,?,?,?)",
                     [(date, hhmm, *r) for r in rows])
    conn.commit()
    conn.close()


def premarket_message(date: str, log_db: str = LOG_DB, n: int = 3) -> str:
    """★ 10-26 대장 — 08:50 리나 아침 알림에 '프리장 섹터: 테마: 1~3위'. 그날 09시 전 마지막 기록으로, 없으면 ""."""
    conn = sqlite3.connect(log_db, timeout=10)
    try:
        t = conn.execute("SELECT MAX(time) FROM theme_pre_top WHERE date=? AND time<'0900'", (date,)).fetchone()[0]
        if not t:
            return ""
        heads = conn.execute("SELECT rank, theme, avg_chg, up_ratio, n FROM theme_open_obs WHERE date=? AND time=? "
                             "AND rank<=? ORDER BY rank", (date, t, n)).fetchall()
        lines = [f"🌅 **프리장 섹터** ({t[:2]}:{t[2:]} NXT 기준 · 본장 09:05에 프리장가 ±3% 안이면 '가는 섹터')"]
        for rank, theme, avg, up, cnt in heads:
            stocks = conn.execute("SELECT name, chg FROM theme_pre_top WHERE date=? AND time=? AND theme=? "
                                  "ORDER BY stock_rank", (date, t, theme)).fetchall()
            lines.append(f"   {rank}. **{theme}** {avg:+.1f}%(오른 비율 {up:.0f}%·{cnt}종목): "
                         + " · ".join(f"{nm} {ch:+.1f}%" for nm, ch in stocks))
        return "\n".join(lines) if heads else ""
    except sqlite3.OperationalError:
        return ""
    finally:
        conn.close()


def save(date: str, hhmm: str, ranked: list, log_db: str = LOG_DB):
    conn = sqlite3.connect(log_db, timeout=30)
    conn.execute("CREATE TABLE IF NOT EXISTS theme_open_obs (date TEXT, time TEXT, rank INTEGER, theme TEXT, avg_chg REAL, "
                 "up_ratio REAL, n INTEGER, leader TEXT, leader_chg REAL, PRIMARY KEY(date, time, theme))")
    conn.executemany("INSERT OR REPLACE INTO theme_open_obs VALUES (?,?,?,?,?,?,?,?,?)",
                     [(date, hhmm, r, t, a, u, n, ln, lc) for r, t, a, u, n, ln, lc in ranked])
    conn.commit()
    conn.close()


def run(api=None, now: datetime.datetime = None) -> str:
    """한 번 기록(09시 전이면 프리장 NXT, 아니면 본장). 반환: 한 줄 요약(실패면 ""). ★ 10-30: 리나가 장중 15분마다 부르려고 함수로."""
    import capitulation_alert as C
    now = now or datetime.datetime.now()
    if api is None:
        from kis_api import KisAPI
        api = KisAPI()
    themes = load_themes()
    codes = sorted({c for ms in themes.values() for c, _ in ms})
    pre = now.strftime("%H%M") < "0900"
    if pre:   # ★ 10-26 프리장 — NXT 시세만(가격 0 = NXT 안 되는 종목은 뺌)
        quotes = {c: q for c, q in api.get_multi_price(codes, pause=0.15, market="NX").items() if q.get("price", 0) > 0}
        ranked = rank_themes(themes, quotes, PRE_MIN_MEMBERS, fill_zero=True)
    else:
        quotes = C.snapshot(api, codes, pause=0.15)        # 30개씩 복수시세 — 장중 다른 봇과 호출 한도 나눠 쓰니 천천히
        ranked = rank_themes(themes, quotes)
    if not ranked:
        print(f"{now:%H:%M} 시세 부족 — 기록 안 함 ({len(quotes)}/{len(codes)}종목)")
        return ""
    save(now.date().isoformat(), now.strftime("%H%M"), ranked)
    if pre:
        save_pre_top(now.date().isoformat(), now.strftime("%H%M"), pre_top(themes, quotes, ranked))
    line = (f"{now:%Y-%m-%d %H:%M} 주도 테마 기록 {len(ranked)}개 (시세 {len(quotes)}/{len(codes)}종목) — "
            + " · ".join(f"{r}.{t}({a:+.1f}%)" for r, t, a, *_ in ranked[:5]))
    print(line)
    return line


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE, ".env"))
    from kis_api import KisAPI
    _api = KisAPI()
    if _api.is_market_open() is False:
        print(f"{datetime.datetime.now():%Y-%m-%d %H:%M} 휴장일 — 기록 안 함")
        sys.exit(0)
    sys.exit(0 if run(_api) else 1)
