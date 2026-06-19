import sys

path = sys.argv[1] if len(sys.argv) > 1 else "sbo2_backtest_engine.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

results = []

# 1. Config에 ma_period 옵션 추가
old1 = '''    buy_score_min:     int   = 65'''
new1 = '''    buy_score_min:     int   = 65
    ma_period:         int   = 20      # ★ MA이탈 매도 기준 기간 (기본20, 검증용 40 등 가능)'''
if old1 in content:
    content = content.replace(old1, new1, 1)
    results.append("✅ ma_period 옵션 추가")
else:
    results.append("❌ Config 위치 미일치")

# 2. ma20 계산 부분을 ma_period 기반 직접 계산으로 교체
old2 = '''            # ATR / MA20 — DB 컬럼 우선, 없으면 features에서 보완
            df       = self.loader.load_ohlcv(code)
            atr_rate = 0.0
            ma20     = 0.0
            if not df.empty and date in df.index:
                _row = df.loc[date]
                if hasattr(_row, "columns"): _row = _row.iloc[-1]
                try:
                    atr14 = float(_row.get("atr14", 0) or 0)
                    if atr14 > 0 and pos["entry_price"] > 0:
                        atr_rate = atr14 / pos["entry_price"]
                    ma20 = float(_row.get("ma20", 0) or 0)
                except Exception:
                    pass
            # ★ DB 컬럼에 없으면 feature_builder 결과에서 보완
            if ma20 == 0:
                feat_now = build_features_at(self.loader, code, date)
                if feat_now:
                    ma20     = float(feat_now.get("ma20", 0) or 0)
                    atr_rate = atr_rate or float(feat_now.get("atr_rate", 0) or 0)'''

new2 = '''            # ATR / MA(가변기간) — DB 컬럼 우선, 없으면 features에서 보완
            df       = self.loader.load_ohlcv(code)
            atr_rate = 0.0
            ma20     = 0.0
            _ma_period = getattr(self.config, "ma_period", 20)
            if not df.empty and date in df.index:
                _row = df.loc[date]
                if hasattr(_row, "columns"): _row = _row.iloc[-1]
                try:
                    atr14 = float(_row.get("atr14", 0) or 0)
                    if atr14 > 0 and pos["entry_price"] > 0:
                        atr_rate = atr14 / pos["entry_price"]
                except Exception:
                    pass
                # ★ ma_period가 20이 아니면 close 시계열로 직접 계산 (검증용)
                if _ma_period != 20:
                    try:
                        _hist = df.loc[:date]["close"].tail(_ma_period)
                        if len(_hist) >= _ma_period:
                            ma20 = float(_hist.mean())
                    except Exception:
                        pass
                else:
                    try:
                        ma20 = float(_row.get("ma20", 0) or 0)
                    except Exception:
                        pass
            # ★ DB 컬럼에 없으면 feature_builder 결과에서 보완 (ma_period=20 한정)
            if ma20 == 0 and _ma_period == 20:
                feat_now = build_features_at(self.loader, code, date)
                if feat_now:
                    ma20     = float(feat_now.get("ma20", 0) or 0)
                    atr_rate = atr_rate or float(feat_now.get("atr_rate", 0) or 0)'''

if old2 in content:
    content = content.replace(old2, new2, 1)
    results.append("✅ MA 가변기간 계산 로직 추가")
else:
    results.append("❌ MA20 계산부 미일치")

print("\n".join(results))
with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
