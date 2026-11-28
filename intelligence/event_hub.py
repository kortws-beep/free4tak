"""
event_hub.py — 이벤트 모으기·분류 허브 → 대시보드 📅 달력 (2026-11-28, 대장 "이벤트 수집 방법을 정해야 해")
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
대장: "0) 지난 2~3년 이벤트 정리 1) 세이브티커 자료의 이벤트 2) MTN 아침정리를 매일 수집해서 — 매년·매분기·매월 있는
이벤트인지, 특정(돌발) 이벤트인지 구분해 대응해야." + "대시보드에 1년치 달력 — 키움에서 '오늘 살 게 없네' 할 때
다가오는 이벤트를 보고 미리 선점."
저장소는 자금시장 날씨판의 이벤트 표(intelligence/money_weather.db · events, 출처 없으면 저장 안 함) 그대로 쓰고,
칸 3개를 더함: recurrence(주기: 매년·매분기·매월·매주·돌발) · sectors(관련 섹터) · origin(어디서 왔나) · detail(숫자·원문).
  ① 규칙 달력: intelligence/event_calendar.py(CES·블프·학회·연휴·실적시즌·연말배당…) → 매년/매분기
  ② 세이브티커 PDF(대장이 ~/다운로드에 받는 'YYYY년 M월 D일 (요일).pdf')의 [경제지표] — 전날 밤 미국 지표(★·실제/예상/이전).
     ⚠️ PDF 끝 '주요 일정' 칸은 11월 PDF에도 7월 13일 내용이 그대로라(세이브티커 틀 고정) 안 씀.
  ③ MTN 아침 받아쓰기(logs/mtn_morning/날짜.txt) → AI가 '방송에서 말한 일정·사건'만 뽑음(원문 문장을 출처로, 지어내기 금지).
  ④ 검증 결과(backtest/event_sector_validate.py --runup, 11-28): '미리 사기 창'(✅ 통과 · ❌ 피할 것)을 달력에 같이 표시.
매매 신호 아님 — 대장 판단 재료. 실행: venv/bin/python intelligence/event_hub.py [--rules] [--saveticker] [--mtn 날짜|all]
"""
import datetime
import glob
import json
import os
import re
import sqlite3
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_BASE = os.path.dirname(_HERE)
for _d in ("core", "intelligence"):
    if os.path.join(_BASE, _d) not in sys.path:
        sys.path.insert(0, os.path.join(_BASE, _d))

DB_PATH = os.path.join(_HERE, "money_weather.db")
PDF_DIR = os.path.expanduser("~/다운로드")
MTN_DIR = os.path.join(_BASE, "logs", "mtn_morning")
STATE = os.path.join(_HERE, "event_hub_state.json")
REC = {"yearly": "매년", "quarterly": "매분기", "monthly": "매월", "weekly": "매주", "oneoff": "돌발"}

# ④ 11-28 검증 결과(섹터 ETF 10년, KODEX200 대비, 가짜 날짜 대조) — (이벤트, 섹터, 매수 b거래일 전, 매도 s거래일 전, 판정, 근거)
WINDOWS = [
    ("CES", "반도체", 20, 3, "ok", "+3.3%p · 11년 중 9번 · 종목 단위(반도체_ 62종목)는 7/11"),
    ("CES", "IT", 10, 1, "ok", "+2.1%p · 11년 중 9번"),
    ("지스타", "게임", 10, 1, "ok", "+3.3%p · 9번 중 7번"),
    ("광군절", "아모레퍼시픽", 20, 3, "maybe", "평균 +6%p지만 이긴 해 8/12(몇 해 쏠림)"),
    ("JPM헬스케어", "헬스케어", 20, 3, "maybe", "평균 +3%p지만 이긴 해 6/11"),
    ("실적시즌", "반도체", 15, 3, "maybe", "+1%p · 47번 중 60%"),
    ("블랙프라이데이", "화장품·한국콜마", 15, 3, "avoid", "미리 사면 -3.6~-8%p(이긴 해 18~36%)"),
    ("광군절", "한국콜마", 15, 3, "avoid", "-2~-4%p(이긴 해 25~50%)"),
    ("618", "화장품·아모레퍼시픽", 20, 3, "avoid", "-3.4~-7.4%p(이긴 해 8~36%)"),
    ("ESMO", "바이오·헬스케어", 10, 1, "avoid", "-3%p(이긴 해 10~20%)"),
    ("연말배당", "KT&G·KT·LG유플러스(배당락)", 10, 1, "avoid", "배당락 하루 -3~-8% — 단기 방패로 들지 말 것"),
]
RULE_REC = {"실적시즌": "quarterly"}
RULE_SECTORS = {"CES": "반도체·IT·로봇", "GTC": "반도체·로봇", "JPM헬스케어": "바이오·헬스케어", "AACR": "바이오",
                "ASCO": "바이오", "ESMO": "바이오", "블랙프라이데이": "화장품·의류OEM·유통", "광군절": "화장품·면세",
                "618": "화장품·면세", "지스타": "게임", "설연휴": "여행·유통", "추석연휴": "여행·유통",
                "연말배당": "은행·보험·통신·담배(배당락)", "실적시즌": "전 섹터(삼성전자 잠정실적 무렵)"}
