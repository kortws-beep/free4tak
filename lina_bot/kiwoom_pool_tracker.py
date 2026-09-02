"""
kiwoom_pool_tracker.py — 키움 전체 조건검색식 결과 누적 추적 (2026-07-25)
================================================================
[목적]
키움 조건검색식(전체, 단타성 제외) 결과를 매일 검색식(소스)별로 구분해
누적 저장한다. 스캔 당일 나온 종목이 5영업일 이상 지나도 너무 오르지
않았으면(10% 이하 상승) 아직 늦지 않은 후보로 재검토할 수 있게 플래그만
남긴다 — sbo2 실거래 후보군에 자동 연결은 아직 하지 않음(2026-07-25 결정,
일단 DB/로그로만 관찰).

[스키마] kiwoom_pool_log
  scan_date/stock_name/code/source(검색식명)/base_price/atr_val/
  stop_price/tgt_price/checked/check_date/check_price/change_pct/promoted
  UNIQUE(scan_date, stock_name, source) — 당일 중복만 제거, 일자별로는 누적

[사용법]
  python3 kiwoom_pool_tracker.py scan     # 오늘자 전체 조건검색 스캔+저장
  python3 kiwoom_pool_tracker.py checkin  # 5영업일 이상 경과분 체크인
"""
import os
import sys
import asyncio
import sqlite3
import datetime
from dotenv import load_dotenv

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
STOCK_BOT  = os.path.dirname(BASE_DIR)
POOL_DB    = os.path.join(BASE_DIR, "kiwoom_pool_history.db")
FIN_DB     = os.path.join(BASE_DIR, "kr_theme_finance.db")

_env1 = os.path.join(BASE_DIR, ".env")
_env2 = os.path.join(STOCK_BOT, ".env")
if os.path.exists(_env1):
    load_dotenv(_env1)
elif os.path.exists(_env2):
    load_dotenv(_env2)

