"""
event_sector.py — 데이봇 '이벤트 섹터 엔진'(음봉 엔진 2) (2026-11-04, 대장 결정)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
11-04: 지수는 장중 -2.6%까지 빠졌는데 미 선거(블루웨이브) 이벤트로 태양광·2차전지만 강했음(OCI홀딩스·삼성SDI +10%).
대장: "이 섹터가 오늘 조명받을 거라는 건 아침에 이미 알았잖아 — MTN 아침 종합에서 1섹터를 뽑으면 돼."
  아침: mtn_morning_brief가 방송 요약에서 오늘 조명받을 테마 1개를 우리 테마 목록(kr_theme_stocks) 이름으로 고름 → 여기 저장
        ★ 대장 "억지로 고르면 안 돼": 받아쓰기에서 그 테마·종목이 10번↑, 방송 셋 중 두 구간↑에서 나와야 인정(natural)
  ★ 11-05 손봄(대장): ① 지수 바닥 확인(저가 대비 +1%↑·20분 새 저가 없음) 뒤에만 ② 지수 저가 -5%↓ 폭락날은 쉼
     ③ 테마 하나가 아니라 방송 언급 1·2·3위 테마의 대장주(그 시각 거래대금 1위)만 후보 — 그중 새 저가 대비 반등 1등
  장중(daybot): 지수(코스피·코스닥 중 하나) 장중 -3%↓(INDEX_DROP) 처음 찍은 시각 T → 살 때까지 일반 엔진은 1슬롯 양보
                (하락장 1슬롯이면 사실상 멈춤, 정상장 3슬롯이면 2슬롯은 일반 — 대장 11-04)
                T+20분 동안은 지켜보기만 → 그 뒤 15:20까지 매분, 테마 종목 중 'T+20 이후 새 저가' 대비 +1% 넘게
                반등한 종목 중 반등 1등을 1번 매수(오늘 +15% 넘은 종목은 제외 — daybot 상한과 같음)
  매도: daybot 일반 규칙(손절 -3.5% · +5% 30% → 트레일링).
  ※ 11-04 분봉 검증(backtest/dip_engine2_minute.py): '주도주 전체'에 이 진입을 쓰면 14:30보다 나빴음(뒤 절반 -) —
     대장: "종합은 빠지고 특정 섹터만 이벤트가 있는 희귀한 경우"에 한정해 실전으로 시험(검증 데이터 없음, 결과는 SCENARIOS에).
"""
import os
import re
import json
import sqlite3
import datetime

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(_BASE, "intelligence", "mtn_morning.db")
TAG = "event_sector"
WAIT_MIN = 20
REBOUND_PCT = 1.0
MAX_CHG = 15.0
INDEX_DROP = 3.0       # ★ 11-04 대장 "아침 하락폭 -3% 이상일 때만 — 리스크를 줄일 수 있을 듯"(음봉 엔진 1은 -2% 그대로)


PANIC_DROP = 5.0       # ★ 11-05 대장 — 지수 장중 -5%↓ 폭락날은 쉼(11-05 코스피 저가 -6.1%: 태양광도 10시 뒤 다 밀림, 지투파워 손절)
BOTTOM_REBOUND = 1.0   # ★ 11-05 대장 — 지수 바닥 확인: 저가 대비 +1%↑ 반등 &
BOTTOM_QUIET_MIN = 20  #   최근 20분 새 저가 없음 → 그때부터 종목 찾기(11-05 10:01 매수 뒤 10:15~20 지수 한 번 더 무너짐)
MAX_THEMES = 3         # ★ 11-05 대장 — 오늘 최대 이벤트 하나가 아니라 방송 언급 1·2·3위 섹터의 대장주 중심


def save_theme(date: str, themes, why: str, db: str = DB_PATH):
    """themes: 테마 목록(1~3위 순) 또는 이름 하나 — '|'로 이어 저장."""
    if isinstance(themes, (list, tuple)):
        themes = "|".join(t for t in themes if t)
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE IF NOT EXISTS event_theme (date TEXT PRIMARY KEY, theme TEXT, why TEXT)")
    c.execute("INSERT OR REPLACE INTO event_theme VALUES (?,?,?)", (date, themes or "", why))
    c.commit()
    c.close()


def load_theme(date: str, db: str = DB_PATH):
    """→ ([테마 1~3위], 이유) / 없으면 ([], None)."""
    try:
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
        row = c.execute("SELECT theme, why FROM event_theme WHERE date=?", (date,)).fetchone()
        c.close()
    except sqlite3.Error:
        return [], None
    return ([t for t in row[0].split("|") if t], row[1]) if row and row[0] else ([], None)


MIN_MENTIONS = 10      # ★ 11-04 대장 "억지로 고르면 안 돼 — 자연스럽게 많이 언급되는 섹터가 있을 때만"
MIN_PARTS = 2          # 방송을 셋으로 나눠 두 군데 이상에서 나와야(한 코너에서만 몰아 나온 건 '자연스럽게'가 아님)