ECON_REC = [(r"실업수당|EIA|시추기수|천연가스 재고|원유 재고", "weekly"), (r"GDP|분기", "quarterly"),
            (r"FOMC|금리 ?결정|베이지북|의사록", "monthly"),
            (r"CPI|PCE|PPI|고용|비농업|실업률|소매판매|PMI|ISM|주택|내구재|산업생산|소비자 ?신뢰|심리|JOLTS|무역", "monthly"),
            (r"국채 입찰", "weekly")]


def _conn():
    import money_weather
    c = money_weather._conn()
    cols = {r[1] for r in c.execute("PRAGMA table_info(events)")}
    for col in ("recurrence", "sectors", "origin", "detail"):
        if col not in cols:
            c.execute(f"ALTER TABLE events ADD COLUMN {col} TEXT DEFAULT ''")
    c.commit()
    return c


def add(date, kind, title, source, recurrence="", sectors="", origin="", detail="", time_kst="", impact="") -> bool:
    """출처 없으면 저장 안 함(날씨판 규칙 그대로). 이미 있으면 빈 칸만 채움."""
    if not (date and kind and title and source):
        return False
    c = _conn()
    try:
        cur = c.execute("INSERT OR IGNORE INTO events (date, time_kst, kind, title, scope, impact, source, created_at, recurrence, "
                        "sectors, origin, detail) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (date, time_kst, kind, title, sectors, impact, source,
                         datetime.datetime.now().isoformat(timespec="seconds"), recurrence, sectors, origin, detail))
        if cur.rowcount == 0:
            c.execute("UPDATE events SET recurrence=COALESCE(NULLIF(recurrence,''),?), sectors=COALESCE(NULLIF(sectors,''),?), "
                      "origin=COALESCE(NULLIF(origin,''),?), detail=COALESCE(NULLIF(detail,''),?) WHERE date=? AND kind=? AND title=?",
                      (recurrence, sectors, origin, detail, date, kind, title))
        c.commit()
        return cur.rowcount > 0
    finally:
        c.close()


# ── ① 규칙 달력 ─────────────────────────────────────────
def seed_rules(y0=None, y1=None) -> int:
    import event_calendar
    today = datetime.date.today()
    y0, y1 = y0 or today.year - 2, y1 or today.year + 1
    n = 0
    for name, dates in event_calendar.events(y0, y1).items():
        for d in dates:
            n += add(d.isoformat(), "정기", name, "event_calendar.py(규칙·공식 일정, 학회·전시는 ±수일)",
                     recurrence=RULE_REC.get(name, "yearly"), sectors=RULE_SECTORS.get(name, ""), origin="규칙", impact="중간")
    try:   # FOMC·금통위도 주기 칸 채우기
        c = _conn()
        c.execute("UPDATE events SET recurrence='monthly', origin=COALESCE(NULLIF(origin,''),'매크로 일정') "
                  "WHERE kind IN ('FOMC','금통위') AND COALESCE(recurrence,'')=''")
        c.commit()
        c.close()
    except Exception as e:
        print(f"⚠️ FOMC 주기 채우기 실패: {e}")
    return n


