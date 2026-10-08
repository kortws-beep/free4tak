"""
daybot_replay.py — daybot 분봉 재현 백테스트 (2026-10-08)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

daybot_review.py 결과: 손절 뒤 주가가 대개 제자리로 돌아오고(흔들기에 털림),
09:40~11:00 매수가 특히 나빴고, 일손실 한도가 없다. 대장 결정: "백테스트로 정하자."
일봉 근사(daybot_backtest_engine)가 아니라, 실제 신호가 뜬 그 시각부터 한투 1분봉으로
다시 돌려 본다.

신호(무엇을 샀다고 칠지):
  trades — daybot이 실제로 산 것(실매매와 비교해 시뮬레이터 검증용)
  obs    — 리나 파이썬판 검색식(주도주검색식3/단타000/3개월수급) 통과 기록, 종목·날짜별
           첫 통과 시각·가격 (daybot 슬롯이 꽉 차 못 본 것까지 다 있음 → 표본 큼)
  cands  — daybot 후보로그(호가비율 미달 등으로 거른 것 포함) — 거르는 규칙이 맞는지
비교하는 것:
  1. 매수 시각대·등락률·출처별로 "이 신호를 샀다면" 성적 (현행 매도 규칙)
  2. 매도 규칙 — 손절폭 × 트레일링(시작점·폭) × 하룻밤 넘긴 종목 시초 손절 유예
  3. 실제처럼 슬롯 3개로 굴리며 — 매수 시간창 × 일손실 대응
     (없음 / -10만이면 그날 매수 중단 / -10만이면 매수금 절반으로 계속 —
      대장 우려 "한도로 멈추면 새로 발굴된 종목은 기회조차 없다")
가정: 신호가 뜬 분의 기록 가격에 매수(+0.1% 미끄러짐), 1분봉 고가/저가로 손절·트레일링
판정(한 봉 안에서 저가 먼저 — 불리하게), 봉 전체가 손절선 아래면 그 봉 고가에 체결,
수수료·세금 왕복 0.2%. 정규장(09:00~15:30) 분봉만. 분봉은 backtest/data/minute_cache.db에
저장해 두고 재사용.
실행:  python backtest/daybot_replay.py [최근 N일, 기본 20] [--src obs|trades|cands]
"""
import os
import sys
import sqlite3
import datetime
import itertools
from dataclasses import dataclass, replace

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DB = os.path.join(BASE, "backtest", "data", "minute_cache.db")
REG_START, REG_END = "090000", "153000"
COST, SLIP = 0.002, 0.001
AMT = 1_000_000
OBS_TABLES = (("leader_obs", "주도주"), ("danta_obs", "단타000"), ("tml_obs", "3개월수급"))


@dataclass(frozen=True)
class Rule:
    stop: float = -3.5          # 손절(%)
    tp: float = 2.5             # 이만큼 오르면 트레일링 시작
    tight: float = 1.5          # 고점 수익 widen_at 이하일 때 트레일링 폭
    wide: float = 2.0           # 그 위일 때 폭
    widen_at: float = 4.5
    floor: float = 1.0          # 트레일링 매도의 최소 보장 수익
    hold_days: int = 3          # 트레일링 못 타면 N영업일째 아침 정리
    grace: int = 0              # 하룻밤 넘긴 종목, 시초 N분은 손절 안 함
    # ★ 대장 수동매매 방식(2026-10-08): "09:40쯤이면 승패가 나고, 안 오른 놈은 오후까지
    #   가져가는데 거의 수익권" — 트레일링을 못 탄 종목을 아침에 -3.5%로 끊지 않고
    #   오후에 정리하는 규칙
    pm_take: float = 0.0        # pm_from 이후 이만큼(%) 수익이면 정리(0=안 씀)
    pm_from: str = "130000"
    eod: str = ""               # 이 시각까지 트레일링 못 탔으면 당일 정리(""=안 씀, 3영업일 기한 그대로)

    def label(self) -> str:
        return (f"손절{self.stop:g} 트레일 +{self.tp:g}→{self.tight:g}/{self.wide:g}"
                + (f" 시초유예{self.grace}분" if self.grace else "")
                + (f" {self.pm_from[:2]}시후+{self.pm_take:g}%정리" if self.pm_take else "")
                + (f" {self.eod[:2]}:{self.eod[2:4]}당일청산" if self.eod else ""))


