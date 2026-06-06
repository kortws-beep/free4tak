import pandas as pd
import itertools
import sqlite3
from backtest_engine import BacktestEngine, BacktestConfig
from metrics import calc_metrics

def generate_params():
    params = {"buy_score_min": [60, 70], "time_stop_days": [3, 5], "max_positions": [5]}
    keys = params.keys()
    for instance in itertools.product(*params.values()):
        yield dict(zip(keys, instance))

def run_walk_forward():
    print("\n🔄 전진 분석 시작...\n")
    db_path = "data/backtest_data.db"
    
    conn = sqlite3.connect(db_path)
    codes = [row[0] for row in conn.execute("SELECT DISTINCT code FROM daily_ohlcv").fetchall()]
    conn.close()

    total_start = pd.to_datetime("2024-01-01")
    total_end = pd.to_datetime("2026-05-18")
    
    current_train_start = total_start
    
    while True:
        train_end = current_train_start + pd.DateOffset(months=2)
        test_end = train_end + pd.DateOffset(months=1)
        if test_end > total_end: break

        print(f"🗓️ 구간: {current_train_start.date()} ~ {train_end.date()} (학습) -> {test_end.date()} (검증)")

        best_params, best_profit = None, -999999999
        for params in generate_params():
            config = BacktestConfig(start_date=current_train_start.strftime("%Y-%m-%d"), end_date=train_end.strftime("%Y-%m-%d"), codes=codes, **params)
            engine = BacktestEngine(config, db_path)
            engine.run()
            
            equity = engine.get_equity_curve()
            final_asset = float(equity[-1][1]) if (len(equity) > 0 and isinstance(equity[-1], tuple)) else (float(equity[-1]) if len(equity) > 0 else config.initial_cash)
            profit = final_asset - config.initial_cash
            
            if profit > best_profit:
                best_profit, best_params = profit, params

        print(f"   🏆 최적 파라미터: {best_params} | 학습 수익: {best_profit:,.0f}원")
        
        config_oos = BacktestConfig(start_date=train_end.strftime("%Y-%m-%d"), end_date=test_end.strftime("%Y-%m-%d"), codes=codes, **best_params)
        engine_oos = BacktestEngine(config_oos, db_path)
        engine_oos.run()
        
        equity_oos = engine_oos.get_equity_curve()
        final_asset_oos = float(equity_oos[-1][1]) if (len(equity_oos) > 0 and isinstance(equity_oos[-1], tuple)) else (float(equity_oos[-1]) if len(equity_oos) > 0 else config_oos.initial_cash)
        print(f"   💸 검증 수익: {final_asset_oos - config_oos.initial_cash:,.0f}원")
        
        current_train_start += pd.DateOffset(months=1)

if __name__ == "__main__":
    run_walk_forward()