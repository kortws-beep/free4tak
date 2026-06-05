"""
fix_db_dates.py — DB 날짜 정규화 + 중복 제거
==============================================
문제: YYYYMMDD와 YYYY-MM-DD 형식이 동시에 존재
     → 같은 종목+날짜가 두 번 저장된 중복 상태
     → UPDATE 시 UNIQUE 제약 위반

해결:
  1. YYYYMMDD 형식 행을 삭제 (YYYY-MM-DD 형식이 이미 있으므로)
  2. YYYY-MM-DD 없는 경우만 변환
"""
import sqlite3
import shutil
import os

DB_PATH = "/home/free4tak/k-bot/stock_bot/backtest/data/backtest_data.db"

# 백업
backup = DB_PATH + ".bak_date"
shutil.copy(DB_PATH, backup)
print(f"✅ DB 백업: {backup}")

conn = sqlite3.connect(DB_PATH, timeout=30)
conn.execute("PRAGMA journal_mode = WAL")

for tbl in ["daily_ohlcv", "daily_flow"]:
    print(f"\n[ {tbl} ]")

    # YYYYMMDD 형식 날짜 목록
    bad_dates = conn.execute(
        f"SELECT DISTINCT date FROM {tbl} "
        f"WHERE length(date)=8 AND date NOT LIKE '%-%-_%'"
    ).fetchall()
    bad_dates = [r[0] for r in bad_dates if r[0].isdigit()]

    if not bad_dates:
        print(f"  ✅ 변환 필요 없음")
        continue

    print(f"  YYYYMMDD 형식 날짜: {len(bad_dates)}개")

    del_cnt  = 0
    upd_cnt  = 0

    for dt in bad_dates:
        new_dt = f"{dt[:4]}-{dt[4:6]}-{dt[6:]}"

        # YYYY-MM-DD 형식이 이미 있는지 확인
        if tbl == "daily_ohlcv":
            exists = conn.execute(
                f"SELECT COUNT(*) FROM {tbl} "
                f"WHERE date=? AND code IN "
                f"(SELECT code FROM {tbl} WHERE date=?)",
                (new_dt, dt)
            ).fetchone()[0]
        else:
            exists = conn.execute(
                f"SELECT COUNT(*) FROM {tbl} WHERE date=?",
                (new_dt,)
            ).fetchone()[0]

        if exists > 0:
            # 이미 YYYY-MM-DD 존재 → YYYYMMDD 행 삭제
            conn.execute(f"DELETE FROM {tbl} WHERE date=?", (dt,))
            del_cnt += 1
        else:
            # YYYY-MM-DD 없음 → 변환
            conn.execute(
                f"UPDATE {tbl} SET date=? WHERE date=?",
                (new_dt, dt)
            )
            upd_cnt += 1

    conn.commit()
    print(f"  삭제(중복): {del_cnt}건")
    print(f"  변환(신규): {upd_cnt}건")

# 결과 확인
print("\n📊 정규화 후 상태:")
for tbl in ["daily_ohlcv", "daily_flow"]:
    r = conn.execute(
        f"SELECT MIN(date), MAX(date), COUNT(*) FROM {tbl}"
    ).fetchone()
    # 잔여 YYYYMMDD 확인
    bad = conn.execute(
        f"SELECT COUNT(*) FROM {tbl} "
        f"WHERE length(date)=8 AND date NOT LIKE '%-%-_%'"
    ).fetchone()[0]
    print(f"  {tbl}: {r[0]} ~ {r[1]} ({r[2]:,}건) | YYYYMMDD 잔여: {bad}건")

conn.close()
print("\n✅ DB 정규화 완료!")
print("\n다음 명령으로 재실행:")
print("  python3 run_backtest.py --compare --start 2025-08-01 --end 2026-05-01 --max-codes 50")