CURRENT = Rule()


def _mins(t: str) -> int:
    return int(t[:2]) * 60 + int(t[2:4])


# ── 분봉 저장소 ────────────────────────────────────────────
class MinuteStore:
    def __init__(self, api=None, path: str = CACHE_DB, pause: float = 0.06):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.api, self.pause = api, pause
        self.conn = sqlite3.connect(path)
        self.conn.execute("CREATE TABLE IF NOT EXISTS bars (code TEXT, date TEXT, time TEXT, price REAL, "
                          "high REAL, low REAL, PRIMARY KEY(code, date, time))")
        self.conn.execute("CREATE TABLE IF NOT EXISTS done (code TEXT, date TEXT, PRIMARY KEY(code, date))")
        self.calls = 0

    def day(self, code: str, date: str) -> list:
        """[(time, price, high, low)] 정규장, 오래된→최신. date는 YYYY-MM-DD."""
        done = self.conn.execute("SELECT 1 FROM done WHERE code=? AND date=?", (code, date)).fetchone()
        if not done and self.api is not None:
            self._fetch(code, date)
        return self.conn.execute("SELECT time, price, high, low FROM bars WHERE code=? AND date=? "
                                 "ORDER BY time", (code, date)).fetchall()

    def _fetch(self, code: str, date: str):
        import time as _t
        ymd, t, rows = date.replace("-", ""), REG_END, {}
        for _ in range(6):
            got = [b for b in self.api.get_minute_bars_by_date(code, ymd, t) if b["date"] == ymd]
            self.calls += 1
            _t.sleep(self.pause)
            new = [b for b in got if b["time"] not in rows]
            if not new:
                break
            for b in new:
                if REG_START <= b["time"] <= REG_END:
                    rows[b["time"]] = (b["price"], b["high"], b["low"])
            oldest = min(b["time"] for b in got)
            if oldest <= REG_START:
                break
            m = _mins(oldest) - 1
            t = f"{m // 60:02d}{m % 60:02d}00"
        self.conn.executemany("INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?)",
                              [(code, date, tm, *v) for tm, v in rows.items()])
        today = datetime.date.today().isoformat()
        if date < today or datetime.datetime.now().strftime("%H%M") >= "1540":
            self.conn.execute("INSERT OR REPLACE INTO done VALUES (?,?)", (code, date))
        self.conn.commit()

    def days_from(self, code: str, date: str, n: int) -> list:
        """date부터 거래가 있는 날 n개 [(date, bars)] — 주말·휴장일(분봉 없음)은 건너뜀."""
        out, d, today = [], datetime.date.fromisoformat(date), datetime.date.today()
        while len(out) < n and d <= today:
            if d.weekday() < 5:
                bars = self.day(code, d.isoformat())
                if bars:
                    out.append((d.isoformat(), bars))
            d += datetime.timedelta(days=1)
        return out


# ── 한 신호 시뮬레이션 (순수 함수) ───────────────────────────
def simulate(days: list, entry_time: str, entry_price: float, rule: Rule) -> dict:
    """days=[(date, [(time, price, high, low)])] 매수일부터. entry_time=HHMMSS.
    → {exit_date, exit_time, ret(소수), reason}"""
    entry = entry_price * (1 + SLIP)
    stop_px = entry * (1 + rule.stop / 100)
    peak, held = None, 0

    def done(d, t, px, reason):
        return {"exit_date": d, "exit_time": t, "ret": px * (1 - SLIP) / entry - 1 - COST, "reason": reason}
    last = None
    for di, (d, bars) in enumerate(days):
        if di > 0:
            held += 1
            if peak is None and held >= rule.hold_days and bars:
                return done(d, bars[0][0], bars[0][1], "보유기한")
        for t, p, h, l in bars:
            if di == 0 and t <= entry_time:
                continue
            last = (d, t, p)
            if peak is None:
                in_grace = di > 0 and _mins(t) - 540 < rule.grace
                if not in_grace and l <= stop_px:
                    return done(d, t, min(stop_px, h), "손절")
                if h >= entry * (1 + rule.tp / 100):
                    peak = h
                    continue
                if rule.pm_take and t >= rule.pm_from and h >= entry * (1 + rule.pm_take / 100):
                    return done(d, t, max(p, entry * (1 + rule.pm_take / 100)), "오후정리")
                if rule.eod and t >= rule.eod:
                    return done(d, t, p, "당일청산")
                continue
            peak_rate = (peak / entry - 1) * 100
            trail = rule.wide if peak_rate > rule.widen_at else rule.tight
            ts_px = max(peak * (1 - trail / 100), entry * (1 + rule.floor / 100))
            if l <= ts_px:
                return done(d, t, min(ts_px, h), "트레일링")
            peak = max(peak, h)
    if last is None:
        return {}
    return dict(done(*last, "보유중"))


