"""
coin_market_context.py — 코인 시장 상황 판단 재료 (2026-10-07)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

cbot이 "일손실 한도로 멈춘 뒤 4시간 점검"이나 매수 후보 고를 때 쓰는 시장 정보.
텔레그램 코인뉴스 수집이 끊긴(10-03 계정 사고) 뒤의 대안으로, 계정이 필요 없는
공개 자료만 쓴다.
  1. 업비트 공식 경보 — 유의종목 지정, 주의 경보(가격 급등락·거래량 급등·
     입금량 급등·해외 가격차·소수계정 집중)
  2. 시장 전체 흐름 — 업비트 원화마켓 전 종목의 기준시점 대비 등락 중앙값·상승 비율
     (BTC 하나만 보는 것보다 "다 같이 빠지는지"를 직접 본다)
  3. 코인 뉴스 헤드라인 — 해외 RSS 제목(최근 N시간) + AI 한 줄 판단(참고용)
판단(재개/유지)은 가격 숫자로만 하고, 뉴스·AI는 대장이 보는 참고 정보.
"""
import datetime
import email.utils
import re
import xml.etree.ElementTree as ET

BASE_URL = "https://api.upbit.com/v1"
NEWS_FEEDS = [
    ("Cointelegraph", "https://cointelegraph.com/rss"),
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
]
CAUTION_KO = {
    "PRICE_FLUCTUATIONS": "가격급등락",
    "TRADING_VOLUME_SOARING": "거래량급등",
    "DEPOSIT_AMOUNT_SOARING": "입금량급등",
    "GLOBAL_PRICE_DIFFERENCES": "해외가격차",
    "CONCENTRATION_OF_SMALL_ACCOUNTS": "소수계정집중",
}


# ── 1. 업비트 경보 ─────────────────────────────────────────
def parse_market_flags(items: list) -> dict:
    """market/all?isDetails=true 응답 → {market: {"warning": bool, "cautions": [한글]}}.
    예전 형식(market_warning: "CAUTION")과 새 형식(market_event) 둘 다 처리."""
    out = {}
    for it in items or []:
        m = it.get("market", "")
        if not m.startswith("KRW-"):
            continue
        ev = it.get("market_event") or {}
        warning = bool(ev.get("warning")) or str(it.get("market_warning", "")).upper() == "CAUTION"
        cautions = [CAUTION_KO.get(k, k) for k, v in (ev.get("caution") or {}).items() if v]
        out[m] = {"warning": warning, "cautions": cautions}
    return out


def fetch_market_flags(session) -> dict:
    try:
        res = session.get(f"{BASE_URL}/market/all", params={"isDetails": "true"}, timeout=5).json()
        return parse_market_flags(res if isinstance(res, list) else [])
    except Exception as e:
        print(f"⚠️ 업비트 경보 조회 실패: {e}")
        return {}


# ── 2. 시장 전체 흐름 ─────────────────────────────────────
def fetch_krw_prices(session, markets: list = None) -> dict:
    """원화마켓 현재가 {market: price}. markets 생략 시 전 종목."""
    try:
        if markets is None:
            res = session.get(f"{BASE_URL}/market/all", params={"isDetails": "false"}, timeout=5).json()
            markets = [x["market"] for x in res if x.get("market", "").startswith("KRW-")]
        out = {}
        for i in range(0, len(markets), 100):
            chunk = markets[i:i + 100]
            data = session.get(f"{BASE_URL}/ticker", params={"markets": ",".join(chunk)}, timeout=5).json()
            for t in data if isinstance(data, list) else []:
                p = float(t.get("trade_price") or 0)
                if p > 0:
                    out[t["market"]] = p
        return out
    except Exception as e:
        print(f"⚠️ 원화마켓 시세 조회 실패: {e}")
        return {}


def breadth(base: dict, now: dict) -> dict:
    """기준시점 대비 전 종목 등락: 중앙값(%), 상승 비율(%), 비교 종목 수."""
    chg = sorted((now[m] / base[m] - 1) * 100 for m in base if m in now and base[m] > 0)
    if not chg:
        return {"median": None, "up_pct": None, "n": 0}
    return {"median": chg[len(chg) // 2], "up_pct": sum(c > 0 for c in chg) / len(chg) * 100, "n": len(chg)}


# ── 3. 뉴스 헤드라인 ──────────────────────────────────────
def parse_rss(xml_text: str, source: str, since: datetime.datetime) -> list:
    """RSS → [(시각, 제목, 출처)] (since 이후, 최신순)."""
    out = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        pub = item.findtext("pubDate") or ""
        try:
            ts = email.utils.parsedate_to_datetime(pub)
            ts = ts.astimezone(datetime.timezone.utc).replace(tzinfo=None)
        except (TypeError, ValueError):
            continue
        if title and ts >= since:
            out.append((ts, re.sub(r"\s+", " ", title), source))
    return out


def fetch_headlines(session, hours: float = 6, limit: int = 6) -> list:
    since = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) - datetime.timedelta(hours=hours)
    items = []
    for source, url in NEWS_FEEDS:
        try:
            r = session.get(url, timeout=6, headers={"User-Agent": "Mozilla/5.0"})
            items += parse_rss(r.text, source, since)
        except Exception as e:
            print(f"⚠️ 뉴스 RSS 실패({source}): {e}")
    items.sort(key=lambda x: x[0], reverse=True)
    return items[:limit]


def ai_judgement(llm, model: str, summary: str, headlines: list) -> str:
    """가격 숫자 + 헤드라인으로 '이벤트성 순간 급락 vs 추세적 하락' 한 줄(참고용)."""
    if llm is None:
        return ""
    heads = "\n".join(f"- {t}" for _, t, _ in headlines) or "- (최근 헤드라인 없음)"
    prompt = ("코인 시장 점검. 아래 숫자와 최근 해외 뉴스 제목만 보고, 이번 하락이 "
              "'일시적 이벤트성(뉴스·발언·청산 등 순간 충격)'인지 '추세적 하락'인지 판단해 "
              "한국어 한 줄(60자 이내)로만 답해. 모르면 '판단 어려움'이라고 해.\n"
              f"[숫자] {summary}\n[뉴스]\n{heads}")
    try:
        res = llm.messages.create(model=model, max_tokens=120,
                                  messages=[{"role": "user", "content": prompt}])
        text = "".join(getattr(b, "text", "") for b in getattr(res, "content", []))
        return text.strip().splitlines()[0][:120] if text.strip() else ""
    except Exception as e:
        print(f"⚠️ AI 시장판단 실패: {e}")
        return ""
