"""
hk_consensus.py — 한경 컨센서스 기업 리포트 모으기 (2026-11-05, 대장)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
대장: DART 공시 대신 "한경 컨센서스로 해결 안 되니?" → 증권사 기업 리포트(날짜·종목·목표가·투자의견·증권사)를 매일 쌓는다.
실적 리뷰·목표가 상향·신규 커버리지 같은 '재료'를 횡보장 개별 특징주 엔진 검증에 쓰려는 것(매매 안 함).
수주 공시(단일판매·공급계약)는 리포트에 안 나옴 → 따로(KIND) 볼 것.
  consensus.hankyung.com/analysis/list?skinType=business&sdate&edate&now_page&pagenum — 로그인·키 없이 읽힘.
  저장: intelligence/hk_consensus.db 테이블 reports(report_idx 기준 중복 없음).
리나가 [특징주] 뉴스와 같이 30분마다(오늘·어제 범위) 돌림. 과거 채우기: venv/bin/python intelligence/hk_consensus.py 2026-07-01 2026-11-05
"""
import os
import re
import sys
import html
import time
import sqlite3
import datetime

_here = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(_here, "hk_consensus.db")
URL = "https://consensus.hankyung.com/analysis/list"
PAGE = 80


def parse(page_html: str) -> list:
    """→ [(idx, 날짜, 코드, 종목명, 제목, 목표가, 의견, 애널리스트, 증권사)]."""
    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page_html, re.S):
        m = re.search(r"report_idx=(\d+)", row)
        tds = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        if not m or len(tds) < 6:
            continue
        txt = lambda x: html.unescape(re.sub(r"<[^>]+>", " ", x)).split()  # noqa: E731
        date = " ".join(txt(tds[0]))
        a = re.search(r'target="_blank">(.*?)</a>', tds[1], re.S)
        title = html.unescape(re.sub(r"\s+", " ", a.group(1))).strip() if a else ""
        cm = re.search(r"^(.*?)\(([0-9A-Z]{6})\)", title)
        name, code = (cm.group(1).strip(), cm.group(2)) if cm else ("", "")
        tp = re.sub(r"[^0-9]", "", " ".join(txt(tds[2])))
        out.append((int(m.group(1)), date, code, name, title, int(tp) if tp else None,
                    " ".join(txt(tds[3])), " ".join(txt(tds[4])), " ".join(txt(tds[5]))))
    return out


def save(rows: list, db: str = DB_PATH) -> int:
    c = sqlite3.connect(db, timeout=10)
    c.execute("CREATE TABLE IF NOT EXISTS reports (report_idx INTEGER PRIMARY KEY, date TEXT, code TEXT, name TEXT, title TEXT, "
              "target INTEGER, opinion TEXT, analyst TEXT, broker TEXT)")
    before = c.execute("SELECT count(*) FROM reports").fetchone()[0]
    c.executemany("INSERT OR IGNORE INTO reports VALUES (?,?,?,?,?,?,?,?,?)", rows)
    c.commit()
    n = c.execute("SELECT count(*) FROM reports").fetchone()[0] - before
    c.close()
    return n


def collect(sdate: str = None, edate: str = None, db: str = DB_PATH, max_pages: int = 60) -> int:
    import requests
    today = datetime.date.today()
    sdate = sdate or (today - datetime.timedelta(days=1)).isoformat()
    edate = edate or today.isoformat()
    total = 0
    for p in range(1, max_pages + 1):
        try:
            r = requests.get(URL, params={"skinType": "business", "sdate": sdate, "edate": edate, "now_page": p,
                                          "pagenum": PAGE}, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        except Exception as e:
            print(f"⚠️ [한경컨센서스] 조회 오류: {e}")
            break
        rows = parse(r.text)
        total += save(rows, db)
        if len(rows) < PAGE:
            break
        time.sleep(0.5)
    return total


if __name__ == "__main__":
    s, e = (sys.argv[1], sys.argv[2]) if len(sys.argv) > 2 else (None, None)
    if s:   # 한 달씩 나눠서(페이지 끝 제한 피함)
        d, end, n = datetime.date.fromisoformat(s), datetime.date.fromisoformat(e), 0
        while d <= end:
            nxt = min(end, (d.replace(day=28) + datetime.timedelta(days=4)).replace(day=1) - datetime.timedelta(days=1))
            n += collect(d.isoformat(), nxt.isoformat())
            d = nxt + datetime.timedelta(days=1)
    else:
        n = collect()
    c = sqlite3.connect(DB_PATH)
    print(f"✅ [한경컨센서스] 새로 {n}건 · 전체 {c.execute('SELECT count(*) FROM reports').fetchone()[0]}건 "
          f"({c.execute('SELECT min(date), max(date) FROM reports').fetchone()})")
