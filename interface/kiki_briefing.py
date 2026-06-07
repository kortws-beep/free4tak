"""
kiki_briefing.py — KiKi 브리핑 모듈
================================================================
모닝/저녁 브리핑, 글로벌 시장, Finnhub 이벤트, AI 전망
kiki.py에서 import해서 사용
"""
import os
import sys
import asyncio
import datetime
import sqlite3

_here = os.path.dirname(os.path.abspath(__file__))
_base = os.path.dirname(_here)
for _d in ["core", "intelligence", "interface", "bots", ""]:
    _p = os.path.join(_base, _d)
    if _p not in sys.path:
        sys.path.insert(0, _p)

for _ep in [os.path.join(_here, ".env"), os.path.join(_base, ".env")]:
    if os.path.exists(_ep):
        from dotenv import load_dotenv
        load_dotenv(_ep, override=True)
        break

from anthropic import Anthropic
from common_utils import now_kst
try:
    from kiki_data import get_today_realized_all
except Exception:
    def get_today_realized_all(): return {'nbot':0,'sbot':0,'cbot':0}
# ★ read_state/write_state — 봇 이름 기반 래퍼
# kiki.py on_ready에서 주입받거나 자체 구현 사용
import os as _os2
_BOT_STATE_FILES = {
    "nbot": "bot_state.json",
    "sbot": "sbot_state.json",
    "cbot": "cbot_state.json",
}

def read_state(bot: str = "nbot") -> dict:
    from common_utils import read_state as _rs
    fname = _BOT_STATE_FILES.get(bot, "bot_state.json")
    fpath = _os2.path.join(_base, fname)
    return _rs(fpath, default={})

def write_state(bot: str = "nbot", state: dict = None):
    from common_utils import write_state as _ws
    fname = _BOT_STATE_FILES.get(bot, "bot_state.json")
    fpath = _os2.path.join(_base, fname)
    _ws(fpath, state or {})

def update_state(bot: str = "nbot", **kwargs):
    state = read_state(bot)
    state.update(kwargs)
    write_state(bot, state)

# 봇 상태 파일 경로
_base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BOT_STATE_FILES = {
    "nbot": os.path.join(_base, "bot_state.json"),
    "sbot": os.path.join(_base, "sbot_state.json"),
    "cbot": os.path.join(_base, "cbot_state.json"),
}

# kiki.py에서 주입받는 전역 변수
bot         = None   # discord.Client 인스턴스
send_long   = None   # kiki.py에서 주입
CHANNEL_ID  = 0
BOT_STATE_FILES = {}

# ai는 주입받거나 직접 생성
_ai_instance = None

def _get_ai():
    global _ai_instance, ai
    if ai is not None:
        return ai
    if _ai_instance is None:
        try:
            from kiki import AIAssistant
            _ai_instance = AIAssistant()
        except Exception as e:
            print(f"AIAssistant 생성 오류: {e}")
            return None
    return _ai_instance

ai = None

DEFAULT_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")