for _d in ["core", ""]:
    _p = os.path.join(STOCK_BOT, _d)
    if os.path.exists(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

# 단타성 검색식 제외 (sbot의 원래 스킵리스트) — 추세/VCP/눌림목은 여기서는
# 오히려 소스로 구분해 누적하고 싶은 대상이라 제외하지 않는다
SKIP_KEYWORDS = ["종가", "단타", "장개장", "직후", "시가이탈", "오전중저가", "090930", "당일고가"]

ATR_PERIOD           = 14
CHECKIN_MIN_DAYS     = 5    # 최소 5영업일 경과해야 체크인 대상
CHECKIN_MAX_GAIN_PCT = 10.0  # 이 이하 상승이면 "아직 안 늦은 후보"로 판정


def init_db():
    conn = sqlite3.connect(POOL_DB, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS kiwoom_pool_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_date   TEXT NOT NULL,
            stock_name  TEXT NOT NULL,
            code        TEXT DEFAULT '',
            source      TEXT NOT NULL,
            base_price  REAL DEFAULT 0,
            atr_val     REAL DEFAULT 0,
            stop_price  REAL DEFAULT 0,
            tgt_price   REAL DEFAULT 0,
            checked     INTEGER DEFAULT 0,
            check_date  TEXT DEFAULT '',
            check_price REAL DEFAULT 0,
            change_pct  REAL DEFAULT 0,
            promoted    INTEGER DEFAULT 0,
            created_at  TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(scan_date, stock_name, source)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kpl_date ON kiwoom_pool_log(scan_date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_kpl_checked ON kiwoom_pool_log(checked)")
    conn.commit()
    conn.close()


def _calc_atr(stock_name: str, conn: sqlite3.Connection) -> dict:
    """kr_stock_daily_data 종가 기반 근사 ATR + 손절/목표 (sbo2 최소게이트와 동일 방식)"""
    rows = conn.execute("""
        SELECT close_price FROM kr_stock_daily_data
        WHERE stock_name = ? ORDER BY date DESC LIMIT 30
    """, (stock_name,)).fetchall()
    closes = [r[0] for r in rows if r[0] and r[0] > 0]
    if len(closes) < ATR_PERIOD + 1:
        return {}
    curr = closes[0]
    atr = sum(abs(closes[i] - closes[i + 1]) for i in range(ATR_PERIOD)) / ATR_PERIOD
    if atr <= 0 or curr <= 0:
        return {}
    return {
        "base_price": curr,
        "atr_val":    atr,
        "stop_price": round(curr - atr * 1.5, 0),
        "tgt_price":  round(curr + atr * 3.0, 0),
    }


async def scan_and_log() -> bool:
    """오늘자 키움 전체 조건검색(단타성 제외) 스캔 → 검색식별로 구분해 누적 저장.
    반환값은 스케줄러의 실패 재시도 판단용 — 조건검색 결과를 하나라도
    정상적으로 받아왔으면 True, API 비활성화/전체 타임아웃 등으로
    아무것도 못 받아왔으면 False."""
    from kiwoom_api import KiwoomAPI

    api = KiwoomAPI()
    if not api.enabled:
        print("⚠️ 키움 API 비활성화 — 스캔 스킵")
        return False

    code_name_map = {}
    code_multi_tag_map = {}
    await api.get_condition_codes(
        use_keywords=None,
        skip_keywords=SKIP_KEYWORDS,
        code_name_map=code_name_map,
        code_multi_tag_map=code_multi_tag_map,
    )

    if not code_multi_tag_map:
        print("⚠️ 조건검색 결과 없음")
        return False

    init_db()
    today = datetime.date.today().strftime("%Y-%m-%d")
    conn = sqlite3.connect(POOL_DB, timeout=10)
    fin_conn = sqlite3.connect(FIN_DB, timeout=10)

    saved, skipped = 0, 0
    for code, sources in code_multi_tag_map.items():
        name = code_name_map.get(code, code)
        atr_data = _calc_atr(name, fin_conn)
        if not atr_data:
            skipped += 1
            continue
        for source in set(sources):
            try:
                conn.execute("""
                    INSERT OR IGNORE INTO kiwoom_pool_log
                        (scan_date, stock_name, code, source,
                         base_price, atr_val, stop_price, tgt_price)
                    VALUES (?,?,?,?,?,?,?,?)
                """, (today, name, code, source,
                      atr_data["base_price"], atr_data["atr_val"],
                      atr_data["stop_price"], atr_data["tgt_price"]))
                saved += 1
            except Exception as e:
                print(f"⚠️ 저장 오류 {name}/{source}: {e}")

    conn.commit()
    conn.close()
    fin_conn.close()
    print(f"✅ 키움풀 스캔 저장 완료: {saved}건 저장 ({skipped}건 가격데이터 부족으로 스킵)")
    return True


def checkin_pool_log():
    """5영업일 이상 경과한 미체크 항목 체크인 — 10% 이하 상승분 promoted 플래그.
    반환: (checked_cnt, promoted_list[(name, scan_date, change_pct), ...])
    ★ 2026-09-03: 07/25 신설 이후 스케줄러에 실제로 물려 있지 않아 한 달 넘게
    데이터만 쌓이고 체크인이 한 번도 안 돌고 있었음(사용자 지적). 반환값을
    lina_bot.py의 07:20 마스터 리포트에 포함하도록 연결."""
    init_db()
    conn = sqlite3.connect(POOL_DB, timeout=10)
    fin_conn = sqlite3.connect(FIN_DB, timeout=10)

    trading_days = [r[0] for r in fin_conn.execute(
        "SELECT DISTINCT date FROM kr_stock_daily_data ORDER BY date"
    ).fetchall()]
    day_index = {d: i for i, d in enumerate(trading_days)}
    today = trading_days[-1] if trading_days else datetime.date.today().strftime("%Y-%m-%d")

    pending = conn.execute("""
        SELECT id, scan_date, stock_name, base_price
        FROM kiwoom_pool_log WHERE checked = 0
    """).fetchall()

    promoted_list = []
    checked_cnt = 0
    for row_id, scan_date, stock_name, base_price in pending:
        idx = day_index.get(scan_date)
        if idx is None:
            continue
        days_elapsed = len(trading_days) - idx - 1
        if days_elapsed < CHECKIN_MIN_DAYS:
            continue  # 아직 5영업일 안 지남

        row = fin_conn.execute("""
            SELECT close_price FROM kr_stock_daily_data
            WHERE stock_name = ? ORDER BY date DESC LIMIT 1
        """, (stock_name,)).fetchone()
        if not row or not row[0]:
            continue
        curr_price = row[0]
        change_pct = round((curr_price - base_price) / base_price * 100, 2) if base_price else 0
        # ★ 2026-09-03: 하한선 없이 "10% 이하 상승"만 봐서 -78%처럼 완전히
        #   폭락한 종목까지 "아직 안 늦은 후보"로 잘못 분류되던 버그 발견
        #   (실측: 2124건 체크인 중 1793건이 후보로 잡혔는데 그중 1014건이
        #   실제론 하락 종목 — 진짜 의도(플랫~소폭상승, 아직 안 늦음)에
        #   맞는 건 779건뿐). 하락은 이미 실패한 픽이지 "안 늦은 후보"가
        #   아니므로 0% 이상으로 하한선 추가.
        promoted = int(0 <= change_pct <= CHECKIN_MAX_GAIN_PCT)

        conn.execute("""
            UPDATE kiwoom_pool_log
            SET checked=1, check_date=?, check_price=?, change_pct=?, promoted=?
            WHERE id=?
        """, (today, curr_price, change_pct, promoted, row_id))
        checked_cnt += 1
        if promoted:
            promoted_list.append((stock_name, scan_date, change_pct))

    conn.commit()
    conn.close()
    fin_conn.close()

    promoted_list.sort(key=lambda x: x[2])
    print(f"✅ 체크인 완료: {checked_cnt}건 평가 ({len(promoted_list)}건 재검토 후보)")
    for name, scan_date, chg in promoted_list:
        print(f"   🔍 {name} ({scan_date} 스캔, {chg:+.1f}%) — 재검토 후보")
    return checked_cnt, promoted_list


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "scan"
    if cmd == "scan":
        asyncio.run(scan_and_log())
    elif cmd == "checkin":
        checkin_pool_log()
    else:
        print("사용법: python3 kiwoom_pool_tracker.py [scan|checkin]")
