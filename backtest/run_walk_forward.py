import os
import pandas as pd
import itertools
import sqlite3
from backtest_engine import BacktestEngine, BacktestConfig
from metrics import calc_metrics

def generate_params():
    params = {
        "buy_score_min":   [60, 70],
        "time_stop_days":  [0, 5],          # 0=없음, 5=5일청산
        "max_positions":   [5],
        "stop_loss_rate":  [-0.07, -0.10],  # 손절선
        "sell_1st_rate":   [0.03, 0.05],    # 1차 익절
        "sell_2nd_rate":   [0.06, 0.10],    # 2차 익절
        "sell_3rd_rate":   [0.10, 0.15],    # 3차 익절
    }
    keys = params.keys()
    for instance in itertools.product(*params.values()):
        yield dict(zip(keys, instance))

def run_walk_forward():
    print("\n🔄 전진 분석 시작...\n")
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data/backtest_data.db")
    
    conn = sqlite3.connect(db_path)
    codes = [row[0] for row in conn.execute("SELECT DISTINCT code FROM daily_ohlcv").fetchall()]
    conn.close()

    total_start = pd.to_datetime("2024-09-01")  # ★ 60일 워밍업 후
    total_end   = pd.to_datetime("2026-06-05")  # ★ DB 최신 데이터
    
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
        oos_profit = final_asset_oos - config_oos.initial_cash
        oos_return = oos_profit / config_oos.initial_cash * 100
        print(f"   💸 검증 수익: {oos_profit:,.0f}원 ({oos_return:+.2f}%)")

        results.append({
            "train": f"{current_train_start.date()}~{train_end.date()}",
            "test":  f"{train_end.date()}~{test_end.date()}",
            "best_params": best_params,
            "train_profit": best_profit,
            "oos_profit":   oos_profit,
            "oos_return":   oos_return,
        })
        
        current_train_start += pd.DateOffset(months=1)

    # ── 최종 요약 ─────────────────────────────────────────
    print("\n" + "="*70)
    print("📊 워크포워드 최종 요약")
    print("="*70)
    print(f"{'구간':<25} {'최적파라미터(임계/손절/익절1)':<35} {'검증수익률':>10}")
    print("-"*70)
    for r in results:
        p = r["best_params"]
        label = f"임계{p.get('buy_score_min',60)}/손절{p.get('stop_loss_rate',-0.07):.0%}/익절{p.get('sell_1st_rate',0.03):.0%}"
        sign = "✅" if r["oos_return"] > 0 else "❌"
        print(f"{r['test']:<25} {label:<35} {r['oos_return']:>+8.2f}% {sign}")

    wins = [r for r in results if r["oos_return"] > 0]
    total_oos = sum(r["oos_profit"] for r in results)
    print("-"*70)
    print(f"검증 승률: {len(wins)}/{len(results)} ({len(wins)/len(results)*100:.0f}%)")
    print(f"검증 누적수익: {total_oos:,.0f}원")

    # 가장 많이 선택된 파라미터
    from collections import Counter
    param_counts = Counter(
        f"임계{r['best_params'].get('buy_score_min',60)}/손절{r['best_params'].get('stop_loss_rate',-0.07):.0%}/시간청산{r['best_params'].get('time_stop_days',0)}일"
        for r in results
    )
    print("\n🏆 가장 많이 선택된 파라미터:")
    for param, cnt in param_counts.most_common(3):
        print(f"  {param}: {cnt}회")
    print("="*70)

if __name__ == "__main__":
    results = []
    run_walk_forward()