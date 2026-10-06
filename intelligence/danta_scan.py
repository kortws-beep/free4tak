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
# ★ 2026-10-06 실측 3건으로 뒤집음: 키움 "매도매수잔량비"는 총매수잔량 ÷ 총매도잔량
#   (매도벽이 더 두꺼워야 통과)으로 보인다.
#   라온시큐어 매도66,451/매수6,708 → 키움 통과 / 알멕 매수쪽 8배 → 키움 없음 /
#   한선엔지니어링 매수쪽 1.1배 → 키움 없음. 처음(매도÷매수)과 정반대로 맞음.
K_BID_OVER_ASK  = True
PRE_VALUE_MIN   = 2_900_000_000   # 1단계 사전필터(F·G에서 따라나오는 최소 거래대금)
BAR_CACHE_MAX   = 600
MULTI_PAUSE     = 0.15            # 복수시세 묶음 사이 쉬는 시간(초) — 70묶음 ≈ 15초
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


def count_ticks_1m(api, code: str, now_hhmmss: str, max_pages: int = 5):
    """최근 60초 체결건수를 시간대별체결 API로 거슬러 올라가며 직접 센다.
    ★ 2026-10-06: 처음엔 최근 30건의 간격으로 "분당 속도"를 추정했는데, 한 번에
      몰린 체결(30건이 1초 안)이 1,800건/분으로 부풀려져 키움에 없는 종목이
      통과했음(알멕·한선엔지니어링 10:59). 반환: (건수, 확실한지)"""
    now = _secs(now_hhmmss)
    seen, t = set(), now_hhmmss
    for _ in range(max_pages):
        page = api.get_time_ticks(code, t)
        if not page:
            return len(seen), False
        new = [x for x in page if x["acml_vol"] not in seen]
        for x in new:
            if now - _secs(x["time"]) < 60:
                seen.add(x["acml_vol"])
        if len(seen) >= C_TICKS_MIN:
            return len(seen), True
        oldest = min(page, key=lambda x: x["time"])["time"]
        if now - _secs(oldest) >= 60 or not new:
            return len(seen), True             # 1분 구간을 다 덮음
        t = oldest
    return len(seen), False


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
        self.diag: dict = {}        # 마지막 스캔의 종목별 판정 {code: (name, 설명)} — !단타 종목명 용
        self.diag_time = ""

    # ── 데이터 준비 ──
    def pool(self) -> dict:
        """★ 2026-10-06: 처음엔 주도주 풀(거래대금 상위 위주)을 같이 썼는데, 키움에 뜬
        한선엔지니어링 같은 중소형주가 빠졌음 — 단타000은 시총 1000억대도 대상이라
        일봉 DB 전 종목(약 2,100) + 순위 API 보완을 쓴다(복수시세 70여 회/분)."""
        ts, p = self._pool
        if time.time() - ts > 180 or not p:
            p = leader_scan.build_pool(self.api)
            conn = sqlite3.connect(tml.THEME_DB, timeout=10)
            try:
                for name, code in tml._name_code_map(conn).items():
                    p.setdefault(code, name)
            finally:
                conn.close()
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
        quotes = self.api.get_multi_price(list(pool), pause=MULTI_PAUSE)
        stage1, diag = [], {}
        missing = [c for c in pool if c not in quotes]
        for c in missing:
            diag[c] = (pool[c], "시세 조회 안 됨")
        for code, q in quotes.items():
            name = pool.get(code) or q["name"]
            if q["price"] <= 0 or leader_scan._is_etf(name):
                diag[code] = (name, "가격 없음/ETF"); continue
            if q["value"] < PRE_VALUE_MIN:
                diag[code] = (name, f"거래대금 {q['value'] / 1e8:.0f}억 < 29억 (시총·회전율 조건상 불가)"); continue
            num, den = ((q.get("bid_rsqn"), q.get("ask_rsqn")) if K_BID_OVER_ASK
                        else (q.get("ask_rsqn"), q.get("bid_rsqn")))
            if den:
                ratio = num / den * 100
                if ratio > K_ASK_BID_MAX:
                    diag[code] = (name, f"K잔량비 {ratio:.0f}% > 100% (총매도잔량 "
                                        f"{q['ask_rsqn']:,.0f} / 총매수잔량 {q['bid_rsqn']:,.0f})"); continue
            else:
                ratio = None
            stage1.append((code, name, q, ratio))

        results = []
        for code, name, q, ratio in stage1:
            shares = self._shares(code, today)
            if shares <= 0:
                diag[code] = (name, "상장주식수 조회 실패"); continue
            cap = q["price"] * shares / 1e8
            turnover = q["volume"] / shares * 100
            if not (G_CAP_MIN_EOK <= cap <= G_CAP_MAX_EOK) or turnover < F_TURNOVER_MIN:
                diag[code] = (name, f"G시총 {cap:,.0f}억 / F회전율 {turnover:.1f}% "
                                    f"(기준 1,000~9,990억, 3%↑)"); continue
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
            # 최근 30건 안에 1분이 다 들어가면 그대로 센 값이 정확 → 100건 미만 확정.
            # 30건이 전부 1분 안이면(활발) 아래에서 다른 조건 통과 후 직접 센다.
            rate = tick_rate(cc["ticks"], now.strftime("%H%M%S"))
            rate_exact = rate < len(cc["ticks"]) or not cc["ticks"]
            if rate_exact and rate < C_TICKS_MIN: fails.append(f"C체결{rate:.0f}건/분")
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
            if not fails and not rate_exact:    # 체결건수 직접 세기(호출 최대 5회)
                cnt, sure = count_ticks_1m(self.api, code, now.strftime("%H%M%S"))
                rate = float(cnt)
                if cnt < C_TICKS_MIN:
                    fails.append(f"C체결{cnt}건/분" + ("" if sure else " (확인불가)"))
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
        for r in results:
            diag[r["code"]] = (r["name"], "✅ 전 조건 통과" if r["passed"] else ", ".join(r["fails"]))
        self.diag, self.diag_time = diag, now.strftime("%H:%M")
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


def explain(scanner: "DantaScanner", keyword: str) -> list:
    """마지막 스캔에서 이름/코드에 keyword가 들어간 종목의 판정 설명."""
    # ★ 재시작 직후엔 첫 검사(상장주식수 조회로 1분 넘게 걸림)가 끝나기 전이라
    #   diag가 비어 있음 — "풀에 없음"으로 오해하지 않게 구분(10:57 실사례).
    if not scanner.diag:
        return ["첫 검사가 아직 끝나지 않았어 — 1~2분 뒤 다시 쳐줘"]
    out = [f"{n}({c}) — {why}" for c, (n, why) in scanner.diag.items()
           if keyword and (keyword in n or keyword == c)]
    out = out or [f"'{keyword}' — 후보 풀(일봉 DB 종목 + 순위 API)에 없음"]
    return [f"({scanner.diag_time} 검사 기준)"] + out


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
        tk = api.get_time_ticks("005930", now.strftime("%H%M%S"))
        print(f"시간대별체결: {len(tk)}건", tk[:2], "…", tk[-1:])
        print("최근 1분 체결건수:", count_ticks_1m(api, "005930", now.strftime("%H%M%S")))
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
