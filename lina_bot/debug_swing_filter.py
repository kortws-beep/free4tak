"""
swing_analyzer.get_swing_data() 필터 단계별 통과 종목수를 yfinance import 전/후로 비교
"""
import sys
sys.path.insert(0, '.')

def run_test(label):
    import sqlite3, os, re
    from swing_analyzer import DB_PATH, _ma, _atr, MA20_BAND, VCP_RATIO, VOL_DRY_RATIO, MIN_TRADING_VALUE_EOK, SMART_DAYS, SMART_MIN_DAYS, ATR_PERIOD, ATR_STOP_MULT, ATR_TARGET_MULT

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT stock_name FROM kr_stock_daily_data")
    all_stocks = [r[0] for r in cursor.fetchall()]

    counters = {"total": 0, "rows_ok": 0, "closes_ok": 0, "etf_pass": 0,
                "ma200_pass": 0, "ma20band_pass": 0, "vcp_pass": 0,
                "voldry_pass": 0, "tradingvalue_pass": 0, "smart_pass": 0, "rr_pass": 0}

    etf_kw = ["KODEX","TIGER","KBSTAR","ARIRANG","HANARO","KOSEF","TREX",
              "SOL","ACE","PLUS","RISE","KIWOOM","SMART","FOCUS",
              "인버스","레버리지","ETN","ETF"]

    for stock_name in all_stocks:
        counters["total"] += 1
        cursor.execute("""
            SELECT date, close_price, volume, foreign_net_buy, institution_net_buy
            FROM kr_stock_daily_data WHERE stock_name = ?
            ORDER BY date DESC LIMIT 220
        """, (stock_name,))
        rows = cursor.fetchall()
        if len(rows) < 30: continue
        counters["rows_ok"] += 1

        closes  = [r[1] if r[1] else 0 for r in rows]
        volumes = [r[2] if r[2] else 0 for r in rows]
        valid_closes = [c for c in closes if c > 0]
        if len(valid_closes) < 30: continue
        counters["closes_ok"] += 1

        curr_price = valid_closes[0]
        pure_name  = re.sub(r"\s*(KOSPI|KOSDAQ)\s*\d{6}$", "", stock_name).strip()
        if any(k in pure_name for k in etf_kw): continue
        if pure_name.endswith("우") or pure_name.endswith("우B"): continue
        counters["etf_pass"] += 1

        ma200 = _ma(valid_closes, 200)
        if ma200 == 0 or curr_price < ma200: continue
        counters["ma200_pass"] += 1

        ma20 = _ma(valid_closes, 20)
        if ma20 == 0: continue
        dist_ma20 = abs(curr_price - ma20) / ma20
        if dist_ma20 > MA20_BAND: continue
        counters["ma20band_pass"] += 1

        if len(valid_closes) < 30: continue
        recent_amp = (max(valid_closes[0:15]) - min(valid_closes[0:15])) / min(valid_closes[0:15]) if min(valid_closes[0:15]) > 0 else 0
        prev_amp   = (max(valid_closes[15:30]) - min(valid_closes[15:30])) / min(valid_closes[15:30]) if min(valid_closes[15:30]) > 0 else 0
        if prev_amp == 0 or recent_amp >= prev_amp * VCP_RATIO: continue
        counters["vcp_pass"] += 1

        valid_volumes = [v for v in volumes if v > 0]
        if len(valid_volumes) < 10: continue
        vol_avg_all    = sum(valid_volumes) / len(valid_volumes)
        vol_avg_recent = sum(valid_volumes[:5]) / 5
        if vol_avg_all == 0 or vol_avg_recent >= vol_avg_all * VOL_DRY_RATIO: continue
        counters["voldry_pass"] += 1

        recent_trading_value = (curr_price * vol_avg_recent) / 100_000_000
        if recent_trading_value < MIN_TRADING_VALUE_EOK: continue
        counters["tradingvalue_pass"] += 1

        f_net_raw  = [r[3] for r in rows]
        i_net_raw  = [r[4] for r in rows]
        supply_len = max(sum(1 for v in f_net_raw if v is not None),
                         sum(1 for v in i_net_raw if v is not None))
        f_net = [v if v is not None else 0 for v in f_net_raw]
        i_net = [v if v is not None else 0 for v in i_net_raw]
        if supply_len == 0:
            smart_ok = True
        else:
            sw         = min(SMART_DAYS, supply_len)
            f_pos_days = sum(1 for v in f_net[:sw] if v > 0)
            i_pos_days = sum(1 for v in i_net[:sw] if v > 0)
            f_cum      = sum(f_net[:sw])
            i_cum      = sum(i_net[:sw])
            adj_min    = max(2, int(SMART_MIN_DAYS * supply_len / SMART_DAYS))
            smart_ok   = (f_pos_days >= adj_min or i_pos_days >= adj_min or
                          (f_cum > 0 and i_cum > 0) or (f_cum > 0 or i_cum > 0))
        if not smart_ok: continue
        counters["smart_pass"] += 1

        atr        = _atr(valid_closes, ATR_PERIOD)
        stop_price = round(curr_price - atr * ATR_STOP_MULT, 0)
        tgt_price  = round(curr_price + atr * ATR_TARGET_MULT, 0)
        stop_pct   = round((curr_price - stop_price) / curr_price * 100, 1)
        tgt_pct    = round((tgt_price  - curr_price) / curr_price * 100, 1)
        rr_ratio   = round(tgt_pct / stop_pct, 1) if stop_pct > 0 else 0
        if rr_ratio < 1.5: continue
        counters["rr_pass"] += 1

    conn.close()
    print(f"\n=== {label} ===")
    for k, v in counters.items():
        print(f"  {k}: {v}")

run_test("BEFORE yfinance import")

import yfinance as yf

run_test("AFTER yfinance import")