# ── ② 세이브티커 PDF ────────────────────────────────────
def _pdf_date(path):
    m = re.search(r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일", os.path.basename(path))
    return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def parse_saveticker(text: str, pdf_day: datetime.date) -> list:
    """[경제지표] 칸 → [(date, time, 제목, 별개수, detail)] — 12시 이후 발표는 PDF 전날 밤(한국 시각)."""
    out = []
    sec = re.search(r"\[경제지표\](.*?)(?:\n\s*\n\s*\n|전일 요약|오늘의 소식)", text, re.S)
    if not sec:
        return out
    for line in sec.group(1).splitlines():
        m = re.match(r"\s*(\d{1,2}):(\d{2})\s*[-–]\s*([^-–]+?)\s*[-–]\s*(.+?)\s*(★+)\s*(.*)$", line)
        if not m:
            continue
        hh, mm, country, title, stars, rest = m.groups()
        day = pdf_day - datetime.timedelta(days=1) if int(hh) >= 12 else pdf_day
        out.append((day.isoformat(), f"{int(hh):02d}:{mm}", f"{country.strip()} {re.sub(r'\\s+', ' ', title).strip()}",
                    len(stars), re.sub(r"\s+", " ", rest).strip()))
    return out


def _econ_rec(title):
    for pat, rec in ECON_REC:
        if re.search(pat, title):
            return rec
    return "monthly"


def ingest_saveticker(pdf_dir=PDF_DIR) -> int:
    st = _state()
    done = set(st.get("pdf", []))
    n = 0
    for path in sorted(glob.glob(os.path.join(pdf_dir, "*년*월*일*.pdf"))):
        base = os.path.basename(path)
        day = _pdf_date(path)
        if not day or base in done:
            continue
        try:
            text = subprocess.run(["pdftotext", "-layout", path, "-"], capture_output=True, text=True, timeout=60).stdout
        except Exception as e:
            print(f"⚠️ PDF 읽기 실패 {base}: {e}")
            continue
        for date, tm, title, stars, detail in parse_saveticker(text, day):
            if stars < 2:
                continue
            n += add(date, "경제지표", title, f"세이브티커 {base}", recurrence=_econ_rec(title), origin="세이브티커",
                     detail=detail, time_kst=tm, impact="높음" if stars >= 3 else "중간")
        done.add(base)
    st["pdf"] = sorted(done)
    _save_state(st)
    return n


# ── ③ MTN 아침 받아쓰기 ─────────────────────────────────
def mtn_prompt(text: str, today: str) -> str:
    return (
        f"오늘은 {today}(한국 시각)야. 아래는 MTN 아침 증시 방송을 받아쓴 원문이야(음성 인식이라 오타 있음).\n"
        "방송에서 **실제로 말한** 시장 일정·사건만 뽑아 JSON 배열로만 답해. 설명 문장 없이 JSON만.\n"
        "각 항목: {\"date\":\"YYYY-MM-DD\"(방송에서 말한 날짜 — '오늘'은 위 날짜, '이번 주 목요일'처럼 말하면 계산, 날짜를 모르면 null),"
        " \"title\":\"짧은 이름(예: 엔비디아 실적 발표, 美 10월 CPI, 젠슨황 복귀)\","
        " \"kind\":\"실적|경제지표|통화정책|정책·규제|학회·전시|쇼핑|IPO·상장|돌발|기타\","
        " \"recurrence\":\"yearly|quarterly|monthly|weekly|oneoff\"(정해진 주기로 반복되면 그 주기, 그때만 일어난 사건이면 oneoff),"
        " \"sectors\":\"관련 섹터(방송이 말한 것만, 없으면 빈 문자열)\","
        " \"quote\":\"근거가 된 원문 문장 그대로(40자 안팎)\"}\n"
        "규칙: 방송에 없는 일정·날짜는 절대 지어내지 마. 광고·회원 모집은 빼. 이미 지나간 사건도 방송이 그날 시장 재료로 말했으면 넣어.\n"
        "같은 일정이 여러 번 나오면 한 번만. 없으면 [].\n\n[받아쓰기]\n" + text[:60000])


def _ask(prompt: str) -> str:
    import anthropic
    from common_utils import extract_claude_text
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    # ★ 11-28 실측: 받아쓰기 4.8만 토큰 입력에 6000이면 생각(thinking)만 하다 끝남(본문 0) → 넉넉히 + 스트리밍(긴 응답 타임아웃 방지)
    with client.messages.stream(model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5"), max_tokens=20000,
                                messages=[{"role": "user", "content": prompt}]) as stream:
        res = stream.get_final_message()
    return extract_claude_text(res)


def parse_items(raw: str) -> list:
    m = re.search(r"\[.*\]", raw or "", re.S)
    if not m:
        return []
    try:
        items = json.loads(m.group(0))
    except ValueError:
        return []
    return [x for x in items if isinstance(x, dict) and x.get("title") and x.get("quote")]


def ingest_mtn(day: str, ask=None) -> int:
    files = [f for f in glob.glob(os.path.join(MTN_DIR, f"{day}*.txt")) if "전문가" not in f]
    if not files:
        return 0
    with open(files[0], encoding="utf-8") as f:
        text = f.read()
    if len(text) < 2000:
        return 0
    items = parse_items((ask or _ask)(mtn_prompt(text, day)))
    n = 0
    for x in items:
        date = x.get("date") or day
        if not re.match(r"\d{4}-\d{2}-\d{2}$", str(date)):
            date = day
        rec = x.get("recurrence") if x.get("recurrence") in REC else "oneoff"
        if x.get("kind") in ("경제지표", "통화정책") and any(re.search(p, str(x["title"])) for p, _ in ECON_REC):
            rec = _econ_rec(str(x["title"]))      # FOMC·CPI·고용 같은 정기 지표를 AI가 '돌발'로 붙이는 실수 바로잡기
        n += add(date, x.get("kind") or "기타", str(x["title"])[:80], f"MTN 아침 {day}: \"{str(x['quote'])[:80]}\"",
                 recurrence=rec, sectors=str(x.get("sectors") or "")[:60], origin="MTN", impact="중간")
    st = _state()
    st.setdefault("mtn", [])
    if day not in st["mtn"]:
        st["mtn"].append(day)
    _save_state(st)
    return n


def pending_mtn_days() -> list:
    done = set(_state().get("mtn", []))
    days = sorted({os.path.basename(f)[:10] for f in glob.glob(os.path.join(MTN_DIR, "*.txt")) if "전문가" not in f})
    return [d for d in days if d not in done]


# ── 상태·조회 ───────────────────────────────────────────
def _state() -> dict:
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_state(st: dict):
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)
    os.replace(tmp, STATE)


