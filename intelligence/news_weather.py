"""
news_weather.py — 날씨판 뉴스 층: 판단 시각 직전 뉴스를 읽어 시장 변동 반영 (2026-11-17, 대장 "기본 데이터만 말고 뉴스도")
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
대장: "1차 판정 때 전일 12시~당일 07시 뉴스, 2차는 07시~11시30분 뉴스를 반영하자 — 기본 데이터만으로 판단하지 말고 시장 변동도."
→ 그 시간 창의 시장 뉴스를 모아 Claude(Haiku)에게 읽혀 '오늘 우리 시장에 미칠 영향'을 -2~+2 점과 이유로 받는다.
  재료(줄마다 출처):
   · 네이버 뉴스 검색(시장 키워드 — 증시·코스피·뉴욕증시·연준·금리·환율·유가·외국인·엔비디아·반도체·관세·전쟁·중국 등, 최신순)
   · [특징주] 뉴스(feature_news.db, 리나 수집)
   · MTN 아침 방송 요약(mtn_morning.db — 1차만, 05:54 받아쓰기)
  결과는 money_weather.db news_board(날짜·차수·창·점수·요약·사건·뜨는/식는 분야·건수·출처)에 남김.
날씨판 반영(검증 전이라 보수적): 1차 뉴스 점수 ≤ -2(분명한 악재)면 주식·코인 태세를 한 칸 더 조심. 좋은 뉴스로 올려 주지는 않음.
2차(11:30 창)는 기록·표시만(2차 점검 자체가 아직 봇 제어 안 함). 점수 기록이 쌓이면 지수 성적과 맞춰 검증 후 비중을 정함.
실행: venv/bin/python intelligence/news_weather.py [1|2]   (지금 시각 기준 해당 차수 창으로 한 번)
"""
import datetime
import email.utils
import json
import os
import re
import sqlite3
import sys

import requests

_HERE = os.path.dirname(os.path.abspath(__file__))
_BASE = os.path.dirname(_HERE)
MW_DB = os.path.join(_HERE, "money_weather.db")
FEATURE_DB = os.path.join(_HERE, "feature_news.db")
MTN_DB = os.path.join(_HERE, "mtn_morning.db")
MODEL = os.getenv("NEWS_WEATHER_MODEL", "claude-haiku-5-5")
KEYWORDS = ["증시", "코스피", "코스닥", "뉴욕증시", "나스닥", "연준", "금리", "환율", "유가", "외국인 순매도", "외국인 순매수",
            "엔비디아", "반도체", "관세", "전쟁", "중국 경기", "비트코인", "공매도", "금융위", "IPO"]


def window(phase: int, now: datetime.datetime = None) -> tuple:
    """1차: 전일 12:00 ~ 당일 07:00 · 2차: 당일 07:00 ~ 11:30 (대장 지정)."""
    now = now or datetime.datetime.now()
    d = now.date()
    if phase == 1:
        return datetime.datetime.combine(d - datetime.timedelta(days=1), datetime.time(12, 0)), \
            datetime.datetime.combine(d, datetime.time(7, 0))
    return datetime.datetime.combine(d, datetime.time(7, 0)), datetime.datetime.combine(d, datetime.time(11, 30))


def _clean(s: str) -> str:
    s = re.sub(r"<[^>]+>", "", s or "")
    return (s.replace("&quot;", '"').replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
            .replace("&apos;", "'").strip())


def naver_news(start, end) -> list:
    cid, sec = os.getenv("NAVER_CLIENT_ID"), os.getenv("NAVER_CLIENT_SECRET")
    if not (cid and sec):
        return []
    seen, out = set(), []
    for kw in KEYWORDS:
        try:
            r = requests.get("https://openapi.naver.com/v1/search/news.json",
                             headers={"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": sec},
                             params={"query": kw, "display": 50, "sort": "date"}, timeout=10).json()
        except Exception as e:
            print(f"⚠️ [뉴스층] '{kw}' 검색 실패: {e}")
            continue
        for it in r.get("items", []):
            try:
                t = email.utils.parsedate_to_datetime(it.get("pubDate")).replace(tzinfo=None)
            except Exception:
                continue
            title = _clean(it.get("title"))
            key = re.sub(r"\W", "", title)[:40]
            if start <= t < end and key and key not in seen:
                seen.add(key)
                out.append((t.strftime("%m-%d %H:%M"), title, _clean(it.get("description"))[:120], f"네이버뉴스({kw})"))
    return out


def feature_news(start, end) -> list:
    try:
        c = sqlite3.connect(f"file:{FEATURE_DB}?mode=ro", uri=True, timeout=5)
        rows = c.execute("SELECT pub_ts, title FROM news WHERE pub_ts >= ? AND pub_ts < ? ORDER BY pub_ts",
                         (start.strftime("%Y-%m-%d %H:%M"), end.strftime("%Y-%m-%d %H:%M"))).fetchall()
        c.close()
        return [(str(ts)[5:16], t, "", "[특징주](feature_news)") for ts, t in rows]
    except Exception:
        return []


def mtn_summary(day: str) -> str:
    try:
        c = sqlite3.connect(f"file:{MTN_DB}?mode=ro", uri=True, timeout=5)
        r = c.execute("SELECT summary FROM mtn_morning WHERE date=?", (day,)).fetchone()
        c.close()
        return (r[0] or "")[:2500] if r else ""
    except Exception:
        return ""


