"""
sideways_pullback.py — 횡보장 엔진 후보 ① '당일 눌림목 반등' 분봉 검증 (2026-11-05, 대장 "선거 끝나면 횡보장 올 수 있으니 엔진 먼저 완성")
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
제미나이 제안 + 대장 힌트("횡보장엔 개별 특징주 장세"): 아침에 거래대금 실어 강하게 오른 종목이, 10:30 뒤
거래를 죽이며(세력 이탈 아닌 단순 조정) 아침 고점 대비 -D%까지 눌렸을 때 매수.
  날짜: 2026-07-01 ~ 2026-10-31, 그날 국면(전날 20일선 위 비율, market_regime 재계산)으로 <35 / 35~50(횡보) / 50+ 나눠 봄
  후보: 일봉 고가 전일 대비 +7%↑ & 거래대금 300억↑ → 분봉(거래량 포함, backtest/data/minute_vol.db)으로
        09:00~10:30 아침 고점이 전일 대비 +7%↑인 종목만
  진입(10:30~14:30 매분 첫 시점):
    눌림 D%      — 가격 ≤ 아침 고점 × (1-D)   (D = 5, 7)
    거래 죽음    — 고점 이후 분당 평균 거래대금 < 상승 구간(시가→고점) 분당 평균 × 0.5
    +VWAP        — 추가로 그 시각 VWAP(시가부터 거래량 가중 평균가) ±1.5% 안
    →반등1%      — 눌림에 닿은 뒤 바로 사지 않고, 그 뒤 저가 대비 +1% 반등 확인하고(고점 -D%+2% 안쪽) 매수(11-05 1차 결과: 떨어지는 칼날 잡기)
  매도 규칙(수수료·세금 0.2%, 매수 미끄러짐 0.1%):
    daybot현행   — 손절 -3.5% · +5%에 30% → 나머지 고점 -2% 트레일링 · 3거래일 종가
    짧게+3       — +3% 전량 · 손절 -3.5% · 당일 15:15 정리
    50%선익절    — +2.5%에 50% → 나머지 고점 -1.5% 트레일링(본전 보호) · 손절 -3.5% · 3거래일 종가
  표마다 건수·승률·평균·앞/뒤 절반(둘 다 +여야 믿을 만함). '하루 1종목'(그날 가장 먼저 신호) 줄도 같이.
실행: venv/bin/python backtest/sideways_pullback.py
"""
import os
import sys
import time
import sqlite3
import datetime
import collections

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(BASE, "core"), os.path.join(BASE, "backtest")]
import market_regime                   # noqa: E402
import dip_leader                      # noqa: E402

SINCE, UNTIL = "2026-07-01", "2026-10-31"
VOL_DB = os.path.join(BASE, "backtest", "data", "minute_vol.db")
COST, SLIP = 0.002, 0.001
MORNING_END, ENTRY_FROM, ENTRY_TO = "103000", "103000", "143000"
MORNING_UP = 7.0
DROPS = (5.0, 7.0)
VOL_RATIO = 0.5
VWAP_BAND = 1.5