def windows(today: datetime.date = None, ahead: int = 120) -> list:
    """앞으로 ahead일 안 이벤트의 '미리 사기 창' [{event, date, sector, buy, sell, verdict, why}] — 거래일 근사(주말 빼기)."""
    import event_calendar
    today = today or datetime.date.today()
    evs = event_calendar.events(today.year, today.year + 1)
    out = []
    for ev, sec, b, s, verdict, why in WINDOWS:
        for d in evs.get(ev, []):
            if not (today - datetime.timedelta(days=5) <= d <= today + datetime.timedelta(days=ahead)):
                continue
            anchor = d
            if ev == "연말배당":
                anchor = d - datetime.timedelta(days=1)
            out.append({"event": ev, "date": d.isoformat(), "sector": sec, "verdict": verdict, "why": why,
                        "buy": _back(anchor, b).isoformat(), "sell": _back(anchor, s).isoformat()})
    return sorted(out, key=lambda x: x["buy"])


# 한국장 쉬는 날(주말 외) — 미리 사기 창 날짜 계산용 근사. 새해 휴장일은 확정되면 추가.
KR_CLOSED = {"2026-12-25", "2026-12-31", "2027-01-01", "2027-02-08", "2027-02-09", "2027-03-01", "2027-05-05"}


def _back(d: datetime.date, n: int) -> datetime.date:
    """d 기준 n거래일 전(주말·KR_CLOSED 빼기)."""
    cur, k = d, 0
    while k < n:
        cur -= datetime.timedelta(days=1)
        if cur.weekday() < 5 and cur.isoformat() not in KR_CLOSED:
            k += 1
    return cur


# ── 비슷한 이벤트 묶기(화면용 — 원본은 그대로) ─────────────────
# ★ 11-28 대장 "앞으로 30일 정리가 필요" — MTN이 며칠 동안 같은 일정을 이름만 바꿔 반복 언급(젠슨황 청문회 6~7줄, 사이버먼데이 2줄)
STOP = {"美", "미국", "한국", "발표", "예정", "관련", "시행", "행사", "개막", "결정", "공개", "지수", "및", "등", "첫", "본격", "기대"}
ORIGIN_RANK = {"매크로 일정": 0, "규칙": 1, "세이브티커": 2, "MTN": 3}


def _tokens(title: str) -> set:
    t = title.replace("미국", "美").replace("젠슨 황", "젠슨황").replace("·", " ").replace("(", " ").replace(")", " ")
    return {w for w in re.split(r"[\s,/]+", t) if len(w) >= 2 and w not in STOP}


def _same(a: dict, b: dict) -> bool:
    import difflib
    na, nb = re.sub(r"[\s美]", "", a["title"].replace("미국", "")), re.sub(r"[\s美]", "", b["title"].replace("미국", ""))
    if na == nb or difflib.SequenceMatcher(None, na, nb).ratio() >= 0.6:
        return True
    return len(_tokens(a["title"]) & _tokens(b["title"])) >= 2


