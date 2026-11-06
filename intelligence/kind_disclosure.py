"""
kind_disclosure.py — 거래소(KIND) 공시 목록 모으기 (2026-11-06, 대장)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
DART는 API 키가 필요해서, 키 없이 읽히는 거래소 KIND '오늘의 공시'(날짜 지정 가능)를 쌓는다 — 개별 특징주 엔진 검증용 재료(매매 안 함).
특히 한경 컨센서스(증권사 리포트)엔 안 나오는 수주 공시(단일판매ㆍ공급계약체결)·잠정실적·자사주·증자.
  kind.krx.co.kr/disclosure/todaydisclosure.do (POST, 100건씩 페이지)
  종목: 회사명을 종목 마스터에서 찾음(코넥스·비상장·거래소 안내 등은 code 없음).
  분류(kind): 수주 · 실적 · 자사주 · 증자 · 감자·분할 · 거래정지·관리 · 기타
  저장: intelligence/kind_disclosure.db 테이블 disc(acptno 기준 중복 없음).
리나가 [특징주] 뉴스와 같이 30분마다(오늘). 과거 채우기: venv/bin/python intelligence/kind_disclosure.py 2026-07-01 2026-11-06
"""
import os
import re
import sys
import html
import time
import sqlite3
import datetime

_here = os.path.dirname(os.path.abspath(__file__))
_base = os.path.dirname(_here)
for _d in ["core", "intelligence", "interface", "bots", ""]:
    _p = os.path.join(_base, _d)
    if _p not in sys.path:
        sys.path.insert(0, _p)

DB_PATH = os.path.join(_here, "kind_disclosure.db")
URL = "https://kind.krx.co.kr/disclosure/todaydisclosure.do"
KINDS = (("수주", ("단일판매", "공급계약")), ("실적", ("잠정)실적", "잠정실적", "손익구조", "매출액또는")),
         ("자사주", ("자기주식취득", "자기주식 취득", "자기주식소각", "주식소각")), ("증자", ("유상증자", "무상증자")),
         ("감자·분할", ("감자", "분할", "합병")), ("거래정지·관리", ("거래정지", "관리종목", "상장폐지", "투자주의", "투자경고", "투자위험")))


def classify(title: str) -> str:
    t = title.replace(" ", "")
    for k, words in KINDS:
        if any(w.replace(" ", "") in t for w in words):
            return k
    return "기타"


def parse(page_html: str, date: str) -> list:
    """→ [(acptno, 날짜, 시각, 회사, 제목, 제출인)]."""
    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page_html, re.S):
        a = re.search(r"openDisclsViewer\('(\d+)'", row)
        tds = [" ".join(html.unescape(re.sub(r"<[^>]+>", " ", c)).split()) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if not a or len(tds) < 4:
            continue
        out.append((a.group(1), date, tds[0], tds[1], tds[2], tds[3]))
    return out


def _master() -> dict:
    import dip_leader
    c = sqlite3.connect(f"file:{dip_leader.DAILY_DB}?mode=ro", uri=True)
    try:
        return {n.replace(" ", ""): k for k, n in c.execute("SELECT code, name FROM kr_stock_master") if n}
    finally:
        c.close()


def save(rows: list, master: dict, db: str = DB_PATH) -> tuple:
    """→ (새로 저장 수, 이미 있던 공시를 만났나)."""
    c = sqlite3.connect(db, timeout=10)
    c.execute("CREATE TABLE IF NOT EXISTS disc (acptno TEXT PRIMARY KEY, date TEXT, time TEXT, company TEXT, code TEXT, "
              "title TEXT, submitter TEXT, kind TEXT)")
    new, seen = 0, False
    for acpt, date, tm, comp, title, sub in rows:
        if c.execute("SELECT 1 FROM disc WHERE acptno=?", (acpt,)).fetchone():
            seen = True
            continue
        c.execute("INSERT INTO disc VALUES (?,?,?,?,?,?,?,?)",
                  (acpt, date, tm, comp, master.get((comp or "").replace(" ", "")), title, sub, classify(title)))
        new += 1
    c.commit()
    c.close()
    return new, seen


def collect(date: str = None, db: str = DB_PATH, stop_on_seen: bool = True, max_pages: int = 40) -> int:
    import requests
    date = date or datetime.date.today().isoformat()
    master, total = _master(), 0
    for p in range(1, max_pages + 1):
        try:
            r = requests.post(URL, data={"method": "searchTodayDisclosureSub", "currentPageSize": 100, "pageIndex": p,
                                         "orderMode": 0, "orderStat": "D", "forward": "todaydisclosure_sub", "chose": "S",
                                         "todayFlag": "N", "selDate": date},
                              headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        except Exception as e:
            print(f"⚠️ [KIND] 조회 오류: {e}")
            break
        rows = parse(r.text, date)
        new, seen = save(rows, master, db)
        total += new
        if len(rows) < 100 or (seen and stop_on_seen):
            break
        time.sleep(0.5)
    return total


if __name__ == "__main__":
    if len(sys.argv) > 2:
        d, end, n = datetime.date.fromisoformat(sys.argv[1]), datetime.date.fromisoformat(sys.argv[2]), 0
        while d <= end:
            if d.weekday() < 5:
                n += collect(d.isoformat(), stop_on_seen=False)
            d += datetime.timedelta(days=1)
    else:
        n = collect()
    c = sqlite3.connect(DB_PATH)
    total = c.execute("SELECT count(*) FROM disc").fetchone()[0]
    orders = c.execute("SELECT count(*) FROM disc WHERE kind='수주'").fetchone()[0]
    print(f"✅ [KIND] 새로 {n}건 · 전체 {total}건 · 수주 {orders}건")
