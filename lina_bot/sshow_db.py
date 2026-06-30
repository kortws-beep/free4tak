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
    - result        : 결과 판정 (2026-06-30 추가)
                       'pending'=미판정, 'hit'=목표가도달,
                       'stop'=손절가터치, 'hold'=기간만료(보합)
    - result_date   : 결과 판정일
    - result_price  : 판정 시점 종가
    - price_valid   : 가격 정합성 (1=정상, 0=비정상 — stop>=buy 등)

[적중률 통계]
  get_sshow_stats() — 최근 N일 result='hit'/'stop'/'hold' 집계
  check_and_update_results() — 5영업일 지난 pending 건을 자동 판정
  (매일 1회, kiki_briefing 등에서 호출 권장)
"""

import os
import re
import sqlite3
import datetime

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
DB_PATH     = os.path.join(BASE_DIR, "sshow_picks.db")
KEEP_DAYS   = 90   # ★ 2026-06-30: 7일 → 90일로 연장 (결과추적/통계 누적을 위해
                    #   필요. 7일이면 5영업일 판정 끝나기도 전에 삭제될 수 있었음)
RESULT_CHECK_DAYS = 5   # 추천 후 5영업일 뒤 결과 판정


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
    # ★ 2026-06-30 추가 컬럼 — 기존 운영 DB에도 안전하게 적용되도록
    #   ALTER TABLE로 동적 추가 (이미 있으면 무시)
    for col, coltype in [
        ("result",       "TEXT DEFAULT 'pending'"),
        ("result_date",  "TEXT DEFAULT ''"),
        ("result_price", "REAL DEFAULT 0"),
        ("price_valid",  "INTEGER DEFAULT 1"),
    ]:
        try:
            conn.execute(f"ALTER TABLE sshow_picks ADD COLUMN {col} {coltype}")
        except sqlite3.OperationalError:
            pass  # 이미 존재
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
    새 포맷:
      📌 현대건설기계(267270) [매수: 156,300원 / 목표: 172,000원 / 손절: 130,000원]
      [사유]: ...
    """
    result = {
        "stock_name": "",
        "buy_price":  0.0,
        "stop_price": 0.0,
        "tgt_price":  0.0,
        "raw_text":   block,
    }

    lines = [l.strip() for l in block.split('\n') if l.strip()]

    # 📌 줄과 다음 줄을 합쳐서 파싱 (종목명/코드/가격이 줄바꿈으로 분리될 수 있음)
    for i, line in enumerate(lines):
        if "📌" in line:
            # 다음 줄과 합치기
            combined = line
            if i + 1 < len(lines) and "📌" not in lines[i+1] and "[사유]" not in lines[i+1]:
                combined = line + " " + lines[i+1]

            # 종목명: 📌 다음, 괄호 또는 [ 앞까지
            m = re.search(r'📌\s*([가-힣A-Za-z0-9·\-&\s]+?)(?:\s*[\(\[])', combined)
            if m:
                result["stock_name"] = m.group(1).strip()
            else:
                m = re.search(r'📌\s*([가-힣A-Za-z0-9·\-&]+)', combined)
                if m:
                    result["stock_name"] = m.group(1).strip()

            # 매수가
            m = re.search(r'매수\s*:\s*([\d,]+)원', combined)
            if m:
                result["buy_price"] = float(m.group(1).replace(',', ''))

            # 목표가
            m = re.search(r'목표\s*:\s*([\d,]+)원', combined)
            if m:
                result["tgt_price"] = float(m.group(1).replace(',', ''))

            # 손절가
            m = re.search(r'손절\s*:\s*([\d,]+)원', combined)
            if m:
                result["stop_price"] = float(m.group(1).replace(',', ''))

    return result


# ══════════════════════════════════════════════════════════════
# 저장/조회 함수
# ══════════════════════════════════════════════════════════════

