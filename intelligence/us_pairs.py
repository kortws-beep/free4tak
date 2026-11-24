"""
us_pairs.py — 우리 관심그룹(분야) ↔ 미국 짝꿍 대표주, 밤사이 미국 등락으로 '오늘 분야 순풍·역풍' (2026-11-25, 대장)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
대장: "관심종목 섹터 지도와 연동해서 미장 대표 종목 2~3개씩 — 야간 변화를 보여주고 우리 지도에 별표 정도만."
(11-24: 일본·대만·한국이 미국과 제일 비슷하게 움직임 — 반도체 공급망 국가)
→ 분야마다 미국 짝꿍 2~3개(대장 OK 11-25)의 '어젯밤 미국 정규장 등락'(전일 종가 대비) 평균을 내서
  ⭐ 순풍(+2%↑) / 🔻 역풍(-2%↓)을 섹터맵에 표시. 근거(짝꿍별 등락)도 같이.
  · 시세: 야후 일봉(정규장 종가끼리 비교 — 한국 아침엔 미국장이 끝나 있음). 애프터·24시간 거래는 안 봄.
  · 표에 없는 분야는 표시 없음. 짝꿍 고치려면 PAIRS만 바꾸면 됨.
검증: backtest/us_pairs_validate.py (미국 짝꿍 밤 등락 → 다음 날 우리 분야 시가·종가, 시장 대비).
"""
import datetime
import time

import requests

UA = {"User-Agent": "Mozilla/5.0"}
CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{}?interval=1d&range={}"
STRONG = 2.0          # ⭐/🔻 기준(짝꿍 평균 등락 %)
TTL = 600

PAIRS = {
    "S7 대형주": ["NVDA", "MU", "SKHY"],
    "반도체_HBM패키징": ["NVDA", "MU", "AMD"],
    "반도체_전공정장비": ["AMAT", "LRCX", "KLAC"],
    "반도체_소재부품": ["AMAT", "LRCX", "ENTG"],
    "반도체_후공정테스트": ["TER", "AMKR"],
    "반도체_기판PCB": ["AVGO", "DELL", "SMCI"],
    "반도체_MLCC": ["AVGO", "DELL", "SMCI"],
    "반도체_유리기판": ["INTC", "AMD"],
    "전력_변압기": ["GEV", "ETN", "VRT"],
    "전력_전선케이블": ["GEV", "ETN", "VRT"],
    "원전_대형시공": ["CEG", "OKLO", "SMR"],
    "원전_기자재": ["CEG", "OKLO", "SMR"],
    "신재생_ESS": ["FLNC", "TSLA"],
    "2차전지_셀양극재": ["TSLA", "ALB", "QS"],
    "2차전지_소재전고체": ["TSLA", "ALB", "QS"],
    "조선": ["HII", "GD"],
    "조선_기자재엔진": ["HII", "GD"],
    "방산": ["LMT", "RTX", "NOC"],
    "우주": ["SPCX", "RKLB", "ASTS"],
    "통신": ["ANET", "GLW", "COHR"],
    "로봇_휴머노이드": ["TSLA", "SYM", "TER"],
    "로봇_피지컬AI자동화": ["TSLA", "SYM", "TER"],
    "로봇_주관심": ["TSLA", "SYM", "TER"],
    "바이오_대형비만": ["LLY", "NVO"],
    "바이오_신약": ["XBI", "AMGN"],
    "화장품": ["ELF", "EL"],
    "철강": ["NUE", "STLD"],
}

# ★ 11-25 검증(backtest/us_pairs_validate.py, 2024-11~2026-11 2년): 짝꿍 밤 등락 ↔ 다음 날 분야 '시장 대비 시가 갭' 상관이
#   0.15 이상인 분야만 ⭐/🔻를 진하게(믿을 만함). 나머지(화장품·철강·통신·조선·전선 등)는 짝꿍이 안 맞아 '참고'로만.
#   전체: ⭐날 분야가 시장보다 종가 +0.35%p(뒷반 +0.60) · 🔻날 -0.19%p, 효과는 거의 시가 갭에서 끝남(장중은 0 근처).
RELIABLE = {"반도체_전공정장비", "반도체_후공정테스트", "S7 대형주", "반도체_HBM패키징", "반도체_기판PCB",
            "반도체_유리기판", "반도체_소재부품", "신재생_ESS"}

_cache = {"t": 0, "data": None}


def tickers() -> list:
    return sorted({t for v in PAIRS.values() for t in v})


def daily(ticker: str, rng: str = "10d", get=None) -> list:
    """[(미국 날짜 'YYYY-MM-DD', 종가)] 오래된→최신. 실패하면 []."""
    get = get or (lambda url: requests.get(url, headers=UA, timeout=8).json())
    try:
        r = get(CHART.format(ticker, rng))["chart"]["result"][0]
        off = int((r.get("meta") or {}).get("gmtoffset", -18000))
        out = []
        for ts, c in zip(r.get("timestamp") or [], r["indicators"]["quote"][0].get("close") or []):
            if c is not None:
                d = datetime.datetime.fromtimestamp(ts + off, datetime.timezone.utc).strftime("%Y-%m-%d")
                out.append((d, float(c)))
        return out
    except Exception as e:
        print(f"⚠️ [미국짝꿍] {ticker} 조회 실패: {e}")
        return []


def last_move(rows: list):
    """일봉 → (미국 날짜, 전일 대비 %) — 마지막 두 종가. 모자라면 None."""
    if len(rows) < 2:
        return None
    (_, p0), (d1, p1) = rows[-2], rows[-1]
    return d1, (p1 / p0 - 1) * 100 if p0 else 0.0


def mark(avg) -> str:
    if avg is None:
        return ""
    return "⭐" if avg >= STRONG else ("🔻" if avg <= -STRONG else "")


def signals(get=None, now: float = None) -> dict:
    """{"date": 미국 날짜, "groups": {분야: {"avg", "mark", "pairs": [(티커, %)]}}} — 10분 캐시."""
    now = now or time.time()
    if get is None and _cache["data"] and now - _cache["t"] < TTL:
        return _cache["data"]
    moves = {}
    for t in tickers():
        m = last_move(daily(t, get=get))
        if m:
            moves[t] = m
    dates = sorted({d for d, _ in moves.values()})
    us_day = dates[-1] if dates else ""
    groups = {}
    for g, ts in PAIRS.items():
        pr = [(t, round(moves[t][1], 2)) for t in ts if t in moves and moves[t][0] == us_day]
        avg = round(sum(p for _, p in pr) / len(pr), 2) if pr else None
        groups[g] = {"avg": avg, "mark": mark(avg), "pairs": pr, "reliable": g in RELIABLE}
    data = {"date": us_day, "groups": groups}
    if get is None:
        _cache.update(t=now, data=data)
    return data


if __name__ == "__main__":
    s = signals()
    print("미국 기준일", s["date"])
    for g, v in sorted(s["groups"].items(), key=lambda x: -(x[1]["avg"] if x[1]["avg"] is not None else -99)):
        print(f"{v['mark'] or '  '} {g:16s} {v['avg'] if v['avg'] is not None else '-':>6} {v['pairs']}")