# ── 신호 불러오기 ──────────────────────────────────────────
def _find(*names) -> str:
    for d in (BASE, os.path.join(BASE, "bots"), os.path.join(BASE, "lina_bot"), os.getcwd()):
        for n in names:
            p = os.path.join(d, n)
            if os.path.exists(p):
                return p
    return ""


def load_signals(src: str, days: int) -> list:
    """[{date, time(HHMMSS), code, name, price, chg, source, extra}] — 종목·날짜별 첫 신호."""
    since = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    out = []
    if src == "obs":
        db = _find("three_month_leader_log.db")
        conn = sqlite3.connect(db)
        try:
            for table, label in OBS_TABLES:
                try:
                    rows = conn.execute(f"SELECT date, time, code, name, price, chg FROM {table} "
                                        "WHERE passed=1 AND date>=? ORDER BY date, time", (since,)).fetchall()
                except sqlite3.OperationalError:
                    continue
                for d, t, code, name, px, chg in rows:
                    out.append({"date": d, "time": t.replace(":", "")[:4] + "00", "code": code, "name": name,
                                "price": px, "chg": chg, "source": label, "extra": ""})
        finally:
            conn.close()
    else:
        conn = sqlite3.connect(_find("daybot_trade_history.db"))
        try:
            if src == "trades":
                rows = conn.execute("SELECT buy_time, code, stock_name, buy_price, buy_tag, profit_rate, "
                                    "sell_reason FROM trades WHERE buy_time>=? AND sell_price IS NOT NULL "
                                    "AND (buy_tag IS NULL OR buy_tag!='수동') ORDER BY buy_time",
                                    (since,)).fetchall()
                for bt, code, name, px, tag, pr, reason in rows:
                    out.append({"date": bt[:10], "time": bt[11:19].replace(":", ""), "code": code,
                                "name": name or code, "price": px, "chg": None, "source": tag or "-",
                                "extra": (pr, reason or "")})
            else:
                rows = conn.execute("SELECT ts, code, stock_name, price, change_rate, source_tier, skip_reason, "
                                    "bought FROM candidate_log WHERE ts>=? AND price>0 ORDER BY ts",
                                    (since,)).fetchall()
                for ts, code, name, px, chg, tier, skip, bought in rows:
                    out.append({"date": ts[:10], "time": ts[11:19].replace(":", ""), "code": code,
                                "name": name or code, "price": px, "chg": chg, "source": tier or "-",
                                "extra": "매수" if bought else (skip or "-")})
        finally:
            conn.close()
    if src == "trades":
        return out
    first = {}
    for s in sorted(out, key=lambda x: (x["date"], x["time"])):
        first.setdefault((s["date"], s["code"]), s)
    return list(first.values())


# ── 집계 ──────────────────────────────────────────────────
def time_bucket(t: str) -> str:
    for end, label in (("0940", "09:00~09:40"), ("1100", "09:40~11:00"), ("1300", "11:00~13:00")):
        if t[:4] < end:
            return label
    return "13:00~15:30"


def chg_bucket(c) -> str:
    if c is None:
        return "-"
    for hi, label in ((0, "마이너스"), (3, "0~3%"), (8, "3~8%"), (15, "8~15%")):
        if c < hi:
            return label
    return "15%~"


