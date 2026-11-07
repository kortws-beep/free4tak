"""
buyback_pbr.py — 자사주 공시 3단 필터 + 저PBR '딱지' 공시 성적 (2026-11-07, 대장)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
11-07 disclosure_stock.py: 자사주(취득·신탁·소각) 공시 → 하락장 3일 보유 203건 +1.86%(승 65%).
대장 "희박한 조합이네 — 킵해 두었다가": 진짜 의미 있는 공시만 남는지 3단으로 거름 + 거래소 저PBR 딱지 공시도 같이.
  ① 시총(공시 전날 종가 × 지금 상장주식수, 억원)  ② 규모(취득·소각 주식 수 ÷ 상장주식수 %)
  ③ 종류: 취득 결정 · 취득 신탁 · 소각 결정 / 취득 목적: 주주가치·소각 vs 임직원 보상 등
  공시 본문은 KIND 뷰어 → 문서 경로 → 본문(htm)에서 뽑아 intelligence/kind_disclosure.db 테이블 detail에 캐시.
  저PBR: '기업가치 제고 계획(자율공시)(PBR 개선 계획 제출 기업…)' · 일반 '기업가치 제고 계획' 공시.
  진입: 장중 공시(09:00~15:20)는 2분 뒤, 장전은 그날 09:05, 장후는 다음 거래일 09:05. 비용 0.3%.
  보유: 당일 종가 · 다음날 시가 · 3일 종가 · 5일 종가
실행: venv/bin/python backtest/buyback_pbr.py
"""
import os
import re
import sys
import html
import time
import sqlite3
import datetime
import collections

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(BASE, "core"), os.path.join(BASE, "backtest"), os.path.join(BASE, "intelligence")]
import market_regime                    # noqa: E402
import dip_leader                       # noqa: E402
import sideways_pullback as SP          # noqa: E402
import disclosure_stock as DS           # noqa: E402

KIND_DB = DS.KIND_DB
UA = {"User-Agent": "Mozilla/5.0"}
COST, SLIP = 0.002, 0.001


def kind_of(title: str):
    t = title.replace(" ", "")
    if t.startswith("[정정]"):
        return None
    if "PBR개선계획제출기업" in t:
        return "PBR딱지"
    if t.startswith("기업가치제고계획(자율공시)"):
        return "밸류업"
    if "자기주식취득신탁계약체결결정" in t:
        return "취득신탁"
    if "자기주식취득결정" in t:
        return "취득결정"
    if "주식소각결정" in t:
        return "소각결정"
    return None


def _num(s):
    m = re.search(r"([\d,]+)", s or "")
    return int(m.group(1).replace(",", "")) if m else 0


def parse_doc(text: str, kind: str) -> dict:
    """본문 텍스트 → {주식수, 금액, 목적}."""
    out = {"shares": 0, "amount": 0, "purpose": ""}
    if kind == "취득결정":
        out["shares"] = _num((re.search(r"취득예정주식\(주\)\s*보통주식\s*([\d,]+)", text) or [None, ""])[1])
        out["amount"] = _num((re.search(r"취득예정금액\(원\)\s*보통주식\s*([\d,]+)", text) or [None, ""])[1])
        m = re.search(r"취득목적\s*(.+?)\s*\d+\.\s*취득방법", text)
        out["purpose"] = m.group(1)[:60] if m else ""
    elif kind == "취득신탁":
        out["amount"] = _num((re.search(r"계약금액\(원\)\s*([\d,]+)", text) or [None, ""])[1])
        out["shares"] = _num((re.search(r"취득예정주식\(주\)\s*보통주식\s*([\d,]+)", text) or [None, ""])[1])
        m = re.search(r"계약목적\s*(.+?)\s*\d+\.\s*", text)
        out["purpose"] = m.group(1)[:60] if m else ""
    elif kind == "소각결정":
        out["shares"] = _num((re.search(r"소각할\s*주식의\s*종류와\s*수\s*보통주식\s*\(주\)\s*([\d,]+)", text) or [None, ""])[1])
        out["amount"] = _num((re.search(r"소각예정금액\(원\)\s*([\d,]+)", text) or [None, ""])[1])
        out["purpose"] = "소각"
    return out


def fetch_text(acptno: str, get) -> str:
    v = get(f"https://kind.krx.co.kr/common/disclsviewer.do?method=search&acptno={acptno}&docno=&viewerhost=&viewerport=")
    m = re.search(r"option value='(\d+)\|Y'", v)
    if not m:
        return ""
    c = get(f"https://kind.krx.co.kr/common/disclsviewer.do?method=searchContents&docNo={m.group(1)}")
    p = re.search(r"setPath\('[^']*','([^']+\.htm)'", c)
    if not p:
        return ""
    d = get(p.group(1))
    d = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", d, flags=re.S)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", d)).split())


def _get(u: str) -> str:
    """KIND 본문은 utf-8인데 requests가 latin-1로 읽어 글자가 깨짐 → 바이트를 직접 풀음(안 되면 euc-kr)."""
    import requests
    b = requests.get(u, headers=UA, timeout=20).content
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("euc-kr", errors="ignore")


def detail(acptno: str, kind: str, get=None) -> dict:
    c = sqlite3.connect(KIND_DB)
    c.execute("CREATE TABLE IF NOT EXISTS detail (acptno TEXT PRIMARY KEY, kind TEXT, shares INTEGER, amount INTEGER, purpose TEXT)")
    r = c.execute("SELECT shares, amount, purpose FROM detail WHERE acptno=?", (acptno,)).fetchone()
    if r:
        c.close()
        return {"shares": r[0], "amount": r[1], "purpose": r[2]}
    if get is None:
        import requests
        get = _get
    try:
        d = parse_doc(fetch_text(acptno, get), kind)
    except Exception as e:
        print(f"  ⚠️ 본문 실패 {acptno}: {e}")
        d = {"shares": 0, "amount": 0, "purpose": ""}
    c.execute("INSERT OR REPLACE INTO detail VALUES (?,?,?,?,?)", (acptno, kind, d["shares"], d["amount"], d["purpose"]))
    c.commit()
    c.close()
    time.sleep(0.3)
    return d


