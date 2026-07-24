"""
factor_screen.py — 신호소스별 팩터 스크리닝 (2026-07-25)
================================================================
[목적]
sbo2 겹침점수 시스템(텔레그램/한경컨센서스/MBN뉴스/촉매)에 들어있는
소스들 + 아직 안 쓰는 소스(외국인·기관 수급, 섹터자금흐름/종목모멘텀)가
실제로 향후 수익률과 상관관계가 있는지 1차 스크리닝한다.

[방법]
소스별 "신호 발생 (날짜, 종목)" 집합을 만들고, 그 날짜의 전체 유니버스
평균 향후 N거래일 수익률(baseline) 대비 초과수익률(alpha)과 승률을 비교.
alpha는 날짜별로 매칭해서 계산하므로 그 기간의 전반적 강세/약세장 편향은
상쇄된다 — 단, 승률 자체는 baseline 편향을 그대로 반영하니 alpha와 같이
봐야 함(예: 전체 약세장이면 알파가 양수여도 개별 승률은 50% 밑일 수 있음).

[대상 소스]
1. 외국인+기관 쌍끌이 순매수 (kr_stock_daily_data)
2. 뉴스감정 긍정 기사 → 테마 매핑 종목 (news_sentiment.db, 테마 단위라 노이즈 있음)
3. 텔레그램 언급 종목 (telegram_events.db, tele_swing_analyzer._get_tele_stocks와 동일 매칭 로직)
4. 종목모멘텀 accel 상위 N% (sector_monitor.db, 키움 실시간 데이터)

[한계]
- 소스마다 데이터 시작일이 다름(수급 04-27~, 섹터 05-20~, 뉴스 05-28~,
  텔레 06-25~) — 소스 간 비교는 방향성 참고용이지 완전히 동일 기간 비교는 아님.
- 뉴스감정은 종목이 아니라 테마 단위 라벨이라 종목 특정 시그널보다 약함.
- 거래비용/슬리피지 미반영, 단순 종가 기준 스크리닝.

[사용법]
  python3 factor_screen.py
  python3 factor_screen.py --fwd-days 10 --min-trading-value 20
"""
import sqlite3
import re
import os
import argparse
from collections import defaultdict

BASE      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIN_DB    = os.path.join(BASE, "lina_bot", "kr_theme_finance.db")
TELE_DB   = os.path.join(BASE, "intelligence", "telegram_events.db")
NEWS_DB   = os.path.join(BASE, "intelligence", "news_sentiment.db")
SECTOR_DB = os.path.join(BASE, "intelligence", "sector_monitor.db")

ALLOWED_TELE_CHANNELS = ("AllStockNews", "FastStockNews", "darthacking", "-1001208429502")


def load_price_series(min_date: str):
    conn = sqlite3.connect(FIN_DB)
    rows = conn.execute("""
        SELECT stock_name, date, close_price, volume FROM kr_stock_daily_data
        WHERE close_price > 0 AND date >= ?
        ORDER BY stock_name, date
    """, (min_date,)).fetchall()
    theme_rows = conn.execute("SELECT theme_name, stock_name FROM kr_theme_stocks").fetchall()
    conn.close()

    price_series = defaultdict(list)
    for name, date, close, vol in rows:
        price_series[name].append((date, close, vol or 0))
    date_index = {name: {d: i for i, (d, c, v) in enumerate(series)}
                  for name, series in price_series.items()}
    return price_series, date_index, theme_rows


def make_forward_return(price_series, date_index, fwd_days):
    def forward_return(stock_name, date):
        series = price_series.get(stock_name)
        if not series:
            return None
        idx = date_index[stock_name].get(date)
        if idx is None or idx + fwd_days >= len(series):
            return None
        entry = series[idx][1]
        if entry <= 0:
            return None
        return (series[idx + fwd_days][1] - entry) / entry * 100
    return forward_return