class VolStore:
    """1분봉(가격·고가·저가·거래량) 캐시 — 기존 minute_cache.db엔 거래량이 없어서 따로."""
    def __init__(self, api, path=VOL_DB, pause=0.06):
        self.api, self.pause = api, pause
        self.conn = sqlite3.connect(path)
        self.conn.execute("CREATE TABLE IF NOT EXISTS bars (code TEXT, date TEXT, time TEXT, price REAL, high REAL, "
                          "low REAL, vol REAL, PRIMARY KEY(code, date, time))")
        self.conn.execute("CREATE TABLE IF NOT EXISTS done (code TEXT, date TEXT, PRIMARY KEY(code, date))")
        self.calls = 0

    def day(self, code, date):
        if not self.conn.execute("SELECT 1 FROM done WHERE code=? AND date=?", (code, date)).fetchone():
            self._fetch(code, date)
        return self.conn.execute("SELECT time, price, high, low, vol FROM bars WHERE code=? AND date=? ORDER BY time",
                                 (code, date)).fetchall()

    def _fetch(self, code, date):
        ymd, t, rows = date.replace("-", ""), "153000", {}
        for _ in range(6):
            try:
                got = [b for b in self.api.get_minute_bars_by_date(code, ymd, t) if b["date"] == ymd]
            except Exception:
                got = []
            self.calls += 1
            time.sleep(self.pause)
            new = [b for b in got if b["time"] not in rows]
            if not new:
                break
            for b in new:
                if "090000" <= b["time"] <= "153000":
                    rows[b["time"]] = (b["price"], b["high"], b["low"], b.get("volume", 0))
            oldest = min(b["time"] for b in got)
            if oldest <= "090000":
                break
            m = int(oldest[:2]) * 60 + int(oldest[2:4]) - 1
            t = f"{m // 60:02d}{m % 60:02d}00"
        self.conn.executemany("INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?)",
                              [(code, date, tm, *v) for tm, v in rows.items()])
        self.conn.execute("INSERT OR REPLACE INTO done VALUES (?,?)", (code, date))
        self.conn.commit()

    def days_from(self, code, date, n):
        out, d, end = [], datetime.date.fromisoformat(date), datetime.date.fromisoformat(UNTIL) + datetime.timedelta(days=10)
        while len(out) < n and d <= end:
            if d.weekday() < 5:
                b = self.day(code, d.isoformat())
                if b:
                    out.append((d.isoformat(), b))
            d += datetime.timedelta(days=1)
        return out


def candidates():
    """{date: [(code, name, prev_close)]} — 일봉으로 먼저 추림."""
    c = sqlite3.connect(dip_leader.DAILY_DB)
    codes = {n: k for k, n in c.execute("SELECT code, name FROM kr_stock_master")}
    q = """WITH d AS (SELECT stock_name, date, high_price h, trade_value v,
                      LAG(close_price) OVER (PARTITION BY stock_name ORDER BY date) p
               FROM kr_stock_daily_data WHERE date >= date(?, '-15 day') AND date <= ?)
           SELECT date, stock_name, p FROM d WHERE date >= ? AND p > 0 AND h >= p * 1.07 AND v >= 3e10"""
    out = collections.defaultdict(list)
    for d, n, p in c.execute(q, (SINCE, UNTIL, SINCE)):
        if n in codes:
            out[d].append((codes[n], n, p))
    c.close()
    return out


def signals(bars, prev):
    """→ {변형: (시각, 가격)} 첫 신호."""
    morning = [b for b in bars if b[0] <= MORNING_END]
    if not morning:
        return {}
    i_hi = max(range(len(morning)), key=lambda i: morning[i][2])
    mh, t_hi = morning[i_hi][2], morning[i_hi][0]
    if mh < prev * (1 + MORNING_UP / 100):
        return {}
    rise = [b[1] * b[4] for b in morning[:i_hi + 1]]
    rise_avg = sum(rise) / len(rise) if rise else 0
    out, cum_pv, cum_v, post = {}, 0.0, 0.0, []
    touched, low_after = {}, {}                # 눌림 D%에 (거래 죽은 채) 닿은 뒤 저가 → 그 저가 대비 +1% 반등 확인
    for tm, p, h, l, v in bars:
        cum_pv += p * v
        cum_v += v
        if tm > t_hi:
            post.append(p * v)
        if not (ENTRY_FROM <= tm <= ENTRY_TO) or not post or rise_avg <= 0:
            continue
        quiet = sum(post) / len(post) < rise_avg * VOL_RATIO
        vwap = cum_pv / cum_v if cum_v else p
        for d in DROPS:
            if p <= mh * (1 - d / 100) and quiet:
                out.setdefault(f"눌림{d:g}%", (tm, p))
                if abs(p / vwap - 1) * 100 <= VWAP_BAND:
                    out.setdefault(f"눌림{d:g}%+VWAP", (tm, p))
                touched[d] = True
            if touched.get(d):
                low_after[d] = min(low_after.get(d, l), l)
                if p >= low_after[d] * 1.01 and p <= mh * (1 - d / 100 + 0.02):
                    out.setdefault(f"눌림{d:g}%→반등1%", (tm, p))
    return out


