"""
premarket_sector_review.py — 프리장 섹터 → 본장 1~3위 하루 대조 (2026-10-26, 대장 아이디어)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
대장: "프리장(08~09시)에서 종목이 아니라 섹터를 잡고, 본장에서 그 섹터 1~3위를 daybot 후보로.
       단, 프리장 주가가 본장 시작하고도 ±3% 안이어야 '가는 섹터' — 프리장에서만 뜨고 본장에 내려앉는 놈이 있다."
→ 그날 장 끝나고 돌리면:
  ① 프리장 마지막 기록(08:55)의 상위 테마와 그 테마 NXT 1~3위 종목
  ② 그 종목들이 본장 09:05에 프리장 가격 대비 어디 있나: 유지(±3%) · 더 감(+3%↑) · 내려앉음(-3%↓) → 테마 판정(평균)
  ③ 본장(KRX) 그 테마 전 종목 중 09:10 등락 1~3위(daybot 범위 +3~15%)를 09:10 가격에 샀다 치면
     +2.5% / -3.5% 어디 먼저 닿나 · 이후 최고 · 15:20
  ④ 본장 09:15/09:30 테마 순위(theme_open_obs)에 그 테마가 있었나
매매·봇 설정은 건드리지 않음. 같은 날(장 끝난 뒤) 돌리는 용도 — 분봉은 한투 일별 분봉이라 지난 날짜도 됨.
실행: venv/bin/python backtest/premarket_sector_review.py [YYYY-MM-DD] [테마수=5]
"""
import os
import sys
import sqlite3
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(BASE, "core"), os.path.join(BASE, "intelligence")]
LOG_DB = os.path.join(BASE, "lina_bot", "three_month_leader_log.db")
HOLD_BAND, UP, DN = 3.0, 2.5, 3.5
PICK_MIN, PICK_MAX, PICKS = 3.0, 15.0, 3
CHECK_AT, ENTRY_AT, END_AT = "090500", "091000", "152000"


def hold_state(pre_price: float, reg_price: float, band: float = HOLD_BAND):
    """프리장 가격 대비 본장 가격 % → (변화%, '유지'|'더 감'|'내려앉음')."""
    if not pre_price or not reg_price:
        return None, "모름"
    d = (reg_price / pre_price - 1) * 100
    return d, ("내려앉음" if d < -band else "더 감" if d > band else "유지")


def first_touch(entry: float, bars: list, up: float = UP, dn: float = DN) -> str:
    """bars(시간순) 중 +up% 고가 / -dn% 저가 어느 쪽이 먼저 — 'win'|'loss'|'none'."""
    for b in bars:
        if b["low"] <= entry * (1 - dn / 100):
            return "loss"
        if b["high"] >= entry * (1 + up / 100):
            return "win"
    return "none"


def price_at(bars: list, hhmmss: str):
    """hhmmss 이하 마지막 분봉 종가(bars 시간순)."""
    p = None
    for b in bars:
        if b["time"] <= hhmmss:
            p = b["price"]
    return p


def day_bars(api, code: str, ymd: str, upto: str = END_AT) -> list:
    got = {}
    for hh in ("091000", "111000", "131000", "152000"):
        for b in api.get_minute_bars_by_date(code, ymd, hh) or []:
            got[b["time"]] = b
        time.sleep(0.06)
        if hh >= upto:
            break
    return [got[k] for k in sorted(got) if "090000" <= k <= upto]


def prev_close(api, code: str, date: str):
    rows = api.get_daily_ohlc(code, 3, end_date=date.replace("-", "")) or []
    rows = [r for r in rows if r.get("date") and r["date"] < date]
    return rows[0]["close"] if rows else None