def judge(items: list, mtn: str, phase: int, start, end) -> dict:
    """Claude Haiku로 영향 판단 → dict. 실패하면 {"score": 0, "error": ...}."""
    if not items and not mtn:
        return {"score": 0, "summary": "창 안 뉴스 없음", "events": [], "hot": [], "cold": []}
    lines = "\n".join(f"- [{t}] {title}" + (f" — {d}" if d else "") + f" ({src})" for t, title, d, src in items[:220])
    today = datetime.date.today()
    prompt = f"""오늘은 {today.isoformat()}({'월화수목금토일'[today.weekday()]})이야. 한국 주식·코인 자동매매 봇의 '시장 날씨' 판단을 돕는 중이야.
아래는 {start:%m-%d %H:%M} ~ {end:%m-%d %H:%M} 사이 뉴스 제목(출처 포함){'와 MTN 아침 방송 요약' if mtn else ''}이야.
이 뉴스들이 '오늘 한국 증시(코스피·코스닥)와 코인 시장'에 미칠 영향을 판단해 줘.
- 점수: -2(분명한 큰 악재: 전쟁·폭락·긴급 규제·대형 쇼크) · -1(악재 우세) · 0(혼재·영향 작음) · +1(호재 우세) · +2(분명한 큰 호재)
- 개별 종목 소식은 시장 전체 영향이 클 때만. 스포츠·연예 등 무관한 뉴스는 무시. 뉴스에 없는 내용은 지어내지 마.
JSON만 답해: {{"score": 정수, "summary": "한두 문장", "events": [{{"title": "핵심 사건", "impact": "+ 또는 -", "source": "뉴스 출처 표기 그대로"}}] (최대 5개),
"hot": ["오늘 주목받을 분야"] (최대 4), "cold": ["약할 분야"] (최대 4)}}

뉴스:
{lines}
""" + (f"\nMTN 아침 방송 요약:\n{mtn}\n" if mtn else "")
    try:
        import anthropic
        res = anthropic.Anthropic().messages.create(model=MODEL, max_tokens=4000, output_config={"effort": "medium"},
                                                    messages=[{"role": "user", "content": prompt}])
        if res.stop_reason == "refusal":
            return {"score": 0, "error": "거절"}
        txt = "".join(b.text for b in res.content if b.type == "text")
        m = re.search(r"\{.*\}", txt, re.S)
        d = json.loads(m.group(0)) if m else {}
        d["score"] = max(-2, min(2, int(d.get("score", 0))))
        return d
    except Exception as e:
        return {"score": 0, "error": str(e)[:200]}


def run(phase: int, now: datetime.datetime = None) -> dict:
    now = now or datetime.datetime.now()
    start, end = window(phase, now)
    items = naver_news(start, end) + feature_news(start, end)
    mtn = mtn_summary(now.date().isoformat()) if phase == 1 else ""
    d = judge(items, mtn, phase, start, end)
    d.update(phase=phase, start=start.strftime("%m-%d %H:%M"), end=end.strftime("%m-%d %H:%M"), n=len(items),
             sources=sorted({s.split("(")[0] for *_, s in items} | ({"MTN 아침 요약"} if mtn else set())))
    try:
        c = sqlite3.connect(MW_DB, timeout=10)
        c.execute("CREATE TABLE IF NOT EXISTS news_board (date TEXT, phase INTEGER, win TEXT, score INTEGER, summary TEXT, "
                  "detail TEXT, n INTEGER, sources TEXT, model TEXT, created_at TEXT, PRIMARY KEY(date, phase))")
        c.execute("INSERT OR REPLACE INTO news_board VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (now.date().isoformat(), phase, f"{d['start']}~{d['end']}", d.get("score", 0), d.get("summary", ""),
                   json.dumps({k: d.get(k) for k in ("events", "hot", "cold", "error")}, ensure_ascii=False), d["n"],
                   ", ".join(d["sources"]), MODEL, now.isoformat(timespec="seconds")))
        c.commit()
        c.close()
    except Exception as e:
        print(f"⚠️ [뉴스층] 기록 실패: {e}")
    return d


def today_score(day: str, phase: int = 1):
    try:
        c = sqlite3.connect(f"file:{MW_DB}?mode=ro", uri=True, timeout=5)
        r = c.execute("SELECT score, summary FROM news_board WHERE date=? AND phase=?", (day, phase)).fetchone()
        c.close()
        return r
    except Exception:
        return None


def format_news(d: dict) -> str:
    ev = "\n".join(f"   {'＋' if e.get('impact') == '+' else '－'} {e.get('title', '')} ({e.get('source', '')})"
                   for e in (d.get("events") or [])[:5])
    hc = (f"\n   🔥 주목: {', '.join(d.get('hot') or [])}" if d.get("hot") else "") + \
         (f"  · 🧊 약세: {', '.join(d.get('cold') or [])}" if d.get("cold") else "")
    err = f"\n   ⚠️ 판단 실패: {d['error']}" if d.get("error") else ""
    return (f"📰 **뉴스 층 {d.get('phase')}차** ({d.get('start')}~{d.get('end')}, 뉴스 {d.get('n', 0)}건 · 출처 "
            f"{', '.join(d.get('sources') or []) or '-'}) — 점수 {d.get('score', 0):+d}\n   {d.get('summary', '')}"
            + (f"\n{ev}" if ev else "") + hc + err)


if __name__ == "__main__":
    sys.path.insert(0, os.path.join(_BASE, "core"))
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_BASE, ".env"))
    ph = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1] in ("1", "2") else 1
    print(format_news(run(ph)))