def sim(store, code, date, t, price, rule):
    entry = price * (1 + SLIP)
    hold = 1 if rule == "짧게+3" else 3
    ds = store.days_from(code, date, hold)
    part, peak, booked, rest = False, entry, 0.0, 1.0
    for i, (d, bars) in enumerate(ds):
        for tm, p, h, l, v in bars:
            if i == 0 and tm <= t:
                continue
            if rule == "짧게+3" and tm >= "151500":
                return (p / entry - 1) * 100 - COST * 100
            if not part and l <= entry * 0.965:
                px = min(h, entry * 0.965)
                return (px / entry - 1) * 100 - COST * 100
            if rule == "짧게+3":
                if h >= entry * 1.03:
                    return 3.0 - COST * 100
                continue
            tp, ratio, trail = (5.0, 0.3, 2.0) if rule == "daybot현행" else (2.5, 0.5, 1.5)
            if not part:
                if h >= entry * (1 + tp / 100):
                    part, booked, rest, peak = True, tp * ratio, 1 - ratio, h
                continue
            peak = max(peak, h)
            line = peak * (1 - trail / 100)
            if rule == "50%선익절":
                line = max(line, entry)
            if l <= line:
                return booked + (min(h, line) / entry - 1) * 100 * rest - COST * 100
    if not ds:
        return None
    last = ds[-1][1][-1][1]
    return booked + (last / entry - 1) * 100 * rest - COST * 100


def bucket(b):
    return "하락<35" if b < 35 else "횡보35~50" if b < 50 else "상승50+"


def line(label, xs):
    if not xs:
        return f"    {label:26s}   0건"
    n, h = len(xs), len(xs) // 2
    a = lambda v: sum(v) / len(v) if v else 0  # noqa: E731
    return (f"    {label:26s} {n:4d}건 승 {sum(x > 0 for x in xs) / n * 100:3.0f}% 평균 {a(xs):+.2f}% "
            f"앞 {a(xs[:h]):+.2f}% / 뒤 {a(xs[h:]):+.2f}%")


def main():
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE, ".env"))
    from kis_api import KisAPI
    store = VolStore(KisAPI())
    c = sqlite3.connect(dip_leader.DAILY_DB)
    ds = [d for (d,) in c.execute("SELECT DISTINCT date FROM kr_stock_daily_data WHERE date >= '2026-05-01' ORDER BY date")]
    c.close()
    br = {d: v[0] for d, v in market_regime.compute(dates=ds).items()}
    bd = sorted(br)
    cand = candidates()
    res = collections.defaultdict(list)          # (국면, 변형, 규칙, 전체/하루1) → [수익률]
    for date in sorted(cand):
        prior = [d for d in bd if d < date]
        if not prior:
            continue
        g = bucket(br[prior[-1]])
        first = {}                                # 변형 → (시각, {규칙: 수익률}) 그날 가장 먼저 신호 난 종목
        for code, name, prev in cand[date]:
            bars = store.day(code, date)
            if not bars:
                continue
            for var, (t, p) in signals(bars, prev).items():
                rs = {}
                for rule in ("daybot현행", "짧게+3", "50%선익절"):
                    r = sim(store, code, date, t, p, rule)
                    if r is not None:
                        rs[rule] = r
                        res[(g, var, rule, "전체")].append(r)
                if rs and (var not in first or t < first[var][0]):
                    first[var] = (t, rs)
        for var, (t, rs) in first.items():
            for rule, r in rs.items():
                res[(g, var, rule, "하루1")].append(r)
        print(f"{date} {g} 후보 {len(cand[date])} · API {store.calls}", flush=True)
    for g in ("횡보35~50", "상승50+", "하락<35"):
        print(f"\n[{g}]")
        for var in sorted({k[1] for k in res}):
            for scope in ("전체", "하루1"):
                for rule in ("daybot현행", "짧게+3", "50%선익절"):
                    print(line(f"{var} {scope} {rule}", res.get((g, var, rule, scope), [])))


if __name__ == "__main__":
    sys.exit(main())