def review(api, date: str, n_themes: int = 5, log_db: str = LOG_DB) -> list:
    import theme_open_obs as T
    conn = sqlite3.connect(log_db)
    pre_t = conn.execute("SELECT MAX(time) FROM theme_pre_top WHERE date=? AND time<'0900'", (date,)).fetchone()[0]
    if not pre_t:
        conn.close()
        return []
    rows = conn.execute("SELECT theme_rank, theme, stock_rank, code, name, chg, price FROM theme_pre_top "
                        "WHERE date=? AND time=? AND theme_rank<=? ORDER BY theme_rank, stock_rank",
                        (date, pre_t, n_themes)).fetchall()
    reg = {}
    for t, rk, th in conn.execute("SELECT time, rank, theme FROM theme_open_obs WHERE date=? AND time>='0900' AND rank<=5",
                                  (date,)):
        reg.setdefault(th, []).append(f"{t[:2]}:{t[2:]} {rk}위")
    conn.close()
    themes = T.load_themes()
    ymd = date.replace("-", "")
    out, bars_cache, prev_cache = [], {}, {}

    def bars(code, upto=END_AT):
        if code not in bars_cache:
            bars_cache[code] = day_bars(api, code, ymd, upto)
        return bars_cache[code]

    def prev(code):
        if code not in prev_cache:
            prev_cache[code] = prev_close(api, code, date)
        return prev_cache[code]

    by_theme = {}
    for tr, th, sr, code, name, chg, price in rows:
        by_theme.setdefault((tr, th), []).append((code, name, chg, price))
    for (tr, th), pres in by_theme.items():
        checks = []
        for code, name, chg, price in pres:
            d, st = hold_state(price, price_at(bars(code), CHECK_AT))
            checks.append((name, chg, d, st))
        ds = [d for _, _, d, _ in checks if d is not None]
        avg = sum(ds) / len(ds) if ds else None
        verdict = hold_state(100, 100 + avg)[1] if avg is not None else "모름"
        # 본장 그 테마 전 종목 09:10 등락
        ranked = []
        for code, name in themes.get(th, []):
            pc = prev(code)
            p = price_at(bars(code), ENTRY_AT) if pc else None
            if pc and p:
                ranked.append(((p / pc - 1) * 100, code, name, p))
        ranked.sort(reverse=True)
        picks = []
        for chg, code, name, p in [r for r in ranked if PICK_MIN <= r[0] <= PICK_MAX][:PICKS]:
            after = [b for b in bars(code) if b["time"] > ENTRY_AT]
            if not after:
                continue
            picks.append({"name": name, "chg": chg, "res": first_touch(p, after),
                          "max": max(b["high"] for b in after) / p * 100 - 100,
                          "close": after[-1]["price"] / p * 100 - 100})
        out.append({"rank": tr, "theme": th, "checks": checks, "avg": avg, "verdict": verdict,
                    "top": [(n, c) for c, _, n, _ in ranked[:3]], "picks": picks, "reg": reg.get(th, [])})
    return out


def main():
    import datetime
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE, ".env"))
    from kis_api import KisAPI
    date = next((a for a in sys.argv[1:] if "-" in a), datetime.date.today().isoformat())
    n = next((int(a) for a in sys.argv[1:] if a.isdigit()), 5)
    res = review(KisAPI(), date, n)
    if not res:
        print(f"{date} 프리장 기록(theme_pre_top) 없음")
        return
    print(f"🌅 {date} 프리장 섹터 → 본장 대조 (프리장 마지막 기록 기준, ±{HOLD_BAND:g}% 유지 판정)")
    tot = {}
    for r in res:
        print(f"\n{r['rank']}. {r['theme']} — 본장 09:05 평균 {r['avg']:+.1f}% → **{r['verdict']}**"
              if r["avg"] is not None else f"\n{r['rank']}. {r['theme']} — 본장 가격 못 받음")
        print("   프리장 1~3위: " + " · ".join(f"{n} {c:+.1f}%→{st}({d:+.1f})" if d is not None else f"{n} {c:+.1f}%→?"
                                         for n, c, d, st in r["checks"]))
        print("   본장 09:10 1~3위: " + " · ".join(f"{n} {c:+.1f}%" for n, c in r["top"])
              + f"   | 본장 테마순위: {', '.join(r['reg']) or '5위 밖'}")
        for p in r["picks"]:
            print(f"     ▸ {p['name']} {p['chg']:+.1f}%에 매수 → {p['res']} · 이후최고 {p['max']:+.1f}% · 15:20 {p['close']:+.1f}%")
            tot.setdefault(r["verdict"], []).append(p["res"])
    print("\n── 판정별 매수 가정 결과 (+2.5% 먼저 / 전체)")
    for v, xs in tot.items():
        print(f"   {v}: {sum(x == 'win' for x in xs)}/{len(xs)} · -3.5% 먼저 {sum(x == 'loss' for x in xs)}")


if __name__ == "__main__":
    main()
