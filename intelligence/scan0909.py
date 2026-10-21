"""
scan0909.py — "090930타점 시가이탈 오전중저가이탈" 키움 조건식의 파이썬 구현 (2026-10-22)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
대장(10-22): daybot 소스를 3개월수급(후보 0건) → 090930으로 바꾸면서 조건식 원문을 줌.
키움 원본 (!A and B and C and D and E and F and G):
  A 당일 첫 1분봉 등락률(첫분봉 시가 대비 종가) 0% 이상  → 앞에 '!'이라 "첫 1분봉이 음봉"이어야 통과(시가이탈)
  B [일] 오늘 시가를 현재가가 상향돌파                    → 키움 '돌파'는 봉 기준: 어제 봉은 종가 ≤ 시가(음봉),
                                                             오늘은 현재가 > 시가(양봉 전환). ★ 10-22 대장이 장 전에 본 키움
                                                             결과(알멕·두산테스나·우리로 = 10-20 음봉 → 10-21 양봉)로 확인
  C [일] 5일 이평 >= 20일 이평(오늘 봉 기준)
  D [1분] 거래대금 1억 이상(지금 1분봉)
  E [1분] 지금 1분봉 거래량 >= 직전 60개 1분봉 평균의 100%
  F 체결강도 100~1000%
  G 시가총액 700억 이상
계산(한투 API, 리나 단타000 1분 감시에 붙어서 같은 시세를 재사용 — 추가 복수시세 조회 없음):
  1단계 — 복수시세 + 일봉 DB로 B(어제 음봉 & 지금가 > 시가)·C(이평, 오늘은 지금가를 종가로)·대상제외
  2단계 — 상장주식수로 G → 체결 1번으로 F
  3단계 — 1분봉으로 A(오늘 첫 봉 종가 < 시가)·D·E. 아침엔 오늘 봉이 60개가 안 돼 전날 마지막 봉으로 채움.
※ 장 전에 키움을 보면 '오늘 첫 분봉'이 아직 없어 A가 거짓 → !A가 참이 돼 어제 기준 종목이 뜸(장중 결과와 다름).
  키움 결과(kiwoom_cond_log)와 대조하며 조정.
"""
import datetime
import sqlite3
import time

import three_month_leader as tml
import leader_scan

KST = datetime.timezone(datetime.timedelta(hours=9))
TAG = "090930타점 시가이탈 오전중저가이탈"
D_VALUE_1M_MIN = 100_000_000          # 1분 거래대금 1억
E_VOL_RATIO_MIN = 100.0               # 지금 1분봉 거래량 / 직전 60봉 평균 (%)
F_STRENGTH_MIN, F_STRENGTH_MAX = 100.0, 1000.0
G_CAP_MIN_EOK = 700
AVG_BARS = 60
NEW_SHARES_PER_SCAN = 40              # 상장주식수 첫 조회는 한 번에 이만큼만(한투 초당 한도 나눠 쓰기)
DEEP_CHECK_MAX = 40                   # 체결·분봉 조회까지 가는 종목 수 상한(거래대금 큰 순)


def ma_ok(closes: list, price: float) -> tuple:
    """C: 오늘(지금가)을 포함한 5일 이평 >= 20일 이평. closes = 오늘 전 종가(오래된→최신). → (통과, ma5, ma20)"""
    s = list(closes[-19:]) + [price]
    if len(s) < 20:
        return False, None, None
    ma5, ma20 = sum(s[-5:]) / 5, sum(s[-20:]) / 20
    return ma5 >= ma20, ma5, ma20


def cross_ok(prev_candle, price: float, day_open: float) -> bool:
    """B: 키움 [일] '시가를 현재가가 상향돌파' — 어제 봉 종가 ≤ 시가, 오늘 현재가 > 시가. prev_candle = (시가, 종가)."""
    return bool(prev_candle) and prev_candle[1] <= prev_candle[0] and price > day_open > 0


