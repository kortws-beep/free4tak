"""
sector_watch.py — 한투 관심그룹(대장 분야별 정리) 기반 섹터 강도·대장주 감시 (2026-10-06)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

대장이 한투 HTS에 분야별로 정리해 둔 관심그룹(우주, 반도체 소부장 …)과
NEW 그룹(요즘 부각 + 전문가 추천)을 API로 읽어서:
  1. 분야별 강도 순위 — 그룹 평균 등락률·상승종목 비율·거래대금
  2. 강한 분야의 대장주(그룹 내 오늘 거래대금 1위)·2등주(2위)가
     +3%를 넘어 움직이기 시작하면 알림(종목당 하루 1번)
  3. 그 종목이 NEW에 있으면 ⭐, 오늘 주도주/단타000/3개월수급 기록과
     겹치면 🔗
테마 판단은 대장이 하고(그룹 구성), 리나는 그 안에서 "오늘 어디가 세고
누가 움직이나"를 숫자로 보여준다. 매매는 하지 않는다.
"""
import os
import sqlite3
import datetime
import time

import three_month_leader as tml

KST = datetime.timezone(datetime.timedelta(hours=9))

NEW_GROUP_NAMES = ("new", "신규추천", "신규", "new추천")   # sbot과 같은 기준
GROUP_RELOAD_SEC = 1800       # 관심그룹 구성은 30분마다 다시 읽음(장중 편집 반영)
TOP_SECTORS      = 3          # 알림 대상: 강도 상위 몇 개 분야
SECTOR_MIN_AVG   = 1.5        # 그 분야 평균 등락률이 이 이상일 때만(%)
LEADER_MOVE_PCT  = 3.0        # 대장·2등주가 이 이상 오르면 "움직이기 시작"(%)
MIN_GROUP_SIZE   = 2          # 종목 1개짜리 그룹은 순위에서 제외
MULTI_PAUSE      = 0.15
HOT_PCT          = 5.0        # 그룹 안 "급등" 종목 기준(%) — 넓은 업종 안의 세부 테마 쏠림 표시용


def hts_id() -> str:
    return os.getenv("KIS_HTS_ID2", os.getenv("KIS_HTS_ID", ""))


def load_groups(api, user_id: str) -> dict:
    """{그룹명: [(code, name), ...]} — 이름이 같은 그룹은 합침."""
    out: dict = {}
    for code, name in api.get_watchlist_groups(user_id).items():
        merged = out.setdefault(name, [])
        for s in api.get_watchlist_stocks(code, user_id):
            if s not in merged:
                merged.append(s)
    return {k: v for k, v in out.items() if v}


def is_new_group(name: str) -> bool:
    return name.strip().lower() in NEW_GROUP_NAMES


def rank_sectors(groups: dict, quotes: dict) -> list:
    """분야별 강도. 반환(강한 순): [{"group","avg_chg","up_ratio","value","n",
    "members":[{"code","name","chg","value","price"} 거래대금순]}]"""
    out = []
    for gname, stocks in groups.items():
        if is_new_group(gname):
            continue
        mem = []
        for code, name in stocks:
            q = quotes.get(code)
            if q and q["price"] > 0:
                mem.append({"code": code, "name": name or q["name"], "chg": q["chg"],
                            "value": q["value"], "price": q["price"]})
        if len(mem) < MIN_GROUP_SIZE:
            continue
        mem.sort(key=lambda m: -m["value"])
        out.append({
            "group": gname, "n": len(mem),
            "avg_chg": sum(m["chg"] for m in mem) / len(mem),
            "up_ratio": sum(m["chg"] > 0 for m in mem) / len(mem) * 100,
            # ★ 업종 그룹이 넓으면(우주방산통신 31종목) 그 안의 세부 테마(우주)가 터져도
            #   평균에 묻힘(10-06 실측: 우주 강세인데 8위) — 급등 종목 수를 같이 보여줌
            "hot": [m for m in mem if m["chg"] >= HOT_PCT],
            "value": sum(m["value"] for m in mem),
            "members": mem,
        })
    out.sort(key=lambda s: -s["avg_chg"])
    return out