def stats(rets: list) -> dict:
    if not rets:
        return {"n": 0}
    w = [r for r in rets if r > 0]
    l = [r for r in rets if r <= 0]
    return {"n": len(rets), "win": len(w) / len(rets) * 100, "avg": sum(rets) / len(rets) * 100,
            "pf": sum(w) / -sum(l) if l and sum(l) < 0 else float("inf"), "krw": sum(rets) * AMT}


def fmt(s: dict) -> str:
    if not s["n"]:
        return "0건"
    pf = f"{s['pf']:.2f}" if s["pf"] != float("inf") else "∞"
    return f"{s['n']:>4}건 승률 {s['win']:>3.0f}% 평균 {s['avg']:+.2f}% PF {pf:>4} | 100만씩 {s['krw']:>+10,.0f}원"


def run_signals(store: MinuteStore, signals: list, rule: Rule) -> list:
    out = []
    for s in signals:
        days = store.days_from(s["code"], s["date"], rule.hold_days + 2)
        if not days or days[0][0] != s["date"]:
            continue
        r = simulate(days, s["time"], s["price"], rule)
        if r:
            out.append(dict(s, **r))
    return out


# ── 섹터 태그 (대장 아이디어 2026-10-08: "변화된 섹터의 1·2등주와 검색식이 겹치면
#    09:40 이후에도 사도 되지 않을까") — 리나 섹터감시 기록(sector_obs: 3분마다 분야 순위와
#    대장·2등 이름, sector_alerts: 강한 분야 대장·2등이 +3% 넘은 알림)으로 신호 시각 기준 판정
TOP_N = 3


def sector_tags(signals: list, db_path: str = None) -> None:
    """각 신호에 s["sector"] = "새섹터1·2등" / "상위섹터1·2등" / "" 를 붙임(제자리).
    새섹터 = 그날 첫 기록 땐 상위 TOP_N 밖이었다가 신호 시각엔 상위 TOP_N 안에 든 분야."""
    db_path = db_path or _find("three_month_leader_log.db")
    for s_ in signals:
        s_["sector"] = ""
    if not db_path:
        return
    conn = sqlite3.connect(db_path)
    def q(sql):
        try:
            return conn.execute(sql).fetchall()
        except sqlite3.OperationalError:          # 아직 안 만들어진 표(알림이 한 번도 안 나간 날 등)
            return []
    try:
        rows = q("SELECT date, time, grp, rank, leader, second FROM sector_obs ORDER BY date, time")
        alerts = q("SELECT date, time, code FROM sector_alerts")
    finally:
        conn.close()
    snaps, first_rank = {}, {}
    for d, t, grp, rank, leader, second in rows:
        snaps.setdefault(d, {}).setdefault(t, []).append((grp, rank, leader, second))
        first_rank.setdefault((d, grp), rank)
    alerted = {}
    for d, t, code in alerts:
        alerted.setdefault((d, code), t)
    for s_ in signals:
        times = [t for t in snaps.get(s_["date"], {}) if t.replace(":", "") <= s_["time"][:4]]
        if not times:
            continue
        snap = snaps[s_["date"]][max(times)]
        for grp, rank, leader, second in snap:
            if rank <= TOP_N and s_["name"] in (leader, second):
                new = first_rank.get((s_["date"], grp), rank) > TOP_N
                s_["sector"] = "새섹터1·2등" if new else "상위섹터1·2등"
                break
        a = alerted.get((s_["date"], s_["code"]))
        if not s_["sector"] and a and a.replace(":", "") <= s_["time"][:4]:
            s_["sector"] = "상위섹터1·2등"


def _early(r) -> bool:
    return r["time"][:4] < "0940"