def bar_checks(today_bars: list, prev_bars: list, day_open: float) -> dict:
    """A·D·E 판정. bars = [{"time","price","volume","acml_value"}], today_bars는 오늘 정규장 봉(아무 순서)."""
    t = sorted(today_bars, key=lambda b: b["time"])
    if not t:
        return {"fails": ["분봉 없음"]}
    first, cur = t[0], t[-1]
    fails = []
    first_rate = (first["price"] / day_open - 1) * 100 if day_open else 0.0
    if first_rate >= 0:
        fails.append(f"A첫분봉 {first_rate:+.2f}%(음봉 아님)")
    prev_acml = t[-2]["acml_value"] if len(t) >= 2 else 0.0
    value_1m = max(cur["acml_value"] - prev_acml, 0.0) if cur["acml_value"] else cur["price"] * cur["volume"]
    if value_1m < D_VALUE_1M_MIN:
        fails.append(f"D1분대금 {value_1m / 1e8:.1f}억")
    history = (sorted(prev_bars, key=lambda b: (b.get("date", ""), b["time"])) + t[:-1])[-AVG_BARS:]
    avg = sum(b["volume"] for b in history) / len(history) if history else 0.0
    vol_ratio = cur["volume"] / avg * 100 if avg else 0.0
    if vol_ratio < E_VOL_RATIO_MIN:
        fails.append(f"E거래량 {vol_ratio:.0f}%")
    return {"fails": fails, "first_rate": first_rate, "value_1m": value_1m, "vol_ratio": vol_ratio}


class Scan0909:
    """리나 단타000 스캐너(DantaScanner)에 붙어서 같은 풀·시세·상장주식수 캐시를 같이 쓴다."""

    def __init__(self, api, danta):
        self.api, self.danta = api, danta
        self.closes: dict = {}        # 종목명 → 오늘 전 종가 19개 (날짜별 1번)
        self.prev_candle: dict = {}   # 종목명 → 어제 (시가, 종가)
        self.closes_date = ""
        self.prev_date = ""
        self.first: dict = {}         # code → (날짜, 오늘 첫 분봉)
        self.prev_bars: dict = {}     # code → (날짜, 전날 마지막 분봉들)
        self.bars: dict = {}          # code → {time: bar} 오늘

    def _load_closes(self, today: str):
        if self.closes_date == today:
            return
        conn = sqlite3.connect(tml.THEME_DB, timeout=10)
        try:
            dates = [d for (d,) in conn.execute("SELECT DISTINCT date FROM kr_stock_daily_data WHERE date < ? "
                                                "ORDER BY date DESC LIMIT 19", (today,))][::-1]
            rows = conn.execute("SELECT stock_name, date, close_price, open_price FROM kr_stock_daily_data WHERE date >= ? "
                                "AND date < ? AND close_price > 0 ORDER BY date", (dates[0], today)).fetchall() if dates else []
        finally:
            conn.close()
        closes, prev = {}, {}
        for n, d, c, o in rows:
            closes.setdefault(n, []).append(c)
            if d == dates[-1]:
                prev[n] = (o, c)
        self.closes, self.prev_candle, self.closes_date = closes, prev, today
        self.prev_date = dates[-1].replace("-", "") if dates else ""

    def _today_bars(self, code: str, now: datetime.datetime) -> list:
        today = now.strftime("%Y%m%d")
        cache = self.bars.setdefault(code, {})
        if cache and min(b.get("date", today) for b in cache.values()) != today:
            cache.clear()
        t = now.strftime("%H%M%S")
        for _ in range(3 if len(cache) < AVG_BARS else 1):        # 처음엔 3쪽(최대 90개), 이후엔 최근 30개만
            got = [dict(b, date=b.get("date") or today) for b in self.api.get_minute_bars(code, t)]
            got = [b for b in got if "090000" <= b["time"] <= "153000"]
            if not got:
                break
            new = [b for b in got if b["time"] not in cache]
            for b in got:
                cache[b["time"]] = b
            if not new:
                break
            t = min(b["time"] for b in got)
            if t <= "090100":
                break
        fc = self.first.get(code)
        if not fc or fc[0] != today:
            fb = [b for b in self.api.get_minute_bars_by_date(code, today, "090200") if b.get("date", today) == today
                  and "090000" <= b["time"] <= "090200"]
            if fb:
                self.first[code] = (today, min(fb, key=lambda b: b["time"]))
        if code in self.first and self.first[code][0] == today:
            f = self.first[code][1]
            cache.setdefault(f["time"], f)
        return list(cache.values())

    def _prev(self, code: str) -> list:
        pc = self.prev_bars.get(code)
        if pc and pc[0] == self.prev_date:
            return pc[1]
        bars = [b for b in self.api.get_minute_bars_by_date(code, self.prev_date, "153000")
                if b.get("date") == self.prev_date and "090000" <= b["time"] <= "153000"] if self.prev_date else []
        self.prev_bars[code] = (self.prev_date, bars)
        return bars

    def scan(self, now: datetime.datetime = None, quotes: dict = None) -> dict:
        now = now or datetime.datetime.now(KST)
        today_iso, today = now.strftime("%Y-%m-%d"), now.strftime("%Y%m%d")
        self._load_closes(today_iso)
        pool = self.danta.pool()
        quotes = quotes if quotes is not None else self.api.get_multi_price(list(pool), pause=0.15)
        stage1 = []
        for code, q in quotes.items():
            name = pool.get(code) or q.get("name", code)
            op, px = q.get("open", 0), q.get("price", 0)
            if px <= 0 or op <= 0 or not cross_ok(self.prev_candle.get(name), px, op):
                continue
            if leader_scan.excluded_by_name(code, name) or q.get("value", 0) < D_VALUE_1M_MIN:
                continue
            ok, ma5, ma20 = ma_ok(self.closes.get(name, []), px)
            if not ok:
                continue
            stage1.append((code, name, q, ma5, ma20))
        stage1.sort(key=lambda x: -x[2].get("value", 0))
        results, new_lookups = [], 0
        for code, name, q, ma5, ma20 in stage1:
            if len(results) >= DEEP_CHECK_MAX:
                break
            cached = self.danta.shares.get(code)
            if not (cached and cached[1] == today_iso):
                if new_lookups >= NEW_SHARES_PER_SCAN:
                    continue
                new_lookups += 1
            shares = self.danta._shares(code, today_iso)
            if shares <= 0 or self.danta.excluded.get(code, ("", ""))[0]:
                continue
            cap = q["price"] * shares / 1e8
            if cap < G_CAP_MIN_EOK:
                continue
            fails = []
            s = self.api.get_ccnl(code).get("strength")
            if s is None:
                fails.append("F체결강도 조회실패")
            elif not (F_STRENGTH_MIN <= s <= F_STRENGTH_MAX):
                fails.append(f"F체결강도 {s:.0f}%")
            chk = {}
            if not fails:
                tb = self._today_bars(code, now)
                prev = self._prev(code) if len(tb) <= AVG_BARS else []
                chk = bar_checks(tb, prev, q["open"])
                fails += chk["fails"]
            results.append({"name": name, "code": code, "price": q["price"], "chg": q.get("chg", 0.0),
                            "open": q["open"], "cap_eok": cap, "strength": s, "ma5": ma5, "ma20": ma20,
                            "first_rate": chk.get("first_rate"), "value_1m": chk.get("value_1m"),
                            "vol_ratio": chk.get("vol_ratio"), "passed": not fails, "fails": fails})
        results.sort(key=lambda r: (not r["passed"], len(r["fails"]), -r["chg"]))
        return {"time": now.strftime("%H:%M"), "pool": len(pool), "stage1": len(stage1), "results": results}


