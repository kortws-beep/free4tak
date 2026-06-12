"""
db_query.py — DB 빠른 조회 유틸
사용법:
    python db_query.py 하이닉스         ← 종목명 검색
    python db_query.py 하이닉스 30      ← 종목명 + 최근 N일
    python db_query.py --themes 삼성전자 ← 종목 테마 조회
    python db_query.py --stats          ← 전체 DB 현황
"""

import sqlite3
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE_DIR, "kr_theme_finance.db")

conn   = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

args = sys.argv[1:]

# ── 전체 현황 ──────────────────────────────────────────────────
if not args or args[0] == "--stats":
    cursor.execute("SELECT COUNT(DISTINCT stock_name) FROM kr_stock_daily_data")
    total_stocks = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM kr_stock_daily_data")
    total_rows = cursor.fetchone()[0]

    cursor.execute("SELECT MIN(date), MAX(date) FROM kr_stock_daily_data")
    date_range = cursor.fetchone()

    cursor.execute("""
        SELECT COUNT(*) FROM kr_stock_daily_data
        WHERE foreign_net_buy IS NOT NULL
    """)
    supply_rows = cursor.fetchone()[0]

    cursor.execute("""
        SELECT stock_name, COUNT(*) as cnt
        FROM kr_stock_daily_data
        GROUP BY stock_name
        ORDER BY cnt DESC LIMIT 5
    """)
    top5 = cursor.fetchall()

    print(f"\n📊 [DB 전체 현황]")
    print(f"   종목 수     : {total_stocks:,}개")
    print(f"   총 행 수    : {total_rows:,}건")
    print(f"   날짜 범위   : {date_range[0]} ~ {date_range[1]}")
    print(f"   수급 있는 행: {supply_rows:,}건 ({supply_rows/total_rows*100:.1f}%)")
    print(f"\n   📈 데이터 많은 종목 TOP 5:")
    for name, cnt in top5:
        print(f"      {name}: {cnt}일치")

# ── 테마 조회 ──────────────────────────────────────────────────
elif args[0] == "--themes":
    keyword = args[1] if len(args) > 1 else ""
    cursor.execute("""
        SELECT DISTINCT stock_name FROM kr_stock_daily_data
        WHERE stock_name LIKE ?
    """, (f"%{keyword}%",))
    found = cursor.fetchall()

    if not found:
        print(f"\n❌ '{keyword}' 종목 없음")
    else:
        print(f"\n🔍 '{keyword}' DB 저장명: {[r[0] for r in found]}")
        for row in found:
            name = row[0]
            cursor.execute("""
                SELECT theme_name FROM kr_theme_stocks
                WHERE stock_name LIKE ?
            """, (f"%{name}%",))
            themes = [r[0] for r in cursor.fetchall()]
            print(f"   📂 테마: {', '.join(themes) if themes else '없음'}")

# ── 종목명 검색 + 데이터 조회 ──────────────────────────────────
else:
    keyword = args[0]
    limit   = int(args[1]) if len(args) > 1 else 30

    # 종목명 후보 먼저 탐색
    cursor.execute("""
        SELECT DISTINCT stock_name FROM kr_stock_daily_data
        WHERE stock_name LIKE ?
        ORDER BY stock_name
    """, (f"%{keyword}%",))
    candidates = [r[0] for r in cursor.fetchall()]

    if not candidates:
        print(f"\n❌ '{keyword}' 포함 종목 없음")
    elif len(candidates) > 1:
        print(f"\n🔍 '{keyword}' 검색 결과 {len(candidates)}개:")
        for c in candidates:
            print(f"   - {c}")
        print(f"\n   더 정확한 이름으로 다시 검색해봐!")
    else:
        name = candidates[0]
        cursor.execute("""
            SELECT date, close_price, foreign_net_buy, institution_net_buy
            FROM kr_stock_daily_data
            WHERE stock_name = ?
            ORDER BY date DESC
            LIMIT ?
        """, (name, limit))
        rows = cursor.fetchall()

        # 데이터 일수 / 수급 있는 일수
        supply_cnt = sum(1 for r in rows if r[2] is not None)

        print(f"\n📈 [{name}] 최근 {len(rows)}일치 (수급 있는 날: {supply_cnt}일)")
        print(f"{'날짜':<12} {'종가':>8} {'외인순매수':>14} {'기관순매수':>14}")
        print("-" * 52)
        for date, close, f_net, i_net in rows:
            f_str = f"{int(f_net):>+,}" if f_net is not None else "    NaN"
            i_str = f"{int(i_net):>+,}" if i_net is not None else "    NaN"
            print(f"{date:<12} {close:>8,} {f_str:>14} {i_str:>14}")

conn.close()