def cluster(events: list, days: int = 3) -> list:
    """날짜 days일 안 + 이름 비슷 → 한 묶음(서로 이어지면 다 같은 묶음, 순서 무관).
    [{title(가장 많이 겹치는 이름), start, end, n, recurrence, sectors, kind, members}] 날짜순."""
    ev = sorted(events, key=lambda x: (x["date"], ORIGIN_RANK.get(x.get("origin"), 9)))
    par = list(range(len(ev)))

    def find(i):
        while par[i] != i:
            par[i] = par[par[i]]
            i = par[i]
        return i
    dd = [datetime.date.fromisoformat(e["date"]) for e in ev]
    for i in range(len(ev)):
        for j in range(i + 1, len(ev)):
            if (dd[j] - dd[i]).days > days:
                break
            if _same(ev[i], ev[j]):
                par[find(j)] = find(i)
    groups = {}
    for i, e in enumerate(ev):
        groups.setdefault(find(i), []).append(e)
    out = []
    for ms in groups.values():
        tk = [_tokens(m["title"]) for m in ms]
        best = max(range(len(ms)), key=lambda k: (sum(len(tk[k] & tk[o]) for o in range(len(ms)) if o != k),
                                                  -ORIGIN_RANK.get(ms[k].get("origin"), 9), -len(ms[k]["title"])))
        rep = ms[best]
        secs = []
        for m in ms:
            for x in re.split(r"[,·]", m.get("sectors") or ""):
                if x.strip() and x.strip() not in secs:
                    secs.append(x.strip())
        title = re.sub(r"\s*\(.*?\)", "", rep["title"]).strip() if len(ms) > 1 else rep["title"]   # 묶음 이름엔 (비공개) 같은 꼬리 빼기
        out.append({"title": title or rep["title"], "start": min(m["date"] for m in ms), "end": max(m["date"] for m in ms),
                    "n": len(ms), "kind": rep.get("kind"), "recurrence": rep.get("recurrence"), "impact": rep.get("impact"),
                    "sectors": "·".join(secs[:4]), "members": [m["title"] for m in ms]})
    return sorted(out, key=lambda g: (g["start"], -g["n"]))


def upcoming_grouped(events: list, today: str, days: int = 30) -> list:
    end = (datetime.date.fromisoformat(today) + datetime.timedelta(days=days)).isoformat()
    pick = [e for e in events if today <= e["date"] <= end
            and (e.get("impact") == "높음" or e.get("recurrence") in ("yearly", "quarterly", "oneoff"))]
    return cluster(pick)


def calendar_data(start: str = None, end: str = None) -> dict:
    today = datetime.date.today()
    start = start or (today - datetime.timedelta(days=60)).isoformat()
    end = end or (today + datetime.timedelta(days=366)).isoformat()
    c = _conn()
    try:
        rows = c.execute("SELECT date, time_kst, kind, title, impact, source, recurrence, sectors, origin, detail FROM events "
                         "WHERE date BETWEEN ? AND ? ORDER BY date, time_kst", (start, end)).fetchall()
    finally:
        c.close()
    keys = ("date", "time", "kind", "title", "impact", "source", "recurrence", "sectors", "origin", "detail")
    evs = [dict(zip(keys, r)) for r in rows]
    by_day = {}
    for e in evs:
        by_day.setdefault(e["date"], []).append(e)
    day_groups = {d: cluster(v, days=0) for d, v in by_day.items()}      # 달력 칸: 같은 날 같은 일은 한 줄
    return {"today": today.isoformat(), "start": start, "end": end, "events": evs, "day_groups": day_groups,
            "upcoming": upcoming_grouped(evs, today.isoformat()), "windows": windows(today), "rec_names": REC}


def run_daily(ask=None) -> str:
    """리나 루프에서: 세이브티커 새 PDF + MTN 안 한 날(오늘 포함) — 결과 한 줄."""
    a = ingest_saveticker()
    m = 0
    for d in pending_mtn_days():
        try:
            m += ingest_mtn(d, ask=ask)
        except Exception as e:
            print(f"⚠️ MTN 이벤트 추출 실패 {d}: {e}")
    return f"세이브티커 {a}건 · MTN {m}건"


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_BASE, ".env"))
    args = sys.argv[1:]
    if "--rules" in args:
        print("규칙 달력", seed_rules(), "건")
    if "--saveticker" in args:
        print("세이브티커", ingest_saveticker(), "건")
    if "--mtn" in args:
        v = args[args.index("--mtn") + 1] if len(args) > args.index("--mtn") + 1 else "all"
        for d in (pending_mtn_days() if v == "all" else [v]):
            print("MTN", d, ingest_mtn(d), "건")
    if not args:
        print(run_daily())
