import sys

path = sys.argv[1] if len(sys.argv) > 1 else "run_sbo2_backtest.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

results = []

# argparse에 --ma-period 옵션 추가
old1 = '''    parser.add_argument("--buy-score-min", type=int, default=75,'''
new1 = '''    parser.add_argument("--ma-period",     type=int, default=20,
                        help="MA이탈 매도 기준 기간 (기본20, 검증용 40)")
    parser.add_argument("--buy-score-min", type=int, default=75,'''
if old1 in content:
    content = content.replace(old1, new1, 1)
    results.append("✅ --ma-period 인자 추가")
else:
    results.append("❌ argparse 위치 미일치")

# base_config 생성 시 ma_period 전달
old2 = '''    base_config = SBo2BacktestConfig(
        initial_cash=args.initial_cash, base_buy_amt=args.base_buy_amt,
        max_positions=args.max_positions, buy_score_min=args.buy_score_min,
        start_date=args.start, end_date=end_date, codes=codes,
        verbose=args.verbose,
    )'''
new2 = '''    base_config = SBo2BacktestConfig(
        initial_cash=args.initial_cash, base_buy_amt=args.base_buy_amt,
        max_positions=args.max_positions, buy_score_min=args.buy_score_min,
        start_date=args.start, end_date=end_date, codes=codes,
        verbose=args.verbose, ma_period=args.ma_period,
    )'''
if old2 in content:
    content = content.replace(old2, new2, 1)
    results.append("✅ base_config에 ma_period 전달")
else:
    results.append("❌ base_config 생성부 미일치")

print("\n".join(results))
with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
