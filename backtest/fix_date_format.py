"""
fix_date_format.py — 날짜 형식 혼용 버그 수정
================================================
문제: fetch_all_data_kis.py는 날짜를 'YYYYMMDD'로 저장
     fetch_history_fdr.py는 'YYYY-MM-DD'로 저장
     → DB에 두 형식이 섞여 pd.to_datetime 파싱 실패

해결:
  1. feature_builder.py load_flow → format='mixed' 적용
  2. DB의 YYYYMMDD 날짜를 모두 YYYY-MM-DD로 정규화
"""
import os
import re
import sqlite3
import shutil

BASE = "/home/free4tak/k-bot/stock_bot"
FB_PATH = f"{BASE}/backtest/feature_builder.py"
DB_PATH = f"{BASE}/backtest/data/backtest_data.db"


# ============================================================
# 1. feature_builder.py 수정
# ============================================================
print("=" * 50)
print("1. feature_builder.py 수정")
print("=" * 50)

shutil.copy(FB_PATH, FB_PATH + ".bak")

with open(FB_PATH, "r") as f:
    code = f.read()

old = '            df["date"] = pd.to_datetime(df["date"])'
new = '            df["date"] = pd.to_datetime(df["date"], format="mixed")'

if old in code:
    code = code.replace(old, new)
    print("✅ load_flow: format='mixed' 적용")
else:
    print("⚠️ 패턴 못 찾음 — 이미 수정됐거나 다른 형태")

with open(FB_PATH, "w") as f:
    f.write(code)

import ast
try:
    ast.parse(code)
    print("✅ feature_builder.py 문법 OK")
except SyntaxError as e:
    print(f"❌ 문법 오류: {e} — 백업 복구")
    shutil.copy(FB_PATH + ".bak", FB_PATH)


# ============================================================
# 2. DB 날짜 정규화 (YYYYMMDD → YYYY-MM-DD)
# ============================================================
print("\n" + "=" * 50)
print("2. DB 날짜 정규화")
print("=" * 50)

if not os.path.exists(DB_PATH):
    print(f"❌ DB 없음: {DB_PATH}")
    exit(1)

conn = sqlite3.connect(DB_PATH, timeout=30)
conn.execute("PRAGMA journal_mode = WAL")

def normalize_dates(table: str, conn: sqlite3.Connection):
    """YYYYMMDD 형식 날짜를 YYYY-MM-DD로 변환"""
    # YYYYMMDD 형식인 것만 찾기 (8자리 숫자, 하이픈 없음)
    rows = conn.execute(
        f"SELECT DISTINCT date FROM {table} WHERE date NOT LIKE '%-%-' AND length(date) = 8"
    ).fetchall()
    
    if not rows:
        print(f"   {table}: 변환 필요한 날짜 없음")
        return 0
    
    print(f"   {table}: YYYYMMDD 형식 {len(rows)}개 날짜 변환 중...")
    cnt = 0
    for (dt,) in rows:
        # YYYYMMDD → YYYY-MM-DD
        if len(dt) == 8 and dt.isdigit():
            new_dt = f"{dt[:4]}-{dt[4:6]}-{dt[6:]}"
            conn.execute(
                f"UPDATE {table} SET date = ? WHERE date = ?",
                (new_dt, dt)
            )
            cnt += 1
    conn.commit()
    print(f"   {table}: {cnt}건 변환 완료")
    return cnt

for tbl in ["daily_ohlcv", "daily_flow"]:
    try:
        normalize_dates(tbl, conn)
    except Exception as e:
        print(f"   {tbl}: 오류 — {e}")

# 결과 확인
print("\n📊 정규화 후 샘플:")
for tbl in ["daily_ohlcv", "daily_flow"]:
    try:
        rows = conn.execute(
            f"SELECT date FROM {tbl} ORDER BY date LIMIT 3"
        ).fetchall()
        print(f"   {tbl}: {[r[0] for r in rows]}")
    except Exception as e:
        print(f"   {tbl}: {e}")

conn.close()
print("\n✅ 모든 수정 완료!")
print("\n이제 다시 실행하세요:")
print("  python3 run_backtest.py --compare --start 2025-08-01 --end 2026-05-01 --max-codes 50")