def save_sshow_picks(raw_text: str) -> int:
    """
    생쇼 raw_text 파싱 후 DB 저장
    새 포맷: "📌 종목명(코드) [매수: X원 / 목표: X원 / 손절: X원]\n  [사유]: ..."
    반환: 저장된 건수
    """
    init_db()

    today = datetime.date.today().strftime("%Y-%m-%d")
    saved = 0

    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")

    # 블록 분리 (📌 기준)
    blocks = [b.strip() for b in raw_text.split("\n\n") if b.strip()]

    for block in blocks:
        if "📌" not in block:
            continue

        parsed = _parse_sshow_block(block)
        name = parsed["stock_name"]
        if not name or len(name) < 2:
            continue

        # ★ 2026-06-30 추가: 가격 정합성 검증 (손절가 < 매수가 < 목표가가
        #   정상). KT&G/엘앤에프처럼 손절가가 매수가보다 크게 파싱되는
        #   사례가 실제로 발견됨 — 원문 자체의 오타일 수도, 파싱 실패일
        #   수도 있어 단정해서 버리지는 않되, price_valid=0으로 표시해
        #   통계 집계에서 자동 제외되도록 함.
        buy_p, stop_p, tgt_p = parsed["buy_price"], parsed["stop_price"], parsed["tgt_price"]
        price_valid = 1
        if buy_p > 0 and stop_p > 0 and tgt_p > 0:
            if not (stop_p < buy_p < tgt_p):
                price_valid = 0
                print(f"   ⚠️ 가격 역전 감지: {name} 매수:{buy_p:,.0f} "
                      f"손절:{stop_p:,.0f} 목표:{tgt_p:,.0f} → 통계 제외 표시")

        # [사유] 추출
        reason = ""
        for line in block.split("\n"):
            if "[사유]" in line:
                reason = re.sub(r"\[사유\]\s*:?\s*", "", line).strip()
                break

        try:
            conn.execute("""
                INSERT OR IGNORE INTO sshow_picks
                    (date, stock_name, buy_price, stop_price, tgt_price,
                     raw_text, price_valid)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (today, name, parsed["buy_price"], parsed["stop_price"],
                  parsed["tgt_price"], reason[:300], price_valid))
            if conn.execute("SELECT changes()").fetchone()[0] > 0:
                saved += 1
                print(f"   💾 생쇼 저장: {name} 매수:{parsed['buy_price']:,.0f} "
                      f"손절:{parsed['stop_price']:,.0f} 목표:{parsed['tgt_price']:,.0f}")
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


# ══════════════════════════════════════════════════════════════
# 결과 자동 판정 (2026-06-30 추가)
# ══════════════════════════════════════════════════════════════

def _get_finance_db_path() -> str:
    """kr_theme_finance.db 경로 (BASE_DIR 기준, 같은 lina_bot 폴더)"""
    return os.path.join(BASE_DIR, "kr_theme_finance.db")


def _get_price_history(stock_name: str, since_date: str) -> list:
    """
    종목명으로 since_date 이후의 (date, close_price) 목록을 날짜순 반환.
    ★ exact match만 사용 — LIKE 매칭은 '삼성전자'가 '삼성전자우'까지
    잘못 잡는 문제가 있어 반드시 정확히 일치하는 stock_name만 조회.
    """
    finance_db = _get_finance_db_path()
    if not os.path.exists(finance_db):
        return []
    try:
        conn = sqlite3.connect(finance_db, timeout=5)
        rows = conn.execute("""
            SELECT date, close_price FROM kr_stock_daily_data
            WHERE stock_name = ? AND date >= ?
            ORDER BY date ASC
        """, (stock_name, since_date)).fetchall()
        conn.close()
        return rows
    except Exception as e:
        print(f"⚠️ 가격 조회 오류 {stock_name}: {e}")
        return []


def check_and_update_results(force_today: str = None) -> dict:
    """
    추천 후 RESULT_CHECK_DAYS(5)영업일이 지난 'pending' 건을 자동 판정.
    판정 기준 (since_date~지금까지의 종가 흐름을 순서대로 확인):
      - 목표가에 먼저 도달한 종가가 있으면 → 'hit'
      - 손절가에 먼저 도달한 종가가 있으면 → 'stop'
      - 둘 다 아닌 채 5영업일(거래일 기준)이 지나면 → 'hold'
      - 가격 데이터가 아직 부족하면 → 그대로 'pending' 유지
    price_valid=0(가격 역전 등 비정상)인 건은 애초에 판정하지 않고 건너뜀.

    매일 1회(예: 장 마감 후) 호출 권장. 반환: {"hit": N, "stop": N, "hold": N}
    """
    init_db()
    today = force_today or datetime.date.today().strftime("%Y-%m-%d")

    conn = sqlite3.connect(DB_PATH, timeout=10)
    pending = conn.execute("""
        SELECT id, date, stock_name, buy_price, stop_price, tgt_price
        FROM sshow_picks
        WHERE result = 'pending' AND price_valid = 1
    """).fetchall()

    counts = {"hit": 0, "stop": 0, "hold": 0}

    for pick_id, pick_date, name, buy_p, stop_p, tgt_p in pending:
        if not (buy_p > 0 and stop_p > 0 and tgt_p > 0):
            continue  # 가격 파싱 자체가 실패한 건 — 판정 불가, pending 유지

        history = _get_price_history(name, pick_date)
        if not history:
            continue  # 가격 데이터 없음 — 다음 기회에 재시도

        # 추천일 자체(같은 날 종가)는 매수 시점 기준이 아니므로 다음날부터 판정
        history = [(d, p) for d, p in history if d > pick_date]
        if not history:
            continue

        result = None
        result_date = None
        result_price = None
        for d, p in history:
            if p >= tgt_p:
                result, result_date, result_price = "hit", d, p
                break
            if p <= stop_p:
                result, result_date, result_price = "stop", d, p
                break

        if result is None:
            # 목표/손절 둘 다 안 닿음 — 거래일 수가 RESULT_CHECK_DAYS 이상이면 보합 확정
            if len(history) >= RESULT_CHECK_DAYS:
                result = "hold"
                result_date, result_price = history[-1][0], history[-1][1]
            else:
                continue  # 아직 판정 기간 안 됨 — pending 유지

        conn.execute("""
            UPDATE sshow_picks
            SET result = ?, result_date = ?, result_price = ?
            WHERE id = ?
        """, (result, result_date, result_price, pick_id))
        counts[result] += 1
        print(f"   📊 생쇼 결과판정: {name} ({pick_date}) → {result} "
              f"({result_price:,.0f}원, {result_date})")

    conn.commit()
    conn.close()

    total = sum(counts.values())
    if total > 0:
        print(f"✅ 생쇼 결과판정 완료: 적중{counts['hit']} / "
              f"손절{counts['stop']} / 보합{counts['hold']}")

    return counts


def get_sshow_stats(days: int = 30) -> dict:
    """
    최근 N일간 판정 완료(hit/stop/hold)된 추천의 적중률 통계.
    반환: {"total": N, "hit": N, "stop": N, "hold": N, "hit_rate": 0~1,
           "sample_size_ok": bool}
    가격 정합성 비정상(price_valid=0) 건은 집계에서 제외.
    """
    if not os.path.exists(DB_PATH):
        return {"total": 0, "hit": 0, "stop": 0, "hold": 0,
                "hit_rate": 0.0, "sample_size_ok": False}

    cutoff = (datetime.date.today() -
              datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        conn = sqlite3.connect(DB_PATH, timeout=5)
        rows = conn.execute("""
            SELECT result, COUNT(*) FROM sshow_picks
            WHERE date >= ? AND price_valid = 1 AND result != 'pending'
            GROUP BY result
        """, (cutoff,)).fetchall()
        conn.close()
    except Exception as e:
        print(f"⚠️ 생쇼 통계 조회 오류: {e}")
        return {"total": 0, "hit": 0, "stop": 0, "hold": 0,
                "hit_rate": 0.0, "sample_size_ok": False}

    counts = {"hit": 0, "stop": 0, "hold": 0}
    for result, cnt in rows:
        if result in counts:
            counts[result] = cnt

    total = sum(counts.values())
    # 적중률: hit / (hit + stop)  ('hold'는 무승부로 분모에서 제외 —
    #  목표/손절 어느 쪽도 안 닿은 종목까지 패배로 잡으면 적중률이
    #  부당하게 낮아짐)
    decided = counts["hit"] + counts["stop"]
    hit_rate = (counts["hit"] / decided) if decided > 0 else 0.0

    return {
        "total": total,
        "hit": counts["hit"],
        "stop": counts["stop"],
        "hold": counts["hold"],
        "hit_rate": hit_rate,
        "sample_size_ok": total >= 20,  # 최소 20건은 돼야 신뢰 가능
    }


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
