"""
buyback_signal.py — 자사주 공시 엔진 신호(데이봇) (2026-11-07, 대장 "실전 테스트 — 마찬가지 50만 한도")
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
backtest/buyback_pbr.py(7/1~11/5 KIND 632건) 결과: 자사주 '취득'(결정·신탁) 중 목적이 주주가치·주가안정이고
상장주식 1%↑ 규모면 공시 뒤 3~5일 보유가 강함(하락장 3일 90건 승 71% +3.02%·5일 +4.65%, 횡보장 5일 +2.07%).
소각 결정·저PBR 딱지·임직원 보상 목적·1% 미만은 마이너스라 뺌. 시총은 오히려 작은 쪽이 나아서 하한 없음.
  · KIND 공시 목록은 리나·데이봇이 쌓는 intelligence/kind_disclosure.db(disc), 본문 추출은 detail 테이블에 캐시.
  · 본문: KIND 뷰어 → 문서 번호 → 본문 htm(utf-8) → 취득예정주식·금액·목적.
"""
import os
import re
import html
import time
import sqlite3

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KIND_DB = os.path.join(_BASE, "intelligence", "kind_disclosure.db")
UA = {"User-Agent": "Mozilla/5.0"}
TAG = "buyback"
AMOUNT = 500_000          # 대장 "마찬가지 50만 한도"
MIN_RATIO = 1.0           # 상장주식 대비 %
HOLD_DAYS = 5             # 산 날 포함 5거래일째 15:10 정리(백테스트 5일 종가)
STOP_PCT = 10.0           # 안전 손절(백테스트엔 손절 없음 — 음봉 엔진과 같은 -10%)
# ★ 2026-11-12 대장 "소규모 기업은 부각을 못 받아"(카스 매수 뒤) → backtest/buyback_pbr.py 엔진조건 시총 구간:
#   전체 기간은 소형주가 오히려 나음(500억↓ 5일 +4.69%)이라 평소엔 하한 없음 그대로, 하지만 하락장(국면<35%)에선
#   1천억↑ 27건 5일 +5.74%(뒤 절반 +7.90%) vs 1천억↓ 44건 +3.39%(뒤 +2.83%) → 하락장일 때만 시총 1천억↑(대장 A안).
DOWN_MIN_CAP_EOK = 1000


def kind_of(title: str):
    t = (title or "").replace(" ", "")
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


def _get(u: str) -> str:
    """KIND 본문은 utf-8인데 requests가 latin-1로 읽어 글자가 깨짐 → 바이트를 직접 풀음(안 되면 euc-kr)."""
    import requests
    b = requests.get(u, headers=UA, timeout=20).content
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("euc-kr", errors="ignore")


def fetch_text(acptno: str, get=_get) -> str:
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


def detail(acptno: str, kind: str, get=None, db: str = KIND_DB, pause: float = 0.3) -> dict:
    """본문 추출(캐시). 실패하면 빈 값 — 실패는 캐시에 안 남겨 다음에 다시 봄."""
    c = sqlite3.connect(db, timeout=10)
    c.execute("CREATE TABLE IF NOT EXISTS detail (acptno TEXT PRIMARY KEY, kind TEXT, shares INTEGER, amount INTEGER, purpose TEXT)")
    r = c.execute("SELECT shares, amount, purpose FROM detail WHERE acptno=?", (acptno,)).fetchone()
    if r:
        c.close()
        return {"shares": r[0], "amount": r[1], "purpose": r[2]}
    try:
        d = parse_doc(fetch_text(acptno, get or _get), kind)
    except Exception as e:
        print(f"  ⚠️ 본문 실패 {acptno}: {e}")
        c.close()
        return {"shares": 0, "amount": 0, "purpose": ""}
    if d["shares"] or d["amount"] or d["purpose"]:
        c.execute("INSERT OR REPLACE INTO detail VALUES (?,?,?,?,?)", (acptno, kind, d["shares"], d["amount"], d["purpose"]))
        c.commit()
    c.close()
    time.sleep(pause)
    return d


def purpose_tag(p: str, kind: str) -> str:
    if kind == "소각결정" or "소각" in (p or ""):
        return "소각"
    if any(w in (p or "") for w in ("임직원", "보상", "상여", "RSU", "스톡", "우리사주", "성과")):
        return "임직원보상"
    if any(w in (p or "") for w in ("주주가치", "주가안정", "주가 안정", "주주환원", "가치제고")):
        return "주주가치"
    return "기타"


def ratio_pct(d: dict, listed: float, price: float) -> float:
    """상장주식 대비 % — 주식수가 있으면 그걸로, 없으면 금액 ÷ (가격 × 상장주식)."""
    if not listed:
        return 0.0
    if d.get("shares"):
        return d["shares"] / listed * 100
    if d.get("amount") and price:
        return d["amount"] / (price * listed) * 100
    return 0.0


def todays_events(date: str, prev_date: str = None, db: str = KIND_DB) -> list:
    """자사주 취득(결정·신탁) 공시 [(acptno, 날짜, 시각, 코드, 회사, 종류)] — 오늘 것 + 전 거래일 장후(15:20~) 것."""
    c = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
    try:
        rows = c.execute("SELECT acptno, date, time, code, company, title FROM disc WHERE code IS NOT NULL AND "
                         "(date=? OR (date=? AND time>='15:20')) ORDER BY date, time", (date, prev_date or "")).fetchall()
    except sqlite3.Error:
        return []
    finally:
        c.close()
    out = []
    for a, d, t, code, comp, title in rows:
        k = kind_of(title)
        if k in ("취득결정", "취득신탁"):
            out.append((a, d, t, code, comp, k))
    return out


def judge(d: dict, kind: str, listed: float, price: float, down: bool = False):
    """→ (통과?, 사유). down=하락장(국면<35%)이면 시총 DOWN_MIN_CAP_EOK억 미만은 패스."""
    tag = purpose_tag(d.get("purpose", ""), kind)
    r = ratio_pct(d, listed, price)
    if tag != "주주가치":
        return False, f"목적 '{d.get('purpose') or '?'}'({tag})"
    if r < MIN_RATIO:
        return False, f"규모 {r:.2f}% < {MIN_RATIO:g}%"
    cap = listed * price / 1e8 if listed and price else 0
    if down and cap and cap < DOWN_MIN_CAP_EOK:
        return False, f"하락장 시총 {cap:,.0f}억 < {DOWN_MIN_CAP_EOK:,}억"
    return True, f"{kind} · {d.get('purpose')} · 상장주식 {r:.1f}%({(d.get('amount') or 0) / 1e8:,.0f}억)"
