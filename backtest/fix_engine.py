"""
backtest_engine.py 버그 수정 스크립트
- Series ambiguous 버그 3곳 수정
"""
import os
import shutil

path = "/home/free4tak/k-bot/stock_bot/backtest/backtest_engine.py"

# 백업
shutil.copy(path, path + ".bak")
print(f"✅ 백업: {path}.bak")

with open(path, "r") as f:
    code = f.read()

# ── 수정 1: atr14 + ma10 ─────────────────────────────
old1 = '''            # ATR (손절선 동적 조정용)
            df = self.loader.load_ohlcv(code)
            atr_rate = 0
            if not df.empty and "atr14" in df.columns and date in df.index:
                atr14 = df.loc[date].get("atr14", 0)
                cur_atr = atr14.iloc[0] if hasattr(atr14, 'iloc') else atr14
                if cur_atr > 0 and pos["entry_price"] > 0:
                    atr_rate = atr14 / pos["entry_price"]

            ma10 = 0
            if not df.empty and "ma10" in df.columns and date in df.index:
                ma10 = df.loc[date].get("ma10", 0) or 0'''

new1 = '''            # ATR (손절선 동적 조정용)
            df = self.loader.load_ohlcv(code)
            atr_rate = 0
            if not df.empty and "atr14" in df.columns and date in df.index:
                _row = df.loc[date]
                if hasattr(_row, 'iloc'): _row = _row.iloc[-1]
                atr14 = float(_row.get("atr14") or 0)
                if atr14 > 0 and pos["entry_price"] > 0:
                    atr_rate = atr14 / pos["entry_price"]

            ma10 = 0
            if not df.empty and "ma10" in df.columns and date in df.index:
                _row = df.loc[date]
                if hasattr(_row, 'iloc'): _row = _row.iloc[-1]
                ma10 = float(_row.get("ma10") or 0)'''

# ── 수정 2: _record_equity ──────────────────────────
old2 = '''            if not df.empty and date in df.index:
                market_value += float(df.loc[date]["close"]) * pos["qty"]'''

new2 = '''            if not df.empty and date in df.index:
                _row = df.loc[date]
                if hasattr(_row, 'iloc'): _row = _row.iloc[-1]
                market_value += float(_row["close"]) * pos["qty"]'''

# ── 수정 3: 마지막 강제 청산 ────────────────────────
old3 = '''            if not df.empty and last_date in df.index:
                self._simulate_sell(
                    code, self.positions[code]["qty"],
                    float(df.loc[last_date]["close"].iloc[-1] if hasattr(df.loc[last_date]["close"], 'iloc') else df.loc[last_date]["close"]),
                    "백테스트종료", last_str)'''

new3 = '''            if not df.empty and last_date in df.index:
                _row = df.loc[last_date]
                if hasattr(_row, 'iloc'): _row = _row.iloc[-1]
                self._simulate_sell(
                    code, self.positions[code]["qty"],
                    float(_row["close"]),
                    "백테스트종료", last_str)'''

# 적용
cnt = 0
for old, new in [(old1, new1), (old2, new2), (old3, new3)]:
    if old in code:
        code = code.replace(old, new)
        cnt += 1
        print(f"✅ 수정 {cnt} 적용")
    else:
        print(f"⚠️ 수정 {cnt+1} — 패턴 못 찾음 (이미 수정됐거나 코드 다름)")
        cnt += 1

with open(path, "w") as f:
    f.write(code)

# 문법 검증
import ast
try:
    ast.parse(code)
    print("\n✅ 문법 OK — 수정 완료!")
except SyntaxError as e:
    print(f"\n❌ 문법 오류: {e}")
    print("   백업으로 복구 중...")
    shutil.copy(path + ".bak", path)
    print("   복구 완료")
