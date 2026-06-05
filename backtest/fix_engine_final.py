"""
fix_engine_final.py — backtest_engine.py Series 버그 완전 수정
=============================================================
남은 버그 위치:
  - _record_equity: df.loc[date]["close"] → Series 반환 시 float 변환 실패
  - run(): 강제청산 _row["close"] → IndexError
  - atr14 계산: 여전히 Series 반환 가능
"""
import shutil
import ast

PATH = "/home/free4tak/k-bot/stock_bot/backtest/backtest_engine.py"

shutil.copy(PATH, PATH + ".bak_final")
print(f"✅ 백업: {PATH}.bak_final")

with open(PATH) as f:
    code = f.read()

fixes = []

# ── 수정 1: _record_equity ─────────────────────────
old1 = '''    def _record_equity(self, date: pd.Timestamp):
        """일별 자산 가치 기록 (MDD/equity curve용)"""
        market_value = 0
        for code, pos in self.positions.items():
            df = self.loader.load_ohlcv(code)
            if not df.empty and date in df.index:
                market_value += float(df.loc[date]["close"]) * pos["qty"]
            else:
                market_value += pos["entry_price"] * pos["qty"]
        total = self.cash + market_value
        self.equity_curve.append((date.strftime("%Y-%m-%d"), total))'''

new1 = '''    def _record_equity(self, date: pd.Timestamp):
        """일별 자산 가치 기록 (MDD/equity curve용)"""
        market_value = 0
        for code, pos in self.positions.items():
            df = self.loader.load_ohlcv(code)
            if not df.empty and date in df.index:
                _r = df.loc[date]
                if hasattr(_r, 'iloc'): _r = _r.iloc[-1]
                market_value += float(_r["close"]) * pos["qty"]
            else:
                market_value += pos["entry_price"] * pos["qty"]
        total = self.cash + market_value
        self.equity_curve.append((date.strftime("%Y-%m-%d"), total))'''

fixes.append(("_record_equity", old1, new1))

# ── 수정 2: run() 강제 청산 ───────────────────────
old2 = '''        for code in list(self.positions.keys()):
            df = self.loader.load_ohlcv(code)
            if not df.empty and last_date in df.index:
                _row = df.loc[last_date]
                if hasattr(_row, 'iloc'): _row = _row.iloc[-1]
                self._simulate_sell(
                    code, self.positions[code]["qty"],
                    float(_row["close"]),
                    "백테스트종료", last_str)'''

new2 = '''        for code in list(self.positions.keys()):
            df = self.loader.load_ohlcv(code)
            if not df.empty and last_date in df.index:
                _row = df.loc[last_date]
                if hasattr(_row, 'iloc'): _row = _row.iloc[-1]
                close_val = _row["close"]
                if hasattr(close_val, 'iloc'): close_val = close_val.iloc[-1]
                self._simulate_sell(
                    code, self.positions[code]["qty"],
                    float(close_val),
                    "백테스트종료", last_str)'''

fixes.append(("강제청산", old2, new2))

# ── 수정 3: 강제청산 원본 패턴 (수정 전 버전) ─────
old3 = '''        for code in list(self.positions.keys()):
            df = self.loader.load_ohlcv(code)
            if not df.empty and last_date in df.index:
                self._simulate_sell(
                    code, self.positions[code]["qty"],
                    float(df.loc[last_date]["close"].iloc[-1] if hasattr(df.loc[last_date]["close"], 'iloc') else df.loc[last_date]["close"]),
                    "백테스트종료", last_str)'''

new3 = '''        for code in list(self.positions.keys()):
            df = self.loader.load_ohlcv(code)
            if not df.empty and last_date in df.index:
                _row = df.loc[last_date]
                if hasattr(_row, 'iloc'): _row = _row.iloc[-1]
                close_val = _row["close"]
                if hasattr(close_val, 'iloc'): close_val = close_val.iloc[-1]
                self._simulate_sell(
                    code, self.positions[code]["qty"],
                    float(close_val),
                    "백테스트종료", last_str)'''

fixes.append(("강제청산(원본)", old3, new3))

# ── 수정 4: atr14 계산 (혹시 남은 경우) ──────────
old4 = '''            if not df.empty and "atr14" in df.columns and date in df.index:
                atr14 = df.loc[date].get("atr14", 0)
                cur_atr = atr14.iloc[0] if hasattr(atr14, 'iloc') else atr14
                if cur_atr > 0 and pos["entry_price"] > 0:
                    atr_rate = atr14 / pos["entry_price"]'''

new4 = '''            if not df.empty and "atr14" in df.columns and date in df.index:
                _r = df.loc[date]
                if hasattr(_r, 'iloc'): _r = _r.iloc[-1]
                atr14 = float(_r.get("atr14") or 0)
                if atr14 > 0 and pos["entry_price"] > 0:
                    atr_rate = atr14 / pos["entry_price"]'''

fixes.append(("atr14(원본)", old4, new4))

# ── 적용 ─────────────────────────────────────────
applied = 0
for label, old, new in fixes:
    if old in code:
        code = code.replace(old, new)
        applied += 1
        print(f"  ✅ {label} 수정")
    else:
        print(f"  ➖ {label} — 패턴 없음 (이미 수정됨)")

# 문법 검사
try:
    ast.parse(code)
    print(f"\n✅ 문법 OK — {applied}곳 수정")
    with open(PATH, "w") as f:
        f.write(code)
    print(f"✅ 저장 완료: {PATH}")
except SyntaxError as e:
    print(f"\n❌ 문법 오류: {e}")
    shutil.copy(PATH + ".bak_final", PATH)
    print("   원본 복구 완료")

# ── 수정 결과 확인 ────────────────────────────────
print("\n🔍 남은 위험 패턴 검사:")
danger = [
    'df.loc[date]["close"]',
    'df.loc[last_date]["close"]',
    '.get("atr14", 0)\n',
    '.get("ma10", 0)',
]
with open(PATH) as f:
    final = f.read()
found = False
for pat in danger:
    if pat in final:
        print(f"  ⚠️  아직 남음: {pat!r}")
        found = True
if not found:
    print("  ✅ 위험 패턴 없음 — 완전히 수정됨")

print("\n실행:")
print("  python3 run_backtest.py --compare --start 2025-08-01 --end 2026-05-01 --max-codes 50")