def build_baseline(price_series, date_index, forward_return, start_date, min_trading_value_eok):
    all_dates = sorted(set(d for series in price_series.values() for d, c, v in series))
    usable_dates = [d for d in all_dates if d >= start_date]
    baseline = {}
    for date in usable_dates:
        rets = []
        for name, series in price_series.items():
            idx = date_index[name].get(date)
            if idx is None:
                continue
            close, vol = series[idx][1], series[idx][2]
            tv = (close * vol) / 1e8 if vol else 0
            if tv < min_trading_value_eok:
                continue
            r = forward_return(name, date)
            if r is not None:
                rets.append(r)
        if rets:
            baseline[date] = sum(rets) / len(rets)
    return baseline


def eval_signals(signals, label, forward_return, baseline, fwd_days):
    excess, wins, total = [], 0, 0
    for date, name in signals:
        r = forward_return(name, date)
        if r is None:
            continue
        base_ret = baseline.get(date)
        if base_ret is None:
            continue
        excess.append(r - base_ret)
        wins += (r > 0)
        total += 1
    if total == 0:
        print(f"\n[{label}] 유효 신호 0건 — 검증 불가")
        return
    avg_excess = sum(excess) / total
    win_rate = wins / total * 100
    print(f"\n[{label}] 유효신호 {total}건")
    print(f"  향후{fwd_days}거래일 초과수익률(alpha, vs 유니버스평균): {avg_excess:+.2f}%p")
    print(f"  승률(수익>0): {win_rate:.1f}%")