def format_hit(r: dict, overlap: str = "") -> str:
    return (f"🎯 **{r['name']}**({r['code']}) {r['price']:,.0f}원 {r['chg']:+.2f}%"
            + (f"  🔗 {overlap}" if overlap else "") + "\n"
            f"   어제 음봉 → 시가 {r['open']:,.0f} 위 · 첫분봉 {r['first_rate']:+.2f}% · 1분 대금 {r['value_1m'] / 1e8:.1f}억 · "
            f"거래량 {r['vol_ratio']:.0f}% · 체결강도 {r['strength']:.0f}% · 시총 {r['cap_eok']:,.0f}억")


def log_scan(out: dict, now: datetime.datetime = None, db_path: str = tml.LOG_DB) -> int:
    rows = out.get("results") or []
    if not rows:
        return 0
    now = now or datetime.datetime.now(KST)
    conn = sqlite3.connect(db_path, timeout=10)
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS obs0909 (
            date TEXT, time TEXT, code TEXT, name TEXT, price REAL, chg REAL, strength REAL,
            value_1m REAL, vol_ratio REAL, first_rate REAL, passed INTEGER, fails TEXT)""")
        conn.executemany("INSERT INTO obs0909 VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", [
            (now.strftime("%Y-%m-%d"), now.strftime("%H:%M"), r["code"], r["name"], r["price"], r["chg"],
             r["strength"], r["value_1m"], r["vol_ratio"], r["first_rate"], int(r["passed"]), ", ".join(r["fails"]))
            for r in rows])
        conn.commit()
        return len(rows)
    finally:
        conn.close()