WINDOWS = {
    "전체": lambda r: True,
    "09:40~11:00 제외": lambda r: not ("0940" <= r["time"][:4] < "1100"),
    "09:40 이전만": _early,
    "11:00 이전만": lambda r: r["time"][:4] < "1100",
    "09:40 이전+13시 이후": lambda r: _early(r) or r["time"][:4] >= "1300",
    "09:40 이전+이후엔 섹터1·2등": lambda r: _early(r) or bool(r.get("sector")),
    "09:40 이전+이후엔 새섹터만": lambda r: _early(r) or r.get("sector") == "새섹터1·2등",
}
LOSS_MODES = ("없음", "-10만 매수중단", "-10만 절반매수")   # -15만 절반은 1차 결과에서 늘 밀려 뺌


def portfolio(results: list, window, loss_mode: str, slots: int = 3) -> dict:
    """신호 시간순으로 슬롯 N개에 넣어 실제처럼 굴림. results는 run_signals 결과."""
    taken, by_day = [], {}
    for r in sorted(results, key=lambda x: (x["date"], x["time"])):
        if not window(r):
            continue
        now = (r["date"], r["time"])
        open_ = [x for x in taken if (x["exit_date"], x["exit_time"]) > now]
        if len(open_) >= slots or any(x["code"] == r["code"] for x in open_):
            continue
        realized = sum(x["krw"] for x in taken if x["exit_date"] == r["date"]
                       and (x["exit_date"], x["exit_time"]) <= now)
        amt = AMT
        if loss_mode != "없음":
            lim = -100_000 if "-10만" in loss_mode else -150_000
            if realized <= lim:
                if "중단" in loss_mode:
                    continue
                amt = AMT // 2
        taken.append(dict(r, krw=r["ret"] * amt))
    for x in taken:
        by_day[x["exit_date"]] = by_day.get(x["exit_date"], 0) + x["krw"]
    cum = peak = mdd = 0.0
    for d in sorted(by_day):
        cum += by_day[d]; peak = max(peak, cum); mdd = min(mdd, cum - peak)
    rets = [x["ret"] for x in taken]
    s = stats(rets)
    s.update(krw=sum(x["krw"] for x in taken), worst=min(by_day.values()) if by_day else 0, mdd=mdd,
             days=len(by_day))
    return s


def group(results: list, key, order=None) -> list:
    g = {}
    for r in results:
        g.setdefault(key(r), []).append(r["ret"])
    keys = order or sorted(g, key=lambda k: -len(g[k]))
    return [f"   {k:<14} {fmt(stats(g[k]))}" for k in keys if k in g]


