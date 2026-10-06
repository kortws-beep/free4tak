"""
danta_scan.py — "단타000" 키움 조건식의 파이썬 구현 (관찰 전용, 2026-10-06)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

단타000은 잠깐 떴다 사라지는 종목을 잡는 식이라, 이 종목이 같은 날
주도주검색식3(또는 그 근접 후보)에 걸리는지 "겹침"을 보는 게 목적이다.

키움 원본 조건식 (전부 AND):
  A [5분] CCI(100) 100 이상
  B 체결강도 105% 이상
  C 최근 1분간 체결건수 100건 이상
  D 당일 순매수 체결수량 1,000주 이상
  E [1분] 순매수 체결수량 100주 이상
  F 거래량 회전율 3% 이상
  G 시가총액 1,000억 ~ 9,990억
  H 매수비율 51% 이상
  K 매도/매수 잔량비 100% 이하

계산 방법 (한투 API, 1분마다):
  1단계 — 주도주 후보 풀(전날 거래대금 상위 + 순위 API)의 복수시세로
     거래대금 29억 미만(시총 1000억×회전율 3%면 최소 30억)과 잔량비(K)를
     거르고, 남은 종목만 상장주식수(하루 1번 조회)로 시총(G)·회전율(F).
  2단계 — 체결 조회 1번으로 체결강도(B). 매수비율(H)·당일 순매수(D)는
     체결강도에서 계산된다: 매수비율 = 강도/(100+강도),
     순매수 = 거래량×(강도−100)/(강도+100). 1분 순매수(E)는 직전 검사와의
     순매수 차이, 1분 체결건수(C)는 최근 체결 30건의 시각 간격으로 추정.
  3단계 — 5분봉 CCI(100)는 1분봉 500개가 필요해 종목별로 처음 한 번만
     과거 분봉을 받아 두고 이후엔 최근 30개만 이어 붙인다.
근사치: C(체결건수)와 E(1분 순매수)는 키움과 계산 방식이 달라 경계값에서
  다를 수 있다. 실전 비교하며 조정.
"""
import datetime
import sqlite3
import time

import three_month_leader as tml
import leader_scan

KST = datetime.timezone(datetime.timedelta(hours=9))

A_CCI_MIN       = 100.0
CCI_PERIOD      = 100
B_STRENGTH_MIN  = 105.0
C_TICKS_MIN     = 100
D_NETBUY_MIN    = 1000
E_NETBUY_1M_MIN = 100
F_TURNOVER_MIN  = 3.0
G_CAP_MIN_EOK, G_CAP_MAX_EOK = 1000, 9990
H_BUY_RATIO_MIN = 51.0
K_ASK_BID_MAX   = 100.0
PRE_VALUE_MIN   = 2_900_000_000   # 1단계 사전필터(F·G에서 따라나오는 최소 거래대금)
BAR_CACHE_MAX   = 600
# ★ 2026-10-06 probe 실측: 일별분봉 API가 넥스트레이드(NXT) 시간외 분봉(08:00대,
#   15:30~20:00)까지 준다 — 키움 5분봉은 정규장만이라 정규장 분봉만 쓴다.
REG_START, REG_END = "090000", "153000"


def _regular(b: dict) -> bool:
    return REG_START <= b["time"] <= REG_END


def _secs(hhmmss: str) -> int:
    return int(hhmmss[:2]) * 3600 + int(hhmmss[2:4]) * 60 + int(hhmmss[4:6])


def tick_rate(ticks: list, now_hhmmss: str) -> float:
    """최근 체결 시각들(최신→과거, 최대 30건)로 1분간 체결건수 추정."""
    now = _secs(now_hhmmss)
    ts = [_secs(t) for t in ticks if len(t) == 6]
    recent = [t for t in ts if now - t < 60]   # 시계 오차로 미래 시각이어도 최근으로
    if len(recent) < len(ts) or not ts:
        return float(len(recent))          # 30건 안에 1분이 다 들어옴 → 그대로 셈
    span = max(now - min(ts), 1)           # 30건이 전부 최근 1분 안 → 속도로 환산
    return len(ts) * 60.0 / span