def main():
    parser = argparse.ArgumentParser(description="신호소스별 팩터 스크리닝")
    parser.add_argument("--fwd-days", type=int, default=5, help="향후 며칠(거래일) 수익률로 평가할지")
    parser.add_argument("--min-trading-value", type=float, default=10, help="최소 거래대금(억원, 잡주 배제)")
    parser.add_argument("--signal-start", default="2026-04-27", help="신호/baseline 평가 시작일")
    parser.add_argument("--accel-pct", type=float, default=0.05, help="종목모멘텀 상위 몇 %%를 신호로 볼지")
    args = parser.parse_args()

    print("가격 데이터 로딩 중...")
    price_series, date_index, theme_rows = load_price_series("2026-04-20")
    forward_return = make_forward_return(price_series, date_index, args.fwd_days)

    print("baseline 계산 중...")
    baseline = build_baseline(price_series, date_index, forward_return,
                               args.signal_start, args.min_trading_value)

    # ── 1. 외국인+기관 쌍끌이 매수 ──────────────────────────
    print("\n" + "=" * 60)
    print("1) 외국인+기관 쌍끌이 순매수")
    print("=" * 60)
    conn = sqlite3.connect(FIN_DB)
    rows = conn.execute("""
        SELECT date, stock_name, close_price, volume FROM kr_stock_daily_data
        WHERE date >= ? AND foreign_net_buy > 0 AND institution_net_buy > 0
    """, (args.signal_start,)).fetchall()
    conn.close()
    signals = []
    for date, name, close, vol in rows:
        tv = (close * vol) / 1e8 if vol else 0
        if tv >= args.min_trading_value:
            signals.append((date, name))
    print(f"신호 발생 (date,종목) 쌍: {len(signals)}건")
    eval_signals(signals, "쌍끌이매수", forward_return, baseline, args.fwd_days)

    # ── 2. 뉴스 감정 (테마 단위) ─────────────────────────────
    print("\n" + "=" * 60)
    print("2) 뉴스감정 (긍정 기사 → 테마 매핑 종목)")
    print("=" * 60)
    theme_to_stocks = defaultdict(set)
    for theme_name, stock_name in theme_rows:
        pure = re.sub(r'\s*(KOSPI|KOSDAQ)\s*\d{6}$', '', stock_name).strip()
        if pure:
            theme_to_stocks[theme_name].add(pure)

    conn = sqlite3.connect(NEWS_DB)
    news_rows = conn.execute("""
        SELECT date, keyword FROM news_sentiment
        WHERE sentiment = '긍정' AND date >= ?
    """, (args.signal_start.replace("-", ""),)).fetchall()
    conn.close()

    signals = set()
    for date_raw, keyword in news_rows:
        date = f"{date_raw[:4]}-{date_raw[4:6]}-{date_raw[6:8]}"
        for theme_name, stocks in theme_to_stocks.items():
            if keyword in theme_name or theme_name in keyword:
                for s in stocks:
                    signals.add((date, s))
    print(f"신호 발생 (date,종목) 쌍(중복제거): {len(signals)}건")
    eval_signals(signals, "뉴스감정(긍정)", forward_return, baseline, args.fwd_days)

    # ── 3. 텔레그램 언급 (일자별 재구성) ─────────────────────
    print("\n" + "=" * 60)
    print("3) 텔레그램 언급 종목 (일자별)")
    print("=" * 60)
    all_stock_names = set()
    for name in price_series.keys():
        pure = re.sub(r'\s*(KOSPI|KOSDAQ)\s*\d{6}$', '', name).strip()
        if pure and len(pure) >= 2:
            all_stock_names.add(pure)

    conn = sqlite3.connect(TELE_DB)
    placeholders = ",".join("?" * len(ALLOWED_TELE_CHANNELS))
    tele_rows = conn.execute(f"""
        SELECT message, created_at FROM telegram_events
        WHERE channel IN ({placeholders}) AND created_at >= ?
    """, (*ALLOWED_TELE_CHANNELS, args.signal_start)).fetchall()
    conn.close()

    msgs_by_date = defaultdict(list)
    for message, created_at in tele_rows:
        msgs_by_date[created_at[:10]].append(message or "")

    signals = set()
    for date, msgs in msgs_by_date.items():
        combined = " ".join(msgs)
        for pure in all_stock_names:
            if pure in combined:
                signals.add((date, pure))
    print(f"신호 발생 (date,종목) 쌍: {len(signals)}건")
    eval_signals(signals, "텔레그램언급", forward_return, baseline, args.fwd_days)

    # ── 4. 섹터자금흐름/종목모멘텀 (accel 상위 N%) ───────────
    print("\n" + "=" * 60)
    print(f"4) 종목모멘텀 (accel 급등, 일자별 최대값, 상위{args.accel_pct*100:.0f}%)")
    print("=" * 60)
    code_to_name = {}
    for theme_name, stock_name in theme_rows:
        m = re.search(r'(\d{6})', stock_name)
        if m:
            pure = re.sub(r'\s*(KOSPI|KOSDAQ)\s*\d{6}$', '', stock_name).strip()
            code_to_name[m.group(1)] = pure

    conn = sqlite3.connect(SECTOR_DB)
    mom_rows = conn.execute("""
        SELECT ts, code, accel FROM stock_momentum WHERE ts >= ?
    """, (args.signal_start,)).fetchall()
    conn.close()

    best_by_daycode = {}
    for ts, code, accel in mom_rows:
        if accel is None:
            continue
        key = (ts[:10], code)
        if key not in best_by_daycode or accel > best_by_daycode[key]:
            best_by_daycode[key] = accel

    accels = sorted(best_by_daycode.values(), reverse=True)
    cutoff = accels[max(0, int(len(accels) * args.accel_pct) - 1)] if accels else 0

    signals = set()
    for (date, code), accel in best_by_daycode.items():
        if accel >= cutoff and accel > 0:
            name = code_to_name.get(code)
            if name:
                signals.add((date, name))
    print(f"컷오프: {cutoff:.4f} / 신호 발생 (date,종목) 쌍: {len(signals)}건")
    eval_signals(signals, f"종목모멘텀(accel상위{args.accel_pct*100:.0f}%)",
                 forward_return, baseline, args.fwd_days)

    print("\n" + "=" * 60)
    print("완료")


if __name__ == "__main__":
    main()