def main():
    n = next((int(a) for a in sys.argv[1:] if a.isdigit()), 20)
    src = sys.argv[sys.argv.index("--src") + 1] if "--src" in sys.argv else "obs"
    sys.path.insert(0, os.path.join(BASE, "core"))
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE, ".env"))
    from kis_api import KisAPI
    store = MinuteStore(KisAPI())

    if src in ("obs", "trades"):
        # 시뮬레이터 검증 — 실제 매매와 같은 규칙으로 돌려 결과가 비슷한지
        real = load_signals("trades", n)
        sim = run_signals(store, real, CURRENT)
        same = sum(1 for r in sim if r["extra"][1] and r["reason"][:2] in r["extra"][1])
        diff = [abs(r["ret"] * 100 - r["extra"][0]) for r in sim if r["extra"][0] is not None]
        print(f"🔧 검증: 실매매 {len(real)}건 → 재현 {len(sim)}건 | 매도사유 일치 {same}/{len(sim)} | "
              f"수익률 차이 평균 {sum(diff) / len(diff) if diff else 0:.2f}%p")
        print(f"   실제   {fmt(stats([r['extra'][0] / 100 - COST for r in sim if r['extra'][0] is not None]))}")
        print(f"   재현   {fmt(stats([r['ret'] for r in sim]))}")

    signals = load_signals(src, n)
    sector_tags(signals)
    print(f"\n📈 신호: {src} {len(signals)}개 (종목·날짜별 첫 신호, 최근 {n}일) — 분봉 받는 중…")
    base = run_signals(store, signals, CURRENT)
    print(f"   분봉 재현 {len(base)}개 · 한투 호출 {store.calls}회")
    print(f"\n■ 1. 현행 매도 규칙으로 '이 신호를 다 샀다면' — {fmt(stats([r['ret'] for r in base]))}")
    print("  [매수 시각대]")
    print("\n".join(group(base, lambda r: time_bucket(r["time"]),
                         ["09:00~09:40", "09:40~11:00", "11:00~13:00", "13:00~15:30"])))
    print("  [신호 때 등락률]")
    print("\n".join(group(base, lambda r: chg_bucket(r["chg"]),
                         ["마이너스", "0~3%", "3~8%", "8~15%", "15%~", "-"])))
    print("  [출처]")
    print("\n".join(group(base, lambda r: r["source"])))
    late = [r for r in base if not _early(r)]
    print(f"  [09:40 이후 신호 {len(late)}개 — 섹터 1·2등과 겹침 여부]")
    print("\n".join(group(late, lambda r: r.get("sector") or "섹터 무관")))
    if src == "cands":
        print("  [daybot이 거른 사유 — '매수'보다 거른 쪽이 나으면 그 거름이 틀린 것]")
        print("\n".join(group(base, lambda r: r["extra"])))
    print("  [매도 사유]")
    print("\n".join(group(base, lambda r: r["reason"])))

    print("\n■ 2. 매도 규칙 비교 (같은 신호 전부, 100만씩)")
    grid = []
    for stop, (tp, tight, wide, widen), grace in itertools.product(
            (-3.5, -5.0, -6.0), ((2.5, 1.5, 2.0, 4.5), (3.0, 2.0, 3.0, 6.0), (4.0, 2.5, 3.5, 8.0)), (0, 10)):
        rule = replace(CURRENT, stop=stop, tp=tp, tight=tight, wide=wide, widen_at=widen, grace=grace)
        res = base if rule == CURRENT else run_signals(store, signals, rule)
        grid.append((stats([r["ret"] for r in res])["krw"], rule, res))
    # 대장 수동 방식: 아침에 안 오른 종목은 오후까지 들고 가서 정리
    for stop, pm, eod in ((-7.0, 1.0, "151500"), (-5.0, 1.0, "151500"), (-7.0, 1.0, ""),
                          (-7.0, 0.0, "151500"), (-3.5, 0.0, "151500")):
        rule = replace(CURRENT, stop=stop, pm_take=pm, eod=eod)
        res = run_signals(store, signals, rule)
        grid.append((stats([r["ret"] for r in res])["krw"], rule, res))
    for krw, rule, res in sorted(grid, key=lambda x: -x[0]):
        mark = " ← 현행" if rule == CURRENT else ""
        print(f"   {rule.label():<34} {fmt(stats([r['ret'] for r in res]))}{mark}")

    print("\n■ 3. 슬롯 3개로 실제처럼 — 매수 시간창 × 일손실 대응 (매도 규칙: 현행 / 2번 1등 / 오후정리식 1등)")
    ranked = sorted(grid, key=lambda x: -x[0])
    picks = [("현행", base), (ranked[0][1].label(), ranked[0][2])]
    boss = next((g for g in ranked if g[1].pm_take or g[1].eod), None)      # 대장식 중 1등
    if boss and boss is not ranked[0]:
        picks.append((boss[1].label(), boss[2]))
    for title, res in picks:
        print(f"  [{title}]")
        rows = []
        for (wname, w), mode in itertools.product(WINDOWS.items(), LOSS_MODES):
            s = portfolio(res, w, mode)
            if s["n"]:
                rows.append((s["krw"], wname, mode, s))
        rows.sort(key=lambda x: -x[0])
        show = rows[:10] + [x for x in rows[10:] if "섹터" in x[1] or x[1] == "전체"]   # 섹터 조합·전체는 항상
        for krw, wname, mode, s in show:
            print(f"   {wname:<24} {mode:<12} {s['n']:>3}건 승률 {s['win']:>3.0f}% | {s['krw']:>+10,.0f}원 "
                  f"| 최악의 날 {s['worst']:+,.0f} · 최대낙폭 {s['mdd']:+,.0f}")
    print("\n※ 분봉 고가/저가 근사·신호가 매수 가정. 표본이 작으면(특히 시간대·등락률 칸) 방향만 참고.")


if __name__ == "__main__":
    sys.exit(main())