def five_min_cci(bars: list, period: int = CCI_PERIOD):
    """1분봉(아무 순서) → 5분봉(09:00, 09:05 …) → 마지막 봉의 CCI. (값, 사용한 5분봉 수)"""
    groups: dict = {}
    for b in sorted((b for b in bars if _regular(b)), key=lambda x: (x["date"], x["time"])):
        key = (b["date"], b["time"][:2], int(b["time"][2:4]) // 5)
        g = groups.get(key)
        if g is None:
            groups[key] = {"high": b["high"], "low": b["low"], "close": b["price"]}
        else:
            g["high"] = max(g["high"], b["high"]); g["low"] = min(g["low"], b["low"])
            g["close"] = b["price"]
    tps = [(g["high"] + g["low"] + g["close"]) / 3 for g in groups.values()]
    n = min(period, len(tps))
    if n < 2:
        return None, len(tps)
    win = tps[-n:]
    sma = sum(win) / n
    md = sum(abs(x - sma) for x in win) / n
    return ((win[-1] - sma) / (0.015 * md) if md else 0.0), len(tps)


class DantaScanner:
    """1분마다 scan()을 부르는 상태 보관형 스캐너(리나가 하나 들고 있음)."""

    def __init__(self, api):
        self.api = api
        self.shares: dict = {}      # code → (상장주식수, 날짜)
        self.netbuy: dict = {}      # code → [(시각 time.time(), 당일 순매수), ...] 최근 몇 개
        self.bars: dict = {}        # code → {(date,time): bar}
        self._pool = (0.0, {})

    # ── 데이터 준비 ──
    def pool(self) -> dict:
        ts, p = self._pool
        if time.time() - ts > 180 or not p:
            p = leader_scan.build_pool(self.api)
            self._pool = (time.time(), p)
        return p

    def _shares(self, code: str, today: str) -> float:
        cached = self.shares.get(code)
        if cached and cached[1] == today:
            return cached[0]
        md = self.api.get_market_data(code) or {}
        try:
            n = float(md.get("lstn_stcn") or 0)
        except (TypeError, ValueError):
            n = 0.0
        self.shares[code] = (n, today)
        return n

    def _minute_bars(self, code: str, now: datetime.datetime) -> list:
        today = now.strftime("%Y%m%d")
        cache = self.bars.get(code)
        if cache is None:
            cache = {}
            d, t = today, now.strftime("%H%M%S")
            for _ in range(10):                     # 120개씩, 정규장 분봉 510개 모일 때까지
                got = self.api.get_minute_bars_by_date(code, d, t)
                new = [b for b in got if (b["date"], b["time"]) not in cache]
                if not new:
                    break
                for b in new:
                    if _regular(b):
                        cache[(b["date"], b["time"])] = b
                oldest = min(new, key=lambda b: (b["date"], b["time"]))
                d, t = oldest["date"], oldest["time"]
                if t > REG_END:                     # 시간외 구간에 들어가면 그날 장마감으로 점프
                    t = REG_END
                if len(cache) >= CCI_PERIOD * 5 + 10:
                    break
            self.bars[code] = cache
        for b in self.api.get_minute_bars(code, now.strftime("%H%M%S")):
            b = dict(b, date=b.get("date") or today)
            cache[(b["date"], b["time"])] = b
        if len(cache) > BAR_CACHE_MAX:
            for k in sorted(cache)[:len(cache) - BAR_CACHE_MAX]:
                del cache[k]
        return list(cache.values())

    # ── 스캔 ──
    def scan(self, now: datetime.datetime = None) -> dict:
        now = now or datetime.datetime.now(KST)
        today = now.strftime("%Y-%m-%d")
        pool = self.pool()
        quotes = self.api.get_multi_price(list(pool))
        stage1 = []
        for code, q in quotes.items():
            name = pool.get(code) or q["name"]
            if q["price"] <= 0 or q["value"] < PRE_VALUE_MIN or leader_scan._is_etf(name):
                continue
            if q.get("bid_rsqn"):
                ratio = q["ask_rsqn"] / q["bid_rsqn"] * 100
                if ratio > K_ASK_BID_MAX:
                    continue
            else:
                ratio = None
            stage1.append((code, name, q, ratio))

        results = []
        for code, name, q, ratio in stage1:
            shares = self._shares(code, today)
            if shares <= 0:
                continue
            cap = q["price"] * shares / 1e8
            turnover = q["volume"] / shares * 100
            if not (G_CAP_MIN_EOK <= cap <= G_CAP_MAX_EOK) or turnover < F_TURNOVER_MIN:
                continue
            fails = []
            cc = self.api.get_ccnl(code)
            s = cc["strength"]
            if s is None:
                fails.append("B체결강도 조회실패")
                net = buy_ratio = None
            else:
                buy_ratio = s / (100 + s) * 100
                net = q["volume"] * (s - 100) / (s + 100)
                if s < B_STRENGTH_MIN:            fails.append(f"B체결강도{s:.0f}%")
                if buy_ratio < H_BUY_RATIO_MIN:   fails.append(f"H매수비율{buy_ratio:.0f}%")
                if net < D_NETBUY_MIN:            fails.append(f"D순매수{net:,.0f}주")
            rate = tick_rate(cc["ticks"], now.strftime("%H%M%S"))
            if rate < C_TICKS_MIN:                fails.append(f"C체결{rate:.0f}건/분")
            net_1m = None
            if net is not None:
                # 60초 전에 가장 가까운 기록(30~180초 전)과의 차이 → 1분당 순매수.
                # !단타를 수시로 쳐도 1분 감시의 측정이 깨지지 않게 이력을 몇 개 둔다.
                t_now = time.time()
                hist = [h for h in self.netbuy.get(code, []) if t_now - h[0] <= 180]
                base = [h for h in hist if t_now - h[0] >= 30]
                if base:
                    t0, n0 = min(base, key=lambda h: abs(t_now - h[0] - 60))
                    net_1m = (net - n0) * 60 / (t_now - t0)
                self.netbuy[code] = (hist + [(t_now, net)])[-8:]
            if net_1m is None:
                fails.append("E1분순매수 측정중")
            elif net_1m < E_NETBUY_1M_MIN:
                fails.append(f"E1분순매수{net_1m:,.0f}주")
            cci = n5 = None
            if not fails:                       # CCI는 분봉 조회가 비싸서 마지막에
                cci, n5 = five_min_cci(self._minute_bars(code, now))
                if cci is None or cci < A_CCI_MIN:
                    fails.append(f"A CCI{cci:.0f}" if cci is not None else "A CCI 분봉부족")
            results.append({
                "name": name, "code": code, "price": q["price"], "chg": q["chg"],
                "cap_eok": cap, "turnover": turnover, "ask_bid": ratio, "strength": s,
                "netbuy": net, "netbuy_1m": net_1m, "tick_rate": rate,
                "cci": cci, "cci_bars": n5, "passed": not fails, "fails": fails,
            })
        results.sort(key=lambda r: (not r["passed"], len(r["fails"]), -r["chg"]))
        return {"time": now.strftime("%H:%M"), "pool": len(pool), "stage1": len(stage1),
                "results": results}


def overlap_today(code: str, date: str, db_path: str = tml.LOG_DB) -> str:
    """같은 날 주도주3/3개월수급 기록과의 겹침 — "주도주 통과" / "주도주 근접" / ""."""
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        try:
            tags = []
            for table, label in (("leader_obs", "주도주"), ("tml_obs", "3개월수급")):
                try:
                    row = conn.execute(f"SELECT MAX(passed) FROM {table} WHERE date=? AND code=?",
                                       (date, code)).fetchone()
                except sqlite3.OperationalError:
                    continue
                if row and row[0] is not None:
                    tags.append(f"{label} {'통과' if row[0] else '근접'}")
            return ", ".join(tags)
        finally:
            conn.close()
    except Exception:
        return ""


def scan_and_tag(scanner: "DantaScanner", now: datetime.datetime = None) -> dict:
    """scan() + 결과마다 오늘 주도주/3개월수급 겹침 태그(overlap)."""
    out = scanner.scan(now)
    date = (now or datetime.datetime.now(KST)).strftime("%Y-%m-%d")
    for r in out["results"]:
        r["overlap"] = overlap_today(r["code"], date)
    return out


def format_hit(r: dict, overlap: str = "") -> str:
    ab = f"{r['ask_bid']:.0f}%" if r.get("ask_bid") is not None else "-"
    return (f"⚡ **{r['name']}**({r['code']}) {r['price']:,.0f}원 {r['chg']:+.2f}%"
            + (f"  🔗 {overlap}" if overlap else "") + "\n"
            f"   체결강도 {r['strength']:.0f}% | 1분 체결 {r['tick_rate']:.0f}건 | 1분 순매수 "
            f"{r['netbuy_1m'] or 0:,.0f}주 | CCI {r['cci'] or 0:.0f} | 회전율 {r['turnover']:.1f}% | "
            f"잔량비 {ab} | 시총 {r['cap_eok']:,.0f}억")


def log_scan(out: dict, now: datetime.datetime = None, db_path: str = tml.LOG_DB) -> int:
    rows = out.get("results") or []
    if not rows:
        return 0
    now = now or datetime.datetime.now(KST)
    conn = sqlite3.connect(db_path, timeout=10)
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS danta_obs (
            date TEXT, time TEXT, code TEXT, name TEXT, price REAL, chg REAL,
            strength REAL, tick_rate REAL, netbuy_1m REAL, cci REAL, turnover REAL,
            passed INTEGER, fails TEXT)""")
        conn.executemany("INSERT INTO danta_obs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", [
            (now.strftime("%Y-%m-%d"), now.strftime("%H:%M"), r["code"], r["name"], r["price"],
             r["chg"], r["strength"], r["tick_rate"], r["netbuy_1m"], r["cci"], r["turnover"],
             int(r["passed"]), ", ".join(r["fails"])) for r in rows])
        conn.commit()
        return len(rows)
    finally:
        conn.close()


if __name__ == "__main__":
    # python danta_scan.py — 1분 간격 2회 스캔(1분 순매수는 두 번째부터 계산됨)
    import os
    import sys
    sys.path.insert(0, os.path.join(tml._BASE, "core"))
    from dotenv import load_dotenv
    for _env in (os.path.join(tml._BASE, ".env"), os.path.join(tml._BASE, "lina_bot", ".env")):
        load_dotenv(_env)
    from kis_api import KisAPI
    api = KisAPI()
    if len(sys.argv) > 1 and sys.argv[1] == "probe":
        # python danta_scan.py probe — 새 API 응답 확인
        now = datetime.datetime.now(KST)
        print("체결:", api.get_ccnl("005930"))
        bars = api.get_minute_bars_by_date("005930", now.strftime("%Y%m%d"), now.strftime("%H%M%S"))
        print(f"일별분봉: {len(bars)}개", bars[:1], "…", bars[-1:])
        print("잔량:", {k: v for k, v in api.get_multi_price(["005930"]).get("005930", {}).items()
                       if k.endswith("rsqn")})
        sys.exit(0)
    sc = DantaScanner(api)
    for i in range(2):
        out = sc.scan()
        print(f"{out['time']} 풀 {out['pool']} → 1단계 {out['stage1']} → 검사 {len(out['results'])} "
              f"→ 통과 {sum(r['passed'] for r in out['results'])}")
        if i == 0:
            print("   (1분 순매수 측정을 위해 60초 대기…)")
            time.sleep(60)
    for r in out["results"][:20]:
        print(("✅ " if r["passed"] else "   ") + format_hit(r).replace("**", "")
              + ("" if r["passed"] else f"\n   ✗ {', '.join(r['fails'])}"))