def leader_moves(sectors: list, new_codes: set, already: set) -> list:
    """강한 분야의 대장·2등주 중 새로 +3%를 넘은 종목."""
    hits = []
    for rank, s in enumerate(sectors[:TOP_SECTORS], 1):
        if s["avg_chg"] < SECTOR_MIN_AVG:
            break
        for pos, m in enumerate(s["members"][:2]):
            if m["chg"] >= LEADER_MOVE_PCT and m["code"] not in already:
                hits.append(dict(m, group=s["group"], sector_rank=rank, sector_avg=s["avg_chg"],
                                 role="대장" if pos == 0 else "2등", is_new=m["code"] in new_codes))
    return hits


class SectorWatcher:
    def __init__(self, api, user_id: str = None):
        self.api = api
        self.user_id = user_id or hts_id()
        self._groups = (0.0, {})

    def groups(self) -> dict:
        ts, g = self._groups
        if time.time() - ts > GROUP_RELOAD_SEC or not g:
            g = load_groups(self.api, self.user_id) or g      # 실패하면 직전 구성 유지
            self._groups = (time.time(), g)
        return g

    def scan(self, now: datetime.datetime = None) -> dict:
        now = now or datetime.datetime.now(KST)
        groups = self.groups()
        codes = sorted({c for stocks in groups.values() for c, _ in stocks})
        quotes = self.api.get_multi_price(codes, pause=MULTI_PAUSE) if codes else {}
        new_codes = {c for g, stocks in groups.items() if is_new_group(g) for c, _ in stocks}
        new_quotes = sorted((dict(quotes[c], code=c) for c in new_codes if c in quotes),
                            key=lambda q: -q["chg"])
        return {"time": now.strftime("%H:%M"), "groups": len(groups), "codes": len(codes),
                "sectors": rank_sectors(groups, quotes), "new_codes": new_codes,
                "new_quotes": new_quotes}


def format_ranking(out: dict, top: int = 5) -> str:
    lines = [f"🗺️ **분야별 강도** {out['time']} (관심그룹 {out['groups']}개 · {out['codes']}종목)"]
    for i, s in enumerate(out["sectors"][:top], 1):
        lead = " / ".join(f"{m['name']} {m['chg']:+.1f}%" for m in s["members"][:2])
        lines.append(f"{i}. **{s['group']}** 평균 {s['avg_chg']:+.2f}% · 상승 {s['up_ratio']:.0f}% · "
                     f"대금 {s['value'] / 1e8:,.0f}억 — {lead}")
    # 순위 밖이어도 급등 종목이 몰린 그룹은 따로 표시
    hot = sorted((s for s in out["sectors"] if len(s["hot"]) >= 3), key=lambda s: -len(s["hot"]))
    if hot:
        lines.append(f"🔥 +{HOT_PCT:.0f}%↑ 몰린 그룹: " + " · ".join(
            f"{s['group']} {len(s['hot'])}개({', '.join(m['name'] for m in sorted(s['hot'], key=lambda m: -m['chg'])[:3])})"
            for s in hot[:4]))
    if out["sectors"][top:]:
        weak = out["sectors"][-1]
        lines.append(f"   (최약: {weak['group']} {weak['avg_chg']:+.2f}%)")
    if out.get("new_quotes"):
        top_new = ", ".join(f"{q['name']} {q['chg']:+.1f}%" for q in out["new_quotes"][:5])
        lines.append(f"⭐ NEW 상위: {top_new}")
    return "\n".join(lines)


def format_move(h: dict, overlap: str = "") -> str:
    return (f"🚩 **{h['name']}**({h['code']}) {h['price']:,.0f}원 {h['chg']:+.2f}%"
            f"{' ⭐NEW' if h['is_new'] else ''}{f'  🔗 {overlap}' if overlap else ''}\n"
            f"   [{h['group']}] {h['role']}주 — 분야 강도 {h['sector_rank']}위(평균 {h['sector_avg']:+.2f}%) "
            f"| 거래대금 {h['value'] / 1e8:,.0f}억")


