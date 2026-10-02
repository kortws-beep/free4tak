"""
macro_calendar.py — 매크로 캘린더 이벤트 DB (FOMC/한국 금통위/실적시즌)
================================================================
[배경 — project_macro_calendar_sector_analysis 메모리 참고]
FOMC/한국은행 금융통화위원회(금통위)/분기실적 발표는 날짜가 사전에
공지되는 고정 반복 이벤트. 이 이벤트들 전후로 특정 섹터가 반복적으로
움직이는 패턴이 있는지 분석하기 위한 장기 리서치 프로젝트의 데이터
수집 단계 — 2026-10-03 대장 지정: "데이터가 어느정도 쌓여야 신뢰성이
확보된다, 지금 선행적으로 쌓아가야 해."

★ 이 모듈은 봇 매매 로직에 연동되지 않는다(대장 명시) — 월/분기 단위
수동 분석용 참고자료 데이터만 쌓는다.

[데이터 출처]
- FOMC: 연준이 1~2년 전에 공식 발표하는 연간 일정(2024~2026 전부 확정).
- 한국 금통위: 한국은행이 전년도 10월경 발표하는 연간 일정(2024~2026
  확정 — 2026년은 신시스 등 보도 기준).
- 실적시즌: ★ 아직 정확한 과거 날짜 미수집(TODO) — 종목별 실적발표일은
  분기마다 조금씩 달라서 FOMC/금통위처럼 고정 일정표가 없음. 우선
  삼성전자 잠정실적(매 분기 가장 이른 발표, 한국 "실적시즌 시작" 신호로
  쓸만함)부터 보강 예정.

[사용법]
  python3 macro_calendar.py --init   # DB+테이블 생성, 확정된 과거 일정 적재
  python3 macro_calendar.py --list   # 적재된 이벤트 전체 출력
"""
import os
import sqlite3
import argparse

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "macro_calendar.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    conn = _connect()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS macro_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            date        TEXT NOT NULL,       -- YYYY-MM-DD (결과 발표일 기준)
            event_type  TEXT NOT NULL,       -- 'FOMC' / '금통위' / '실적_<종목명>'
            description TEXT,
            source      TEXT,                -- 확인 출처(웹서치/공식발표 등 간단메모)
            UNIQUE(date, event_type)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_macro_date ON macro_events(date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_macro_type ON macro_events(event_type)")
    conn.commit()
    conn.close()
    print(f"✅ 매크로 캘린더 DB ({DB_PATH})")


def _insert(conn, date, event_type, description, source):
    conn.execute("""
        INSERT OR IGNORE INTO macro_events (date, event_type, description, source)
        VALUES (?,?,?,?)
    """, (date, event_type, description, source))


# ============================================================
# FOMC — 2026-10-03 WebSearch로 확인(연준 공식 발표 기준, 2일 회의 중 결과
# 발표일인 둘째날 날짜만 기록)
# ============================================================
FOMC_DATES_2024_2026 = [
    "2024-01-31", "2024-03-20", "2024-05-01", "2024-06-12",
    "2024-07-31", "2024-09-18", "2024-11-07", "2024-12-18",
    "2025-01-29", "2025-03-19", "2025-05-07", "2025-06-18",
    "2025-07-30", "2025-09-17", "2025-10-29", "2025-12-10",
    "2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
    "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09",
]

# ============================================================
# 한국은행 금융통화위원회(통화정책방향 결정회의) — 2026-10-03 WebSearch로
# 확인(한국은행 공식 발표/보도 기준)
# ============================================================
BOK_DATES_2024_2026 = [
    "2024-01-11", "2024-02-22", "2024-04-12", "2024-05-23",
    "2024-07-11", "2024-08-22", "2024-10-11", "2024-11-28",
    "2025-01-16", "2025-02-25", "2025-04-17", "2025-05-29",
    "2025-07-10", "2025-08-28", "2025-10-23", "2025-11-27",
    "2026-01-15", "2026-02-26", "2026-04-10", "2026-05-28",
    "2026-07-16", "2026-08-27", "2026-10-22", "2026-11-26",
]


def seed_confirmed_dates():
    conn = _connect()
    for d in FOMC_DATES_2024_2026:
        _insert(conn, d, "FOMC", "FOMC 금리결정 발표일(2일 회의 중 둘째날)",
                "federalreserve.gov 공식 일정(2026-10-03 확인)")
    for d in BOK_DATES_2024_2026:
        _insert(conn, d, "금통위", "한국은행 금융통화위원회 통화정책방향 결정회의",
                "한국은행/언론보도 교차확인(2026-10-03 확인)")
    conn.commit()
    conn.close()
    print(f"✅ FOMC {len(FOMC_DATES_2024_2026)}건 + 금통위 {len(BOK_DATES_2024_2026)}건 적재 완료")
    print("⚠️ 실적시즌(분기실적) 데이터는 아직 미수집 — 종목별 정확한 날짜 보강 필요(TODO)")


def list_events():
    conn = _connect()
    rows = conn.execute(
        "SELECT date, event_type, description FROM macro_events ORDER BY date"
    ).fetchall()
    conn.close()
    for date, etype, desc in rows:
        print(f"{date}  [{etype}]  {desc}")
    print(f"\n총 {len(rows)}건")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="매크로 캘린더 이벤트 DB")
    parser.add_argument("--init", action="store_true", help="DB 생성 + 확정된 과거 일정 적재")
    parser.add_argument("--list", action="store_true", help="적재된 이벤트 전체 출력")
    args = parser.parse_args()

    if args.init:
        init_db()
        seed_confirmed_dates()
    if args.list:
        list_events()
    if not args.init and not args.list:
        parser.print_help()