def _get_finnhub_events(days_ahead: int = 3) -> dict:
    """
    Finnhub API로 실적 발표 + 경제지표 캘린더 조회.
    반환: {
        "earnings": [{"symbol", "date", "hour", "eps_est", "rev_est"}],
        "economic": [{"time_kst", "event", "estimate", "prev"}],
    }
    """
    import requests as _req
    from datetime import datetime, timedelta, timezone

    api_key = os.getenv("FINNHUB_API_KEY", "")
    if not api_key:
        return {"earnings": [], "economic": []}

    today  = datetime.now().strftime("%Y-%m-%d")
    end_dt = (datetime.now() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    # 주목 종목 (반도체/AI/빅테크)
    watchlist = {
        "NVDA","AMD","INTC","TSM","QCOM","AVGO","MU","ASML","AMAT","MRVL",
        "AAPL","MSFT","GOOGL","META","AMZN","TSLA","SMCI","ARM","PLTR",
    }

    result = {"earnings": [], "economic": []}

    # 실적 발표
    try:
        res = _req.get(
            "https://finnhub.io/api/v1/calendar/earnings",
            params={"from": today, "to": end_dt, "token": api_key},
            timeout=10,
        ).json()
        for e in res.get("earningsCalendar", []):
            sym = e.get("symbol", "")
            if sym in watchlist:
                hour_str = e.get("hour", "")
                hour_kor = "장마감후" if hour_str == "amc" else ("장전" if hour_str == "bmo" else hour_str)
                result["earnings"].append({
                    "symbol":  sym,
                    "date":    e.get("date", ""),
                    "hour":    hour_kor,
                    "eps_est": e.get("epsEstimate"),
                    "rev_est": e.get("revenueEstimate"),
                })
    except Exception as e:
        print(f"Finnhub 실적 오류: {e}")

    # 미국 경제지표 (오늘만)
    try:
        res2 = _req.get(
            "https://finnhub.io/api/v1/calendar/economic",
            params={"from": today, "to": today, "token": api_key},
            timeout=10,
        ).json()
        kst = timezone(timedelta(hours=9))
        for e in res2.get("economicCalendar", []):
            if e.get("country") != "US":
                continue
            # 중요 이벤트만
            event_nm = e.get("event", "")
            important = any(k in event_nm for k in [
                "Fed", "CPI", "PPI", "Employment", "GDP", "FOMC",
                "Interest Rate", "Retail", "NFP", "Unemployment",
            ])
            if not important:
                continue
            try:
                utc_t = datetime.strptime(
                    e.get("time", ""), "%Y-%m-%d %H:%M:%S"
                ).replace(tzinfo=timezone.utc)
                kst_t = utc_t.astimezone(kst).strftime("%H:%M")
            except Exception:
                kst_t = "?"
            result["economic"].append({
                "time_kst": kst_t,
                "event":    event_nm,
                "estimate": e.get("estimate"),
                "prev":     e.get("prev"),
            })
    except Exception as e:
        print(f"Finnhub 경제지표 오류: {e}")

    return result


# 경제지표 영→한 번역 사전
_ECON_KR = {
    "Philadelphia Fed Manufacturing Index": "필라델피아 연준 제조업지수",
    "Philly Fed Business Conditions":       "필라델피아 연준 사업여건",
    "Philly Fed CAPEX Index":               "필라델피아 설비투자지수",
    "Philly Fed Employment":                "필라델피아 연준 고용",
    "Philly Fed New Orders":                "필라델피아 연준 신규주문",
    "ADP Employment Change":                "ADP 민간고용변화",
    "ADP Employment Change Weekly":         "ADP 주간고용변화",
    "Fed Waller Speech":                    "연준 월러 발언",
    "Fed Paulson Speech":                   "연준 폴슨 발언",
    "Fed Barr Speech":                      "연준 바 발언",
    "Fed Venable Speech":                   "연준 베너블 발언",
    "FOMC Minutes":                         "FOMC 의사록",
    "Initial Jobless Claims":               "신규 실업수당청구",
    "CPI":                                  "소비자물가지수(CPI)",
    "PPI":                                  "생산자물가지수(PPI)",
    "Core CPI":                             "근원 CPI",
    "GDP":                                  "국내총생산(GDP)",
    "Retail Sales":                         "소매판매",
    "Nonfarm Payrolls":                     "비농업 고용",
    "Unemployment Rate":                    "실업률",
    "Interest Rate Decision":               "금리 결정",
    "Pending Home Sales":                   "잠정 주택판매",
    "Redbook YoY":                          "레드북 소매판매(전년비)",
    "API Crude Oil Stock Change":           "API 원유재고 변화",
    "6-Week Bill Auction":                  "6주 국채 경매",
}

def _translate_event(event_name: str) -> str:
    """이벤트명 영→한 번역 (사전 우선, 없으면 원문)"""
    for en, kr in _ECON_KR.items():
        if en.lower() in event_name.lower():
            return kr
    return event_name


def _format_finnhub_events(events: dict) -> str:
    """Finnhub 이벤트를 브리핑용 문자열로 포맷"""
    lines = []

    earnings = events.get("earnings", [])
    if earnings:
        lines.append("📊 **주요 실적 발표**")
        for e in earnings[:5]:
            eps = f"EPS예상 ${e['eps_est']:.2f}" if e.get("eps_est") else ""
            lines.append(f"  • {e['symbol']} | {e['date']} {e['hour']} {eps}")

    economic = events.get("economic", [])
    if economic:
        lines.append("📅 **미국 주요 이벤트 (KST)**")
        for e in economic[:5]:
            est  = f"예상:{e['estimate']}" if e.get("estimate") else ""
            name = _translate_event(e['event'])
            lines.append(f"  • {e['time_kst']} {name} {est}")

    return "\n".join(lines)


def _claude_call(llm, **kwargs):
    """Claude API 호출 + 과부하 시 재시도 (최대 3회)"""
    import time as _t
    from datetime import datetime as _dt
    # 현재 날짜를 system에 주입 (없으면 추가)
    if "system" not in kwargs:
        kwargs["system"] = (
            f"현재 날짜는 {_dt.now().strftime('%Y년 %m월 %d일')}입니다. "
            "이것은 실제 현재 시점이며 미래가 아닙니다. "
            "제공되는 모든 데이터는 실제 현재 데이터입니다."
        )
    for _retry in range(3):
        try:
            return llm.messages.create(**kwargs)
        except Exception as e:
            if "overloaded" in str(e).lower() or "529" in str(e):
                wait = 30 * (_retry + 1)
                print(f"⚠️ Claude 과부하 — {wait}초 후 재시도 ({_retry+1}/3)")
                _t.sleep(wait)
            else:
                raise
    raise RuntimeError("Claude API 3회 재시도 실패")


def _get_global_market() -> dict:
    """
    미국 주요 지수 조회 — 웹서치 기반 (Yahoo Finance 차단 대응).
    반환: {
        'sox':  {'name': '필라델피아 반도체', 'price': 11302.52, 'rate': -2.47, 'date': '06/08'},
        'ndx':  {...}, 'spx':  {...}, 'dji':  {...},
    }
    """
    import datetime as _dt
    import re as _re

    today = _dt.datetime.now().strftime("%Y-%m-%d")
    result = {}

    try:
        _ai = _get_ai()
        if not _ai:
            return result

        import datetime as _dt2
        today_mmdd = _dt.datetime.now().strftime("%m/%d")
        yesterday  = (_dt.datetime.now() - _dt.timedelta(days=1)).strftime("%Y-%m-%d")

        # ★ 지수별 개별 웹서치 — 한 번에 묶으면 파싱 누락됨
        queries = {
            'sox': (f"필라델피아 반도체 지수 SOX 어제 종가 등락률 {yesterday}", "sox", "필라델피아 반도체"),
            'ndx': (f"나스닥 종합 지수 NASDAQ 어제 종가 등락률 {yesterday}", "ndx", "나스닥"),
            'spx': (f"S&P500 지수 어제 종가 등락률 {yesterday}", "spx", "S&P500"),
            'dji': (f"다우존스 지수 DOW 어제 종가 등락률 {yesterday}", "dji", "다우"),
        }

        llm = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

        for key, (query, rkey, name) in queries.items():
            try:
                raw = _ai._web_search_korea(query)
                if not raw or raw == "검색 결과 없음":
                    continue

                prompt_text = (
                    f"검색결과에서 {name} 지수의 어제({yesterday}) 종가와 등락률만 추출해줘.\n"
                    f"반드시 아래 형식으로만 답해 (숫자만, 다른 말 금지):\n"
                    f"가격|등락률\n"
                    f"예시: 13500.25|+2.34\n\n"
                    f"검색결과:\n{raw[:500]}"
                )
                res = _claude_call(llm,
                    model=DEFAULT_MODEL, max_tokens=30,
                    messages=[{"role": "user", "content": prompt_text}],
                )
                parsed = res.content[0].text.strip()
                parts = parsed.split("|")
                if len(parts) == 2:
                    price = float(parts[0].replace(",", "").strip())
                    rate  = float(parts[1].replace("%", "").replace("+", "").strip())
                    if price > 0:
                        result[rkey] = {
                            'name':  name,
                            'price': price,
                            'rate':  rate,
                            'date':  today_mmdd,
                        }
                        print(f"  ✅ {name}: {price:,.2f} ({rate:+.2f}%)")
            except Exception as e:
                print(f"  ⚠️ {name} 조회 오류: {e}")
                continue

    except Exception as e:
        print(f"⚠️ 글로벌 지수 웹서치 오류: {e}")

    return result


def _format_global_market(market: dict) -> str:
    """글로벌 시장 데이터를 브리핑용 문자열로 포맷"""
    lines = []
    for key in ['sox', 'ndx', 'spx', 'dji']:
        d = market.get(key, {})
        if not d or d['price'] == 0:
            continue
        rate  = d['rate']
        emoji = '🔴' if rate < -1 else ('🟡' if rate < 0 else '🟢')
        lines.append(
            f"  {emoji} {d['name']}: {d['price']:,.2f} ({rate:+.2f}%) [{d['date']}]"
        )
    return '\n'.join(lines)


def _get_us_events(today_date: str) -> str:
    """
    어젯밤 미국 주요 이벤트 발표 결과 웹서치.
    모닝 브리핑(08:00)에서 호출 — 이미 발표된 결과를 가져옴.
    """
    try:
        from datetime import datetime, timedelta
        yesterday = (datetime.strptime(today_date, "%Y-%m-%d")
                     - timedelta(days=1)).strftime("%Y-%m-%d")

        # 연준 발언 결과 + 경제지표 결과 + 실적 결과
        # ★ Finnhub에서 어제 실제 발표 종목만 동적으로 쿼리 생성 (NVDA 하드코딩 제거)
        queries = [
            f"미국 경제지표 발표 결과 고용 CPI 연준 {yesterday}",
        ]

        finnhub_yesterday = _get_finnhub_events(days_ahead=1)
        earnings = finnhub_yesterday.get("earnings", [])
        if earnings:
            # 어제 발표된 종목만 검색 (최대 5개)
            syms = " ".join(e["symbol"] for e in earnings[:5])
            queries.append(f"{syms} 실적 발표 결과 어닝콜 EPS {yesterday}")
        else:
            # 실적 발표 없으면 어젯밤 나스닥/반도체 동향만 검색
            queries.append(f"나스닥 반도체 주요 이슈 {yesterday}")

        all_results = []
        for q in queries:
            _ai = _get_ai()
            r = _ai._web_search_korea(q) if _ai else ""
            if r and r != "검색 결과 없음":
                all_results.append(r[:400])

        if not all_results:
            return ""

        combined = "\n".join(all_results)

        # AI로 핵심만 추출
        llm = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        res = _claude_call(llm, 
            model=DEFAULT_MODEL, max_tokens=600,
            messages=[{"role": "user", "content": (
                f"다음은 어젯밤({yesterday}) 미국 시장 결과입니다. (현재 연도: 2026년, 실제 데이터임)\n"
                f"아래 항목별로 핵심만 한국어로 요약해줘 (각 1~2줄, 없으면 생략):\n"
                f"• 연준 발언: (매파/비둘기파 여부와 핵심 메시지)\n"
                f"• 주요 경제지표: (예상 대비 결과)\n"
                f"• 실적 발표: (어닝 서프라이즈/쇼크 여부)\n"
                f"• 나스닥/반도체 영향: (한국 시장 영향 전망)\n\n"
                f"검색 결과:\n{combined}"
            )}],
        )
        return res.content[0].text.strip()[:800]
    except Exception as e:
        print(f"us_events 오류: {e}")
        return ""''


def _get_foreign_flow_summary() -> str:
    """외국인 수급 요약 (KIS API)"""
    try:
        import sqlite3 as _sq
        db = os.path.join(os.path.dirname(__file__), 'backtest', 'data', 'backtest_data.db')
        if not os.path.exists(db):
            return ''
        conn = _sq.connect(db, timeout=10)
        # 5월 누적 외국인 순매수
        import datetime as _dt
        may_start = _dt.datetime.now().strftime('%Y-05-01')
        rows = conn.execute("""
            SELECT SUM(foreign_qty) as total
            FROM daily_flow
            WHERE date >= ?
        """, (may_start,)).fetchone()
        conn.close()
        total = rows[0] if rows and rows[0] else 0
        if total == 0:
            return ''
        emoji = '🔴' if total < 0 else '🟢'
        return f'{emoji} 외국인 5월누적: {total:+,}주'
    except Exception:
        return ''


def _build_briefing_msg() -> str:
    """모닝 브리핑 메시지 생성"""
    now    = now_kst()
    state  = read_state("nbot")
    status = state.get("last_status", {})
    cbot_state  = read_state("cbot")
    cbot_status = cbot_state.get("last_status", {})

    # ★ 날짜 명시로 최신 데이터 강제
    today_date = now.strftime("%Y-%m-%d")
    searches = [
        # 미국장 → Yahoo Finance 직접 조회로 대체 (웹서치 제거)
        ("💱 환율",      "달러 원화 환율 오늘",                       "korea"),
        ("🪙 코인",      f"비트코인 이더리움 가격 시세 오늘 {today_date}",          "korea"),
        ("🌤️ 날씨",     None,                                                      "weather"),
    ]

    now_str = now.strftime("%m/%d %H:%M")
    msg  = f"🌅 **[영암9 모닝 브리핑] {now_str}**\n━━━━━━━━━━━━━━━━━━━━\n"

    # ★ 미국 주요 지수 (Yahoo Finance 직접 조회)
    global_mkt = _get_global_market()

    # ★ 코스피/코스닥 네이버 직접 조회
    try:
        import requests as _req
        _headers = {"User-Agent": "Mozilla/5.0"}
        _kospi_data = []
        for _sym, _name in [("KOSPI", "코스피"), ("KOSDAQ", "코스닥")]:
            _res = _req.get(
                f"https://m.stock.naver.com/api/index/{_sym}/basic",
                headers=_headers, timeout=10
            ).json()
            _price = _res.get("closePrice", "0").replace(",", "")
            _rate  = float(_res.get("fluctuationsRatio", 0) or 0)
            _status = _res.get("marketStatus", "")
            _emoji = "🔴" if _rate < 0 else ("🟢" if _rate > 0 else "⚪")
            if _status == "PREOPEN":
                _kospi_data.append(f"  {_emoji} {_name}: {float(_price):,.2f} (개장 전)")
            else:
                _kospi_data.append(f"  {_emoji} {_name}: {float(_price):,.2f} ({_rate:+.2f}%)")
        if _kospi_data:
            msg += "🇰🇷 **국내 지수**\n" + "\n".join(_kospi_data) + "\n\n"
    except Exception as _e:
        print(f"코스피 조회 오류: {_e}")
    if global_mkt:
        msg += f"🇺🇸 **미국 지수** ({global_mkt.get('ndx', {}).get('date', '')} 기준)\n"
        msg += _format_global_market(global_mkt) + "\n"
        # SOX 방향 기반 오늘 반도체 전망
        sox_rate = global_mkt.get('sox', {}).get('rate', 0)
        if sox_rate <= -2:
            msg += f"  ⚠️ 필라델피아 반도체 {sox_rate:+.2f}% → 오늘 반도체 약세 주의\n"
        elif sox_rate >= 2:
            msg += f"  ✅ 필라델피아 반도체 {sox_rate:+.2f}% → 오늘 반도체 강세 기대\n"
        msg += "\n"

    # ★ Finnhub 실적/이벤트 캘린더 (웹서치보다 정확)
    finnhub_events = _get_finnhub_events(days_ahead=2)
    # ★ Finnhub 예정 이벤트
    finnhub_str = _format_finnhub_events(finnhub_events)
    if finnhub_str:
        msg += finnhub_str + "\n\n"

    # ★ 어젯밤 발표 결과 (웹서치) — Finnhub과 별개로 항상 실행
    us_events = _get_us_events(today_date)
    if us_events:
        msg += f"📰 **어젯밤 발표 결과**\n{us_events}\n\n"

    # ★ 외국인 수급
    foreign_summary = _get_foreign_flow_summary()
    if foreign_summary:
        msg += f"💰 **수급**: {foreign_summary}\n\n"

    # ★ 검색 결과 수집 (네이버 우선)
    search_results = {}
    for label, query, stype in searches:
        if stype == "weather":
            search_results[label] = ""
        elif stype == "global":
            _ai = _get_ai()
            result = _ai._web_search_korea(query) if _ai else ""
            if result == "검색 결과 없음":
                result = _ai._web_search_global(query) if _ai else ""
            search_results[label] = result
        else:
            _ai = _get_ai()
            search_results[label] = _ai._web_search_korea(query) if _ai else ""
    # ★ Claude가 전체 검색 결과에서 정확한 수치 추출
    all_raw = ""
    for lbl, val in search_results.items():
        if val and val != "검색 결과 없음":
            all_raw += f"\n[{lbl}]\n{val[:400]}\n"
    extracted_map = {}
    if all_raw:
        try:
            llm = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
            _res = _claude_call(llm, 
                model=DEFAULT_MODEL, max_tokens=500,
                messages=[{"role":"user","content":(
                    f"아래 검색 결과에서 핵심 수치만 정확히 추출해줘.\n"
                    f"오늘: {today_date}\n"
                    f"검색 결과에 없는 수치는 절대 만들지 말고 '정보없음' 표시.\n\n"
                    f"{all_raw}\n\n"
                    "반드시 아래 항목명 그대로 사용:\n"
                    "미국장: [다우/나스닥 수치]\n"
                    "환율: [USD/KRW 환율]\n"
                    "코인: [BTC, ETH 가격]\n"
                    "코스피선물: [선물 가격과 방향]\n"
                    "(없는 정보는 '정보없음' 표시)"
                )}],
            )
            for line in _res.content[0].text.strip().split("\n"):
                if ":" in line:
                    k, v = line.split(":", 1)
                    extracted_map[k.strip()] = v.strip()
        except Exception as _e:
            print(f"브리핑 추출 오류: {_e}")
    llm_brief = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    for label, query, stype in searches:
        if stype == "weather":
            _ai = _get_ai()
            first = "\n" + _ai._get_weather_region() if _ai else ""
        else:
            raw_val = search_results.get(label, "")
            if not raw_val or raw_val == "검색 결과 없음":
                first = "조회 실패"
            else:
                try:
                    _r = _claude_call(llm_brief, 
                        model=DEFAULT_MODEL, max_tokens=60,
                        messages=[{"role":"user","content":(
                            f"다음 검색결과를 한국어 1줄(40자 이내)로 요약해줘.\n"
                            f"수치(가격,환율,지수)는 정확히 그대로 유지.\n"
                            f"없는 수치는 절대 만들지 마.\n"
                            f"환율은 반드시 '1달러=X원' 형식(1,000원 이상)으로만 표현해.\n"
                            f"0.00X 같은 역환율이 나오면 무시하고 '정보없음'으로 표시해.\n"
                            f"검색결과:\n{raw_val[:500]}"
                        )}],
                    )
                    first = _r.content[0].text.strip()[:100]
                except Exception:
                    first = raw_val.split("[요약]")[-1].split("\n")[0].strip()[:80]
        msg += f"{label}: {first}\n"
    active = state.get("active_sectors", [])
    if active:
        msg += f"🏭 강세 업종: {' | '.join(active)}\n"

    msg += "━━━━━━━━━━━━━━━━━━━━\n"
    paused_str = "⏸️" if state.get("paused") else "▶️"
    msg += f"📈 단타봇: {paused_str} | 기준:{state.get('score_enter', 55)}점"
    if status:
        msg += f" | 주문가능:{status.get('psbl_cash', 0):,}원"
    # ★ sbot 추가
    sbot_state2  = read_state("sbot")
    sbot_status2 = sbot_state2.get("last_status", {})
    sbot_pos2    = sbot_status2.get("positions", 0)
    sbot_profit2 = sbot_status2.get("total_profit", 0)
    sbot_paused2 = "⏸️" if sbot_state2.get("paused") else "▶️"
    msg += (f"\n📊 스윙봇: {sbot_paused2} | 포지션:{sbot_pos2}개"
            f"{f' | 평가손익:{sbot_profit2:+,}원' if sbot_profit2 else ''}\n")
    msg += (f"\n🪙 코인봇: {'⏸️' if cbot_state.get('paused') else '▶️'} | "
            f"KRW:{cbot_status.get('krw', 0):,}원\n")


    # ★ AI 종합 전망 + nbot 자동 조정
    try:
        sox_rate = global_mkt.get("sox", {}).get("rate", 0) if global_mkt else 0
        ndx_rate = global_mkt.get("ndx", {}).get("rate", 0) if global_mkt else 0
        earnings_str = _format_finnhub_events(finnhub_events) if finnhub_events else ""
        foreign_str  = _get_foreign_flow_summary()
        # ★ dict 방지 — 모든 변수 str 강제 변환
        earnings_str = str(earnings_str) if earnings_str else ""
        foreign_str  = str(foreign_str)  if foreign_str  else ""
        sox_rate = float(sox_rate) if sox_rate else 0.0
        ndx_rate = float(ndx_rate) if ndx_rate else 0.0

        llm_adj = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        adj_res = _claude_call(llm_adj, 
            model=DEFAULT_MODEL, max_tokens=300,
            messages=[{"role": "user", "content": (
                f"오늘 날짜: {today_date}\n"
                f"필라델피아 반도체: {sox_rate:+.2f}%\n"
                f"나스닥: {ndx_rate:+.2f}%\n"
                f"이벤트:\n{earnings_str}\n"
                f"수급: {foreign_str}\n\n"
                "위 정보를 바탕으로 다음 형식으로 답해줘:\n"
                "1. 내일 한국 반도체/증시 한줄 전망 (30자 이내)\n"
                "2. nbot 오전(09-11시) 매수 임계치 권장: 70~85 중 숫자만\n"
                "3. nbot 오후(11-15시) 매수 임계치 권장: 65~80 중 숫자만\n"
                "4. 주의사항 한줄 (30자 이내)\n"
                "형식 예시:\n"
                "전망: NVDA 실적 주목, 반도체 변동성 확대\n"
                "오전임계치: 75\n"
                "오후임계치: 70\n"
                "주의: NVDA 쇼크 시 즉시 손절 대응 필요"
            )}],
        )
        ai_text = adj_res.content[0].text.strip()

        # 파싱
        am_thresh = 70
        pm_thresh = 70
        forecast  = ""
        caution   = ""
        for line in ai_text.split("\n"):
            if "오전임계치:" in line:
                try: am_thresh = int(line.split(":")[1].strip())
                except: pass
            elif "오후임계치:" in line:
                try: pm_thresh = int(line.split(":")[1].strip())
                except: pass
            elif "전망:" in line:
                forecast = line.split(":", 1)[1].strip()
            elif "주의:" in line:
                caution = line.split(":", 1)[1].strip()

        msg += f"\n━━━━━━━━━━━━━━━━━━━━\n"
        msg += f"🤖 **AI 전망**: {forecast}\n"
        if caution:
            msg += f"⚠️ **주의**: {caution}\n"
        msg += f"⚙️ **nbot 권장**: 오전 {am_thresh}점 / 오후 {pm_thresh}점\n"

        # ★ nbot 자동 조정 (bot_state.json에 권장 임계치 저장)
        state = read_state("nbot")
        state["am_score_recommend"] = am_thresh
        state["pm_score_recommend"] = pm_thresh
        state["market_forecast"]    = forecast
        state["forecast_date"]      = today_date
        write_state("nbot", state)
        msg += f"✅ nbot 권장 임계치 저장 완료\n"

    except Exception as e:
        print(f"AI 전망 오류: {e}")

    # ★ 뉴스 감성 섹션 추가
    try:
        import sqlite3 as _sl, os as _os
        from datetime import datetime as _dt
        _db = _os.path.join(_base, "intelligence", "news_sentiment.db")
        if _os.path.exists(_db):
            _today = _dt.now().strftime("%Y%m%d")
            _conn = _sl.connect(_db, timeout=3)
            _conn.execute("PRAGMA query_only=ON")
            # 오늘 데이터 없으면 가장 최근 날짜 사용
            _latest = _conn.execute(
                "SELECT date FROM news_sentiment ORDER BY date DESC LIMIT 1"
            ).fetchone()
            _use_date = _today if _latest and _latest[0] == _today else (_latest[0] if _latest else _today)
            _rows = _conn.execute("""
                SELECT keyword,
                       AVG(CASE sentiment WHEN '긍정' THEN 1
                           WHEN '부정' THEN -1 ELSE 0 END) as score,
                       COUNT(*) as cnt
                FROM news_sentiment
                WHERE date=?
                GROUP BY keyword
                ORDER BY score DESC
            """, (_use_date,)).fetchall()
            _conn.close()
            if _rows:
                msg += "━━━━━━━━━━━━━━━━━━━━\n"
                msg += "📰 **오늘 테마 뉴스 감성**\n"
                for _kw, _score, _cnt in _rows[:5]:
                    _emoji = "▲" if _score > 0.2 else ("▼" if _score < -0.2 else "●")
                    msg += f"  {_emoji} {_kw}: {_score:+.2f} ({_cnt}건)\n"
    except Exception as _e:
        print(f"뉴스 감성 브리핑 오류: {_e}")

    msg += "📌 오늘도 좋은 장 되세요! 💪"
    return msg


def _build_evening_briefing_msg() -> str:
    """저녁 브리핑 메시지 생성"""
    now    = now_kst()
    state  = read_state("nbot")
    status = state.get("last_status", {})
    cbot_state  = read_state("cbot")
    cbot_status = cbot_state.get("last_status", {})

    today_date = now.strftime("%Y-%m-%d")
    searches = [
        ("📈 코스피/코스닥", "코스피 코스닥 오늘 마감 시황",                       "korea"),
        ("🏆 오늘의 주도주", "오늘 급등 테마 주도주",                              "korea"),
        ("🪙 코인시황",     f"bitcoin ethereum crypto price today {today_date}",   "global"),
        ("💱 환율",         f"USD KRW exchange rate today {today_date}",           "global"),

    ]

    now_str = now.strftime("%m/%d %H:%M")
    msg  = f"🌆 **[영암9 저녁 브리핑] {now_str}**\n━━━━━━━━━━━━━━━━━━━━\n"
    # ★ 코스피/코스닥 네이버 API 직접 조회 (모닝 브리핑과 동일)
    try:
        import requests as _req
        _headers = {"User-Agent": "Mozilla/5.0"}
        _kospi_data = []
        for _sym, _name in [("KOSPI", "코스피"), ("KOSDAQ", "코스닥")]:
            _res = _req.get(
                f"https://m.stock.naver.com/api/index/{_sym}/basic",
                headers=_headers, timeout=10
            ).json()
            _price = _res.get("closePrice", "0").replace(",", "")
            _rate  = float(_res.get("fluctuationsRatio", 0) or 0)
            _emoji = "🔴" if _rate < 0 else ("🟢" if _rate > 0 else "⚪")
            _kospi_data.append(f"{_emoji} {_name}: {float(_price):,.2f} ({_rate:+.2f}%)")
        if _kospi_data:
            msg += "🇰🇷 **국내 지수**\n" + " | ".join(_kospi_data) + "\n"
        # 검색 목록에서 코스피 제거
        searches = [(l,q,s) for l,q,s in searches if "코스피" not in l]
    except Exception as _e:
        print(f"저녁브리핑 코스피 조회 오류: {_e}")

    # ★ 검색 결과 수집 (네이버 우선)
    search_results = {}
    for label, query, stype in searches:
        if stype == "weather":
            search_results[label] = ""
        elif stype == "global":
            _ai = _get_ai()
            result = _ai._web_search_korea(query) if _ai else ""
            if result == "검색 결과 없음":
                result = _ai._web_search_global(query) if _ai else ""
            search_results[label] = result
        else:
            _ai = _get_ai()
            search_results[label] = _ai._web_search_korea(query) if _ai else ""

    llm_brief = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    for label, query, stype in searches:
        if stype == "weather":
            _ai = _get_ai()
            first = "\n" + _ai._get_weather_region() if _ai else ""
        else:
            raw_val = search_results.get(label, "")
            if not raw_val or raw_val == "검색 결과 없음":
                first = "조회 실패"
            else:
                try:
                    _r = _claude_call(llm_brief, 
                        model=DEFAULT_MODEL, max_tokens=60,
                        messages=[{"role":"user","content":(
                            f"다음 검색결과를 한국어 1줄(40자 이내)로 요약해줘.\n"
                            f"수치(가격,환율,지수)는 정확히 그대로 유지.\n"
                            f"없는 수치는 절대 만들지 마.\n"
                            f"환율은 반드시 '1달러=X원' 형식(1,000원 이상)으로만 표현해.\n"
                            f"0.00X 같은 역환율이 나오면 무시하고 '정보없음'으로 표시해.\n"
                            f"검색결과:\n{raw_val[:500]}"
                        )}],
                    )
                    first = _r.content[0].text.strip()[:100]
                except Exception:
                    first = raw_val.split("[요약]")[-1].split("\n")[0].strip()[:80]
        msg += f"{label}: {first}\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n"

    # ★ 모든 봇의 오늘 실현손익 합산 (단타+스윙+종가+코인)
    realized = get_today_realized_all()
    nbot_p = realized.get("nbot", 0)
    sbot_p = realized.get("sbot", 0)
    cbot_p = realized.get("cbot", 0)

    if nbot_p:
        msg += f"📈 단타봇: {int(nbot_p):+,}원\n"
    if sbot_p:
        msg += f"📊 스윙봇: {int(sbot_p):+,}원\n"
    if cbot_p:
        msg += f"🪙 코인봇: {int(cbot_p):+,}원\n"
    total = nbot_p + sbot_p + cbot_p
    if total or any([nbot_p, sbot_p, cbot_p]):
        msg += f"━━━━━━━━━━━━━━━━━━━━\n"
        msg += f"💰 **오늘 합계: {int(total):+,}원**\n"
    else:
        # 평가손익 표시 (실현 매매가 없을 때)
        msg += f"📈 단타봇 평가: {status.get('total_profit', 0):+,}원\n"
        msg += f"🪙 코인봇 평가: {cbot_status.get('total_profit', 0):+,}원\n"

    msg += "📌 내일도 좋은 장 되세요! 🌙"
    return msg


async def _send_briefing(target):
    loop = asyncio.get_event_loop()
    msg  = await loop.run_in_executor(None, _build_briefing_msg)
    if hasattr(target, "send"):
        await send_long(target, msg)


async def _send_evening_briefing(target):
    loop = asyncio.get_event_loop()
    msg  = await loop.run_in_executor(None, _build_evening_briefing_msg)
    if hasattr(target, "send"):
        await send_long(target, msg)


async def cmd_briefing(ctx):
    await ctx.send("🌅 **모닝 브리핑 준비 중...**")
    await _send_briefing(ctx)


async def cmd_evening_briefing(ctx):
    await ctx.send("🌆 **저녁 브리핑 준비 중...**")
    await _send_evening_briefing(ctx)