def format_group(out: dict, keyword: str) -> str:
    for s in out["sectors"]:
        if keyword in s["group"]:
            lines = [f"🗂️ **{s['group']}** 평균 {s['avg_chg']:+.2f}% · 상승 {s['up_ratio']:.0f}% ({out['time']})"]
            for m in s["members"][:15]:
                star = " ⭐" if m["code"] in out["new_codes"] else ""
                lines.append(f"   {m['name']} {m['chg']:+.2f}% · 대금 {m['value'] / 1e8:,.0f}억{star}")
            return "\n".join(lines)
    return f"'{keyword}' 이름이 들어간 관심그룹이 없어 (그룹: {', '.join(s['group'] for s in out['sectors'])})"


def save_alerts(hits: list, now: datetime.datetime = None, db_path: str = tml.LOG_DB) -> None:
    """보낸 대장·2등주 알림 기록 — 재시작해도 같은 날 다시 안 보내게."""
    now = now or datetime.datetime.now(KST)
    conn = sqlite3.connect(db_path, timeout=10)
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS sector_alerts (
            date TEXT, time TEXT, code TEXT, name TEXT, grp TEXT, role TEXT, chg REAL, is_new INTEGER)""")
        conn.executemany("INSERT INTO sector_alerts VALUES (?,?,?,?,?,?,?,?)", [
            (now.strftime("%Y-%m-%d"), now.strftime("%H:%M"), h["code"], h["name"], h["group"],
             h["role"], h["chg"], int(h["is_new"])) for h in hits])
        conn.commit()
    finally:
        conn.close()


def alerted_today(date: str, db_path: str = tml.LOG_DB) -> set:
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        try:
            return {c for (c,) in conn.execute("SELECT code FROM sector_alerts WHERE date=?", (date,))}
        finally:
            conn.close()
    except sqlite3.OperationalError:
        return set()


def log_scan(out: dict, now: datetime.datetime = None, db_path: str = tml.LOG_DB) -> int:
    rows = out.get("sectors") or []
    if not rows:
        return 0
    now = now or datetime.datetime.now(KST)
    conn = sqlite3.connect(db_path, timeout=10)
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS sector_obs (
            date TEXT, time TEXT, grp TEXT, rank INTEGER, avg_chg REAL, up_ratio REAL,
            value REAL, leader TEXT, leader_chg REAL, second TEXT, second_chg REAL)""")
        conn.executemany("INSERT INTO sector_obs VALUES (?,?,?,?,?,?,?,?,?,?,?)", [
            (now.strftime("%Y-%m-%d"), now.strftime("%H:%M"), s["group"], i, s["avg_chg"],
             s["up_ratio"], s["value"], s["members"][0]["name"], s["members"][0]["chg"],
             s["members"][1]["name"] if len(s["members"]) > 1 else None,
             s["members"][1]["chg"] if len(s["members"]) > 1 else None)
            for i, s in enumerate(rows, 1)])
        conn.commit()
        return len(rows)
    finally:
        conn.close()


if __name__ == "__main__":
    # python sector_watch.py  — 관심그룹 읽기 + 지금 시점 분야 강도
    import sys
    sys.path.insert(0, os.path.join(tml._BASE, "core"))
    from dotenv import load_dotenv
    for _env in (os.path.join(tml._BASE, ".env"), os.path.join(tml._BASE, "lina_bot", ".env")):
        load_dotenv(_env)
    from kis_api import KisAPI
    w = SectorWatcher(KisAPI())
    g = w.groups()
    print(f"관심그룹 {len(g)}개: " + ", ".join(f"{k}({len(v)})" for k, v in g.items()))
    out = w.scan()
    print(format_ranking(out, top=10).replace("**", ""))
    for h in leader_moves(out["sectors"], out["new_codes"], set()):
        print(format_move(h).replace("**", ""))
