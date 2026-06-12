"""
sshow_db.py — 생쇼 공략주 DB 저장 및 조회
─────────────────────────────────────────────────────────────
매일 14:30 수집된 생쇼 공략주를 DB에 누적 저장
5영업일 (약 20종목) 풀로 tele_swing_analyzer에 제공

[테이블 구조]
  sshow_picks : 생쇼 공략주 이력
    - date       : 수집일 (YYYY-MM-DD)
    - stock_name : 종목명
    - buy_price  : 매수가 (파싱 성공시)
    - stop_price : 손절가 (파싱 성공시)
    - tgt_price  : 목표가 (파싱 성공시)
    - raw_text   : 원문
    - created_at : 저장시각
"""

import os
import re
import sqlite3
import datetime

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
DB_PATH     = os.path.join(BASE_DIR, "sshow_picks.db")
KEEP_DAYS   = 7    # 7일치 보관 (5영업일 + 주말 여유)


# ══════════════════════════════════════════════════════════════
# DB 초기화
# ══════════════════════════════════════════════════════════════

def init_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sshow_picks (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            date       TEXT    NOT NULL,
            stock_name TEXT    NOT NULL,
            buy_price  REAL    DEFAULT 0,
            stop_price REAL    DEFAULT 0,
            tgt_price  REAL    DEFAULT 0,
            raw_text   TEXT    DEFAULT '',
            created_at TEXT    DEFAULT (datetime('now','localtime')),
            UNIQUE(date, stock_name)
        )
    """)
    conn.commit()
    conn.close()


# ══════════════════════════════════════════════════════════════
# 파싱 헬퍼
# ══════════════════════════════════════════════════════════════

def _parse_price(text: str) -> float:
    """텍스트에서 숫자(원) 추출"""
    m = re.search(r'[\d,]+', text.replace(' ', ''))
    if m:
        try:
            return float(m.group().replace(',', ''))
        except Exception:
            pass
    return 0.0

def _parse_stock_name(text: str) -> str:
    """생쇼 텍스트에서 종목명 추출"""
    # "📌 종목명" 패턴
    m = re.search(r'📌\s*([가-힣A-Za-z0-9·\-&]+)', text)
    if m:
        return m.group(1).strip()
    # 첫 번째 한글 단어
    m = re.search(r'([가-힣]{2,10}(?:[A-Za-z0-9]*)?)', text)
    if m:
        return m.group(1).strip()
    return ""

def _parse_sshow_block(block: str) -> dict:
    """
    생쇼 텍스트 블록에서 종목명/매수가/손절가 파싱
    예시:
      📌 한미반도체
      매수가 85,000원 / 손절 78,000원 / 목표 98,000원
      [사유]: AI반도체 수혜 지속
    """
    result = {
        "stock_name": "",
        "buy_price":  0.0,
        "stop_price": 0.0,
        "tgt_price":  0.0,
        "raw_text":   block,
    }

    lines = [l.strip() for l in block.split('\n') if l.strip()]

    for line in lines:
        # 종목명
        if not result["stock_name"]:
            name = _parse_stock_name(line)
            if name:
                result["stock_name"] = name

        # 매수가
        if any(kw in line for kw in ["매수", "진입", "매수가"]):
            price = _parse_price(line)
            if price > 0:
                result["buy_price"] = price

        # 손절가
        if any(kw in line for kw in ["손절", "스탑", "손절가"]):
            price = _parse_price(line)
            if price > 0:
                result["stop_price"] = price

        # 목표가
        if any(kw in line for kw in ["목표", "타겟", "목표가"]):
            price = _parse_price(line)
            if price > 0:
                result["tgt_price"] = price

    return result


# ══════════════════════════════════════════════════════════════
# 저장/조회 함수
# ══════════════════════════════════════════════════════════════

def save_sshow_picks(raw_text: str) -> int:
    """
    생쇼 raw_text 파싱 후 DB 저장
    포맷: "종목명\n\n* 핵심 공략 사유: ..."
    반환: 저장된 건수
    """
    init_db()

    today = datetime.date.today().strftime("%Y-%m-%d")
    saved = 0

    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")

    # 줄 단위 파싱 — "핵심 공략 사유:" 위 줄이 종목명
    # 제외 키워드 (종목명이 아닌 것)
    SKIP_WORDS = {"핵심", "공략", "사유", "매수", "손절", "목표", "분석", "추천"}

    matches = []
    lines = [l.strip() for l in raw_text.split("\n") if l.strip()]
    for i, line in enumerate(lines):
        if "핵심 공략 사유" in line:
            for j in range(i-1, max(i-4, -1), -1):
                candidate = re.sub(r"[\s\*\-\.\d]", "", lines[j])
                # 종목명 조건: 2자 이상 한글 포함, 스킵 단어 아님
                if (len(candidate) >= 2
                        and (re.search(r"[가-힣]", candidate) or re.match(r"^[A-Za-z0-9]+$", candidate))
                        and candidate not in SKIP_WORDS
                        and "사유" not in candidate
                        and "공략" not in candidate):
                    reason = re.sub(r"\*\s*핵심 공략 사유\s*:\s*", "", line).strip()
                    matches.append((candidate, reason))
                    break

    for name, reason in matches:
        name = name.strip()
        if not name or len(name) < 2:
            continue

        try:
            conn.execute("""
                INSERT OR IGNORE INTO sshow_picks
                    (date, stock_name, buy_price, stop_price, tgt_price, raw_text)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (today, name, 0, 0, 0, reason[:300]))
            if conn.execute("SELECT changes()").fetchone()[0] > 0:
                saved += 1
                print(f"   💾 생쇼 저장: {name}")
        except Exception as e:
            print(f"⚠️ 생쇼 저장 오류 {name}: {e}")

    conn.commit()

    # 오래된 데이터 정리
    cutoff = (datetime.date.today() -
              datetime.timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    conn.execute("DELETE FROM sshow_picks WHERE date < ?", (cutoff,))
    conn.commit()
    conn.close()

    if saved > 0:
        print(f"✅ 생쇼 DB 저장: {saved}건 (날짜: {today})")

    return saved


def get_sshow_stocks(days: int = 5) -> dict:
    """
    최근 N영업일 생쇼 종목 반환
    반환: {종목명: {"buy": 매수가, "stop": 손절가, "tgt": 목표가, "days_ago": N}}
    """
    if not os.path.exists(DB_PATH):
        return {}

    try:
        cutoff = (datetime.date.today() -
                  datetime.timedelta(days=days + 2)).strftime("%Y-%m-%d")

        conn   = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT stock_name, buy_price, stop_price, tgt_price, date
            FROM sshow_picks
            WHERE date >= ?
            ORDER BY date DESC
        """, (cutoff,))
        rows = cursor.fetchall()
        conn.close()

        result = {}
        today  = datetime.date.today()

        for name, buy, stop, tgt, date_str in rows:
            if name not in result:
                try:
                    d = datetime.datetime.strptime(date_str, "%Y-%m-%d").date()
                    days_ago = (today - d).days
                except Exception:
                    days_ago = 99
                result[name] = {
                    "buy":      buy,
                    "stop":     stop,
                    "tgt":      tgt,
                    "days_ago": days_ago,
                }

        return result

    except Exception as e:
        print(f"⚠️ 생쇼 조회 오류: {e}")
        return {}


def get_sshow_summary() -> str:
    """생쇼 DB 현황 요약"""
    if not os.path.exists(DB_PATH):
        return "생쇼 DB 없음"

    try:
        conn   = sqlite3.connect(DB_PATH, timeout=5)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT date, COUNT(*) FROM sshow_picks
            GROUP BY date ORDER BY date DESC LIMIT 7
        """)
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return "생쇼 DB 비어있음"

        lines = ["📋 [생쇼 DB 현황]"]
        for date, cnt in rows:
            lines.append(f"   {date}: {cnt}종목")
        return "\n".join(lines)

    except Exception as e:
        return f"⚠️ 생쇼 DB 조회 오류: {e}"


if __name__ == "__main__":
    init_db()
    print(get_sshow_summary())
    stocks = get_sshow_stocks(days=5)
    print(f"\n최근 5일 종목: {len(stocks)}개")
    for name, info in list(stocks.items())[:5]:
        print(f"  {name}: 매수{info['buy']:,.0f} 손절{info['stop']:,.0f} "
              f"목표{info['tgt']:,.0f} ({info['days_ago']}일전)")
