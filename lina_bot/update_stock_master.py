"""
update_stock_master.py — 전 상장종목 목록 갱신 (2026-10-06)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

지금까지 종목 목록은 주달 테마 크롤링(kr_theme_stocks)뿐이라, 테마에 안
묶인 종목·최근 상장 종목이 빠져 있었다(대장: "빠진 게 많네").
한국투자증권이 공식 배포하는 종목 마스터 파일(코스피/코스닥)을 받아
kr_stock_master 테이블에 넣는다. 테마 테이블은 건드리지 않는다
(테마 분석 통계가 섞이지 않게).

실행:  python update_stock_master.py      (주 1회 정도면 충분)
이후:  python collect_daily_data.py new    (새로 들어온 종목만 일봉 수집)
"""
import io
import os
import sqlite3
import zipfile
import datetime

import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE_DIR, "kr_theme_finance.db")
MASTER_URLS = {
    "KOSPI":  ("https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip", 228),
    "KOSDAQ": ("https://new.real.download.dws.co.kr/common/master/kosdaq_code.mst.zip", 222),
}
STOCK_GROUP = "ST"          # 그룹코드 ST = 주권 (ETF=EF, ETN=EN, 리츠=RT …은 제외)
EXCLUDE_WORDS = ("스팩",)


def ensure_table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS kr_stock_master (
        code TEXT PRIMARY KEY, name TEXT NOT NULL, market TEXT NOT NULL,
        updated_at TEXT)""")


def parse_master(text: str, tail_len: int) -> list:
    """KIS 마스터 파일(고정폭) → [(code, name, group)].
    한 줄 = [단축코드 9][표준코드 12][한글명 …] + 뒤쪽 고정폭 tail_len(그룹코드 2자리로 시작)."""
    out = []
    # KIS 예제와 같게 "줄바꿈 1글자 포함" 기준으로 뒤쪽 고정폭을 자른다(\r\n이면 어긋남)
    for row in text.replace("\r\n", "\n").splitlines(keepends=True):
        if len(row) <= tail_len + 21:
            continue
        head, tail = row[:len(row) - tail_len], row[-tail_len:]
        code = head[0:9].strip()
        name = head[21:].strip()
        if code and name:
            out.append((code, name, tail[:2]))
    return out


def fetch_market(market: str) -> list:
    url, tail_len = MASTER_URLS[market]
    res = requests.get(url, timeout=30)
    res.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(res.content)) as z:
        text = z.read(z.namelist()[0]).decode("cp949", errors="replace")
    return parse_master(text, tail_len)


def update(db_path: str = DB_PATH, fetch=fetch_market) -> dict:
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    for market in MASTER_URLS:
        for code, name, group in fetch(market):
            if group != STOCK_GROUP or len(code) != 6 or any(w in name for w in EXCLUDE_WORDS):
                continue
            rows.append((code, name, market, now))
    if not rows:
        raise RuntimeError("마스터 파일에서 종목을 하나도 못 읽음 — 기존 목록 유지")
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        ensure_table(conn)
        before = {c for (c,) in conn.execute("SELECT code FROM kr_stock_master")}
        conn.executemany("INSERT OR REPLACE INTO kr_stock_master VALUES (?,?,?,?)", rows)
        # 이번 목록에 없는 종목 = 상장폐지 등 → 목록에서 뺀다(일봉 기록은 그대로 둠)
        codes = {r[0] for r in rows}
        gone = before - codes
        conn.executemany("DELETE FROM kr_stock_master WHERE code=?", [(c,) for c in gone])
        theme_codes = set()
        try:
            import re
            for (raw,) in conn.execute("SELECT DISTINCT stock_name FROM kr_theme_stocks"):
                m = re.search(r"([0-9A-Z]{6})$", (raw or "").strip())
                if m:
                    theme_codes.add(m.group(1))
        except sqlite3.OperationalError:
            pass
        conn.commit()
    finally:
        conn.close()
    return {"total": len(rows), "added": len(codes - before), "removed": len(gone),
            "not_in_theme": len(codes - theme_codes)}


def master_names(conn) -> list:
    """collect_daily_data 등에서 쓰는 '종목명KOSPI 005930' 형식으로 반환(테이블 없으면 [])."""
    try:
        return [f"{n}{m} {c}" for c, n, m in conn.execute("SELECT code, name, market FROM kr_stock_master")]
    except sqlite3.OperationalError:
        return []


if __name__ == "__main__":
    r = update()
    print(f"✅ 상장종목 {r['total']}개 (신규 {r['added']}, 제외 {r['removed']}) — "
          f"그중 테마 목록에 없던 종목 {r['not_in_theme']}개")
    print("   다음: python collect_daily_data.py new   (새 종목 일봉 수집)")