def mention_stats(text: str, theme: str, members: list):
    """받아쓰기에서 테마 이름·소속 종목 이름(3글자↑ — 'LS'·'NC' 같은 짧은 이름은 엉뚱하게 걸려서 뺌)이 나온 횟수,
    방송을 셋으로 나눈 구간 중 나온 구간 수 → (횟수, 구간 수)."""
    t = (text or "").replace(" ", "")
    words = [re.sub(r"\(.*?\)", "", theme).replace(" ", "")] + [n.replace(" ", "") for _, n in members]
    words = [w for w in set(words) if len(w) >= 3 or w == words[0] and len(w) >= 2]
    if not t or not words:
        return 0, 0
    third = len(t) / 3
    total, parts = 0, set()
    for w in words:
        for m in re.finditer(re.escape(w), t):
            total += 1
            parts.add(min(2, int(m.start() // third)))
    return total, len(parts)


def natural(text: str, theme: str, members: list) -> bool:
    n, parts = mention_stats(text, theme, members)
    return n >= MIN_MENTIONS and parts >= MIN_PARTS


def choose_prompt(summary: str, themes: list, today: str) -> str:
    return (
        f"오늘은 {today}이야. 아래는 오늘 아침 MTN 방송(전일 국장·간밤 미장·오늘 주목) 요약이야.\n\n{summary}\n\n"
        "이 방송 내용으로 볼 때, 오늘 한국 장에서 '이벤트(선거·정책·실적·사고·미장 급등 업종 등) 때문에 따로 조명받을' "
        f"테마를 아래 목록에서 많이 언급된 순서로 최대 {MAX_THEMES}개 골라. 시장 전체가 빠져도 이 테마만은 강할 만한 것으로.\n"
        "★ 억지로 고르지 마. 방송 여러 부분(미장·국장·전망 등)에서 자연스럽게 여러 번 언급된 섹터만. "
        "한 코너에서 소개만 된 섹터, 그냥 많이 오른 섹터는 안 됨. 없으면 빈 목록.\n"
        f"[테마 목록]\n{' / '.join(themes)}\n\n"
        '답은 JSON 한 줄만: {"themes": [{"theme": "목록에 있는 이름 그대로", "why": "한 줄 이유"}]}'
    )


def parse_choice(text: str, themes: list) -> list:
    """LLM 답 → [(테마, 이유)] 목록에 있는 것만(순서 유지, 중복 없음). 예전 꼴({"theme": …})도 받음."""
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return []
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return []
    items = d.get("themes") if isinstance(d.get("themes"), list) else [d]
    out = []
    for it in items:
        th, why = (it.get("theme") or "").strip(), (it.get("why") or "").strip()
        if th in themes and th not in [t for t, _ in out]:
            out.append((th, why))
    return out[:MAX_THEMES]


def leaders(quotes: dict, theme_members: dict) -> dict:
    """테마마다 그 시각 거래대금 1위(섹터 대장) → {코드: 테마}."""
    out = {}
    for th, members in theme_members.items():
        qs = [(float(quotes[c].get("value") or 0), c) for c in members if c in quotes and quotes[c].get("price")]
        if qs:
            out.setdefault(max(qs)[1], th)
    return out


def _mins(hhmm: str) -> int:
    return int(hhmm[:2]) * 60 + int(hhmm[2:4])


def track_index(state: dict, lows_pct: dict, now_t: str):
    """지수별 장중 저가(%)가 새로 낮아진 시각을 기억 — state["idx"] = {지수: (저가%, 시각)}."""
    idx = state.setdefault("idx", {})
    for k, v in lows_pct.items():
        if k not in idx or v < idx[k][0] - 0.05:
            idx[k] = (v, now_t)


def bottom_ok(state: dict, st: dict, now_t: str, drop: float = None) -> bool:
    """-drop%↓ 찍은 지수 중 하나라도 '저가 대비 +1%↑ 반등 & 최근 20분 새 저가 없음'이면 바닥 확인."""
    drop = INDEX_DROP if drop is None else drop
    for k, (low, t_low) in (state.get("idx") or {}).items():
        if low <= -drop and k in st and st[k][1] >= BOTTOM_REBOUND and _mins(now_t) - _mins(t_low) >= BOTTOM_QUIET_MIN:
            return True
    return False


def plus_min(hhmm: str, m: int) -> str:
    d = datetime.datetime.strptime(hhmm, "%H%M") + datetime.timedelta(minutes=m)
    return d.strftime("%H%M")


def update_lows(lows: dict, quotes: dict):
    """T+20 이후 매분 시세로 '새 저가' 갱신."""
    for code, q in quotes.items():
        p = float(q.get("price") or 0)
        if p > 0:
            lows[code] = min(lows.get(code, p), p)


def pick(quotes: dict, lows: dict, exclude=(), rebound: float = REBOUND_PCT, max_chg: float = MAX_CHG):
    """→ (코드, 가격, 등락%, 새 저가 대비 반등%) 반등 1등 / 없으면 None."""
    best = None
    for code, q in quotes.items():
        p, chg = float(q.get("price") or 0), float(q.get("chg") or 0)
        if code in exclude or p <= 0 or code not in lows or chg >= max_chg:
            continue
        b = (p / lows[code] - 1) * 100
        if b >= rebound and (best is None or b > best[3]):
            best = (code, p, chg, b)
    return best