def purpose_tag(p: str, kind: str) -> str:
    if kind == "소각결정" or "소각" in p:
        return "소각"
    if any(w in p for w in ("임직원", "보상", "상여", "RSU", "스톡", "우리사주", "성과")):
        return "임직원보상"
    if any(w in p for w in ("주주가치", "주가안정", "주가 안정", "주주환원", "가치제고")):
        return "주주가치"
    return "기타"


def main():
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE, ".env"))
    from kis_api import KisAPI
    api = KisAPI()
    store = SP.VolStore(api)
    c = sqlite3.connect(dip_leader.DAILY_DB)
    ds = [d for (d,) in c.execute("SELECT DISTINCT date FROM kr_stock_daily_data WHERE date >= '2026-05-01' ORDER BY date")]
    c.close()
    br = {d: v[0] for d, v in market_regime.compute(dates=ds).items()}
    bd = sorted(br)
    k = sqlite3.connect(KIND_DB)
    evs, seen = [], set()
    for acpt, date, tm, code, title in k.execute("SELECT acptno, date, time, code, title FROM disc WHERE code IS NOT NULL "
                                                  "AND date BETWEEN '2026-07-01' AND '2026-11-05' ORDER BY date, time"):
        kd = kind_of(title)
        if kd and (date, code, kd) not in seen:
            seen.add((date, code, kd))
            evs.append((acpt, date, tm, code, kd))
    k.close()
    print(f"공시 {len(evs)}건 — " + ", ".join(f"{x} {n}" for x, n in collections.Counter(e[4] for e in evs).items()), flush=True)
    shares_cache = {}
    res = collections.defaultdict(list)
    for i, (acpt, date, tm, code, kd) in enumerate(evs):
        det = detail(acpt, kd) if kd in ("취득결정", "취득신탁", "소각결정") else {"shares": 0, "amount": 0, "purpose": ""}
        if code not in shares_cache:
            md = api.get_market_data(code) or {}
            shares_cache[code] = float(md.get("lstn_stcn") or 0)
        sl = DS.slot(tm)
        days = store.days_from(code, date, 6)
        if not days:
            continue
        if sl in ("장전", "A오전", "점심", "B오후") and days[0][0] == date:
            d0, bars0, rest = days[0][0], days[0][1], days[1:]
            t_in = "090500" if sl == "장전" else (datetime.datetime.strptime(tm, "%H:%M") + datetime.timedelta(minutes=2)).strftime("%H%M00")
        else:
            nxt = [x for x in days if x[0] > date]
            if not nxt:
                continue
            d0, bars0, rest = nxt[0][0], nxt[0][1], nxt[1:]
            t_in = "090500"
        aft = DS.after(bars0, t_in)
        pc = DS.prev_close(code, d0)
        if not aft or not pc:
            continue
        entry = aft[0][1] * (1 + SLIP)
        cap = pc * shares_cache[code] / 1e8 if shares_cache[code] else 0
        ratio = (det["shares"] / shares_cache[code] * 100) if (det["shares"] and shares_cache[code]) else \
                (det["amount"] / (cap * 1e8) * 100 if (det["amount"] and cap) else 0)
        prior = [d for d in bd if d < d0]
        g = SP.bucket(br[prior[-1]]) if prior else "?"
        out = {"당일종가": (aft[-1][1] / entry - 1) * 100}   # ★ 제미나이 관전 포인트 — 당일 종가 청산도
        if rest:
            out["다음날시가"] = (rest[0][1][0][1] / entry - 1) * 100
        for n in (3, 5):
            if len(rest) >= n - 1:
                out[f"{n}일종가"] = (rest[n - 2][1][-1][1] / entry - 1) * 100 if n >= 2 else None
        cap_t = "시총1조↑" if cap >= 10000 else "시총1조↓" if cap else "시총?"
        size_t = "규모1%↑" if ratio >= 1 else "규모1%↓" if ratio else "규모?"
        pur = purpose_tag(det["purpose"], kd) if kd in ("취득결정", "취득신탁", "소각결정") else "-"
        for rule, r in out.items():
            if r is None:
                continue
            r -= COST * 100
            for key in ((kd, "전체"), (kd, g), (kd, cap_t), (kd, size_t), (kd, "목적:" + pur), (kd, g + "·" + cap_t),
                        ("자사주전체", g + "·" + cap_t + "·" + size_t), ("자사주전체", "목적:" + pur + "·" + g)):
                if key[0] == "자사주전체" and kd not in ("취득결정", "취득신탁", "소각결정"):
                    continue
                res[(key, rule)].append((d0, r))
        if i % 100 == 0:
            print(f"  {i}/{len(evs)} · API {store.calls}", flush=True)
    for grp in ("PBR딱지", "밸류업", "취득결정", "취득신탁", "소각결정", "자사주전체"):
        print(f"\n===== {grp} =====")
        for (key, rule) in sorted({k for k in res if k[0][0] == grp}, key=lambda x: (x[0][1], x[1])):
            xs = [r for _, r in sorted(res[(key, rule)])]
            if len(xs) >= 8:
                print(SP.line(f"{key[1]} {rule}", xs))


if __name__ == "__main__":
    sys.exit(main())
