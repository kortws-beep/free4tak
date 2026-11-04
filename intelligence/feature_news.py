"""
feature_news.py — '[특징주]' 뉴스를 시각과 함께 모으기 (2026-11-05, 대장)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
횡보장 '개별 특징주' 엔진 검증(backtest/feature_stock.py)을 가격만으로 했더니 진짜 재료와 그냥 펌핑을 못 가렸음 →
어느 종목이 몇 시에 무슨 재료로 '[특징주]' 기사가 났는지를 지금부터 쌓는다(매매 안 함).
  네이버 뉴스 검색 API(query=특징주, 최신순, 100개씩 최대 1000개) — 이미 저장한 기사가 나오면 멈춤.
  종목: 제목의 ']' 뒤 첫 이름(쉼표 앞)을 종목 마스터에서 찾고, 없으면 제목 안 가장 긴 종목명.
  등락: 제목의 'N%' · 방향(상승/급등/강세/상한가 ↔ 하락/급락/약세/하한가).
  저장: intelligence/feature_news.db 테이블 news(link 기준 중복 없음). 미국 특징주 기사는 뺌.
리나가 거래일 08:00~18:30 30분마다 돌림. 수동: venv/bin/python intelligence/feature_news.py
"""
import os
import re
import sys
import html
import sqlite3
import datetime
import email.utils

_here = os.path.dirname(os.path.abspath(__file__))
_base = os.path.dirname(_here)
for _d in ["core", "intelligence", "interface", "bots", ""]:
    _p = os.path.join(_base, _d)
    if _p not in sys.path:
        sys.path.insert(0, _p)

DB_PATH = os.path.join(_here, "feature_news.db")
UP = ("상승", "급등", "강세", "상한가", "신고가", "↑", "껑충", "치솟")
US = ("미국 특징주", "美증시", "美 증시", "뉴욕증시", "[美", "해외 특징주", "미국주식", "미국증시", "유럽 특징주", "ETF 특징주")
DOWN = ("하락", "급락", "약세", "하한가", "↓", "추락", "곤두박질")


def _master() -> dict:
    import dip_leader
    c = sqlite3.connect(f"file:{dip_leader.DAILY_DB}?mode=ro", uri=True)
    try:
        return {n.replace(" ", ""): k for k, n in c.execute("SELECT code, name FROM kr_stock_master") if n and len(n) >= 2}
    finally:
        c.close()


def clean(t: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", t or "")).strip()


def parse_title(title: str, master: dict):
    """→ (코드, 이름, 등락%, 방향 +1/-1/0)."""
    t = title.replace(" ", "")
    code = name = None
    m = re.search(r"\]([^,，…]+?)[,，…]", t)
    if m and m.group(1) in master:
        name = m.group(1)
    if not name:   # 2글자 이름('SK'·'NC')은 다른 단어에 엉뚱하게 걸려서(예: GSK) 대괄호 바로 뒤일 때만 인정
        hits = [n for n in master if len(n) >= 3 and n in t]
        name = max(hits, key=len) if hits else None
    if name:
        code = master[name]
    pm = re.search(r"(\d+(?:\.\d+)?)\s*%", title)
    pct = float(pm.group(1)) if pm else None
    d = 1 if any(w in title for w in UP) else -1 if any(w in title for w in DOWN) else 0
    if pct is not None and d < 0:
        pct = -pct
    return code, name, pct, d


def _db(db):
    c = sqlite3.connect(db, timeout=10)
    c.execute("CREATE TABLE IF NOT EXISTS news (link TEXT PRIMARY KEY, pub_ts TEXT, title TEXT, descr TEXT, code TEXT, "
              "name TEXT, pct REAL, dir INTEGER, collected_at TEXT)")
    return c


def save_items(items: list, master: dict, db: str = DB_PATH) -> tuple:
    """→ (새로 저장 수, 이미 있던 기사를 만났나)."""
    c = _db(db)
    new, seen = 0, False
    now = datetime.datetime.now().isoformat(timespec="seconds")
    for it in items:
        title = clean(it.get("title"))
        if "특징주" not in title or any(w in title for w in US):
            continue                                     # 미국 특징주는 뺌(국장 재료만)
        link = it.get("originallink") or it.get("link")
        if c.execute("SELECT 1 FROM news WHERE link=?", (link,)).fetchone():
            seen = True
            continue
        try:
            ts = email.utils.parsedate_to_datetime(it.get("pubDate", "")).strftime("%Y-%m-%d %H:%M")
        except (TypeError, ValueError):
            ts = ""
        code, name, pct, d = parse_title(title, master)
        c.execute("INSERT INTO news VALUES (?,?,?,?,?,?,?,?,?)",
                  (link, ts, title, clean(it.get("description")), code, name, pct, d, now))
        new += 1
    c.commit()
    c.close()
    return new, seen


def collect(db: str = DB_PATH) -> int:
    import requests
    cid, sec = os.getenv("NAVER_CLIENT_ID", ""), os.getenv("NAVER_CLIENT_SECRET", "")
    if not cid or not sec:
        print("⚠️ [특징주뉴스] 네이버 API 키 없음")
        return 0
    master, total = _master(), 0
    for start in range(1, 1000, 100):
        try:
            r = requests.get("https://openapi.naver.com/v1/search/news.json",
                             headers={"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": sec},
                             params={"query": "특징주", "display": 100, "start": start, "sort": "date"}, timeout=10).json()
        except Exception as e:
            print(f"⚠️ [특징주뉴스] 조회 오류: {e}")
            break
        items = r.get("items") or []
        new, seen = save_items(items, master, db)
        total += new
        if seen or len(items) < 100:
            break
    return total


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_base, ".env"))
    n = collect()
    c = sqlite3.connect(DB_PATH)
    print(f"✅ [특징주뉴스] 새로 {n}건 · 전체 {c.execute('SELECT count(*) FROM news').fetchone()[0]}건 · "
          f"종목 매칭 {c.execute('SELECT count(*) FROM news WHERE code IS NOT NULL').fetchone()[0]}건")
