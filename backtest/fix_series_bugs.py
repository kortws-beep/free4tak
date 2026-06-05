"""
fix_series_bugs.py — Series ambiguous 버그 전체 일괄 수정
==========================================================
수정 대상:
  1. feature_builder.py: get_market_data_at (중복 날짜 방어)
  2. feature_builder.py: load_flow format='mixed'
  3. backtest_engine.py: _record_equity (중복 날짜 방어)
  4. DB: YYYYMMDD → YYYY-MM-DD 정규화
"""
import os
import ast
import shutil
import sqlite3

BASE    = "/home/free4tak/k-bot/stock_bot"
FB_PATH = f"{BASE}/backtest/feature_builder.py"
BE_PATH = f"{BASE}/backtest/backtest_engine.py"
DB_PATH = f"{BASE}/backtest/data/backtest_data.db"


def patch(path, old, new, label):
    with open(path) as f:
        code = f.read()
    if old in code:
        code = code.replace(old, new)
        with open(path, "w") as f:
            f.write(code)
        try:
            ast.parse(code)
            print(f"  ✅ {label}")
        except SyntaxError as e:
            print(f"  ❌ 문법오류 {label}: {e}")
    else:
        print(f"  ⚠️  {label} — 패턴 없음 (이미 수정됐거나 다름)")


# ── 백업 ─────────────────────────────────────────────
for p in [FB_PATH, BE_PATH]:
    shutil.copy(p, p + ".bak2")
print("✅ 백업 완료 (.bak2)\n")


# ============================================================
# 1. feature_builder.py — get_market_data_at
# ============================================================
print("[ 1 ] feature_builder.py — get_market_data_at 중복날짜 방어")

patch(FB_PATH,
    old='''    df = loader.load_ohlcv(code)
    if df.empty or date not in df.index:
        return None
    row = df.loc[date]
    price_map = {
        "close": row["close"],
        "open":  row["open"],
        "high":  row["high"],
        "low":   row["low"],
    }
    return {
        "stck_prpr":   str(int(price_map.get(price_type, row["close"]))),
        "stck_oprc":   str(int(row["open"])),
        "stck_hgpr":   str(int(row["high"])),
        "stck_lwpr":   str(int(row["low"])),
        "prdy_ctrt":   str(row["change"]),
    }''',
    new='''    df = loader.load_ohlcv(code)
    if df.empty or date not in df.index:
        return None
    row = df.loc[date]
    if hasattr(row, 'iloc'):   # 중복 날짜 → DataFrame 반환 방어
        row = row.iloc[-1]
    price_map = {
        "close": float(row["close"]),
        "open":  float(row["open"]),
        "high":  float(row["high"]),
        "low":   float(row["low"]),
    }
    return {
        "stck_prpr":   str(int(price_map.get(price_type, price_map["close"]))),
        "stck_oprc":   str(int(price_map["open"])),
        "stck_hgpr":   str(int(price_map["high"])),
        "stck_lwpr":   str(int(price_map["low"])),
        "prdy_ctrt":   str(float(row["change"])),
    }''',
    label="get_market_data_at 수정")


# ============================================================
# 2. feature_builder.py — load_flow format='mixed'
# ============================================================
print("\n[ 2 ] feature_builder.py — load_flow format='mixed'")

patch(FB_PATH,
    old='            df["date"] = pd.to_datetime(df["date"])',
    new='            df["date"] = pd.to_datetime(df["date"], format="mixed")',
    label="load_flow format='mixed'")


# ============================================================
# 3. backtest_engine.py — _record_equity
# ============================================================
print("\n[ 3 ] backtest_engine.py — _record_equity 중복날짜 방어")

patch(BE_PATH,
    old='''            if not df.empty and date in df.index:
                market_value += float(df.loc[date]["close"]) * pos["qty"]''',
    new='''            if not df.empty and date in df.index:
                _r = df.loc[date]
                if hasattr(_r, 'iloc'): _r = _r.iloc[-1]
                market_value += float(_r["close"]) * pos["qty"]''',
    label="_record_equity 수정")


# ============================================================
# 4. DB 날짜 정규화 (YYYYMMDD → YYYY-MM-DD)
# ============================================================
print("\n[ 4 ] DB 날짜 정규화 (YYYYMMDD → YYYY-MM-DD)")

if not os.path.exists(DB_PATH):
    print(f"  ⚠️  DB 없음: {DB_PATH}")
else:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode = WAL")

    for tbl in ["daily_ohlcv", "daily_flow"]:
        try:
            rows = conn.execute(
                f"SELECT DISTINCT date FROM {tbl} "
                f"WHERE date NOT LIKE '%-%-_%' AND length(date) = 8"
            ).fetchall()
            if not rows:
                print(f"  {tbl}: 변환 필요 없음")
                continue
            cnt = 0
            for (dt,) in rows:
                if len(dt) == 8 and dt.isdigit():
                    new_dt = f"{dt[:4]}-{dt[4:6]}-{dt[6:]}"
                    conn.execute(f"UPDATE {tbl} SET date=? WHERE date=?",
                                 (new_dt, dt))
                    cnt += 1
            conn.commit()
            print(f"  ✅ {tbl}: {cnt}건 변환 완료")
        except Exception as e:
            print(f"  ❌ {tbl}: {e}")

    # 확인
    print("\n  📊 정규화 후 날짜 샘플:")
    for tbl in ["daily_ohlcv", "daily_flow"]:
        try:
            r = conn.execute(
                f"SELECT MIN(date), MAX(date), COUNT(*) FROM {tbl}"
            ).fetchone()
            print(f"    {tbl}: {r[0]} ~ {r[1]} ({r[2]:,}건)")
        except:
            pass
    conn.close()


print("\n" + "=" * 50)
print("✅ 모든 수정 완료!")
print("=" * 50)
print("\n다음 명령으로 재실행:")
print("  cd /home/free4tak/k-bot/stock_bot/backtest")
print("  python3 run_backtest.py --compare --start 2025-08-01 --end 2026-05-01 --max-codes 50")
