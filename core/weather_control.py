"""
weather_control.py — 자금시장 날씨판 → 봇 실제 제어 (2026-11-17, 대장 "키키가 실질적인 관리를 해야 해")
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
키키가 매일 아침(07:10) 날씨판(intelligence/weather_board.py)을 계산하면, 여기서 봇마다 '오늘 날씨'를 상태 파일에 적고 행동을 정한다.
대장 지정:
  🔴 쉼   → 봇 신규매수 정지(일시중단 — 보유 종목 매도·손절 감시는 계속). 다음 날 쉼이 아니면 자동 해제.
  🟠 방어 → 거기에 맞는 행동: daybot = 일반 매수 멈추고 음봉·이벤트 엔진만 / sbot = 하락장처럼 1슬롯만 /
            cbot = 비트코인이 오르는 중일 때만 신규매수(극단공포 예외와 같은 방식)
  🟡 보통·🟢 공격 → 봇 자유(기존 규칙 그대로 — 국면 1슬롯·장중 하락 감지 등 원래 안전장치는 계속 일함)
주식 봇(sbot·daybot)은 '주식 태세'(1층 글로벌+2층 한국), cbot은 '코인 태세'(1층+2층 코인)를 따른다.
안전 원칙:
  · 대장이 직접 건 일시중단(!정지 등)이나 손실한도 멈춤은 절대 안 건드림 — 날씨판이 건 멈춤(pause_reason='weather_rest')만 풀고 건다.
  · 날씨 기록은 그날 날짜가 붙어 있어 하루만 유효(키키가 못 돌면 다음 날 자동으로 '날씨 없음' = 봇 자유).
  · 대장이 `!날씨해제` 하면 그날은 보통으로 바꾸고 날씨판 멈춤을 풂. `!날씨해제 주식`(sbot·daybot만) / `!날씨해제 코인`(cbot만) — 11-25
"""
import datetime
import os

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_FILES = {"sbot": "sbot_state.json", "daybot": "daybot_state.json", "cbot": "cbot_state.json"}
BOT_LAYER = {"sbot": "stock_total", "daybot": "stock_total", "cbot": "coin_total"}
PAUSE_REASON = "weather_rest"
ACTION_TEXT = {
    "쉼": "신규매수 정지(매도·손절 감시는 계속)",
    "방어": {"sbot": "하락장처럼 1슬롯만", "daybot": "일반 매수 멈춤 — 음봉·이벤트 엔진만", "cbot": "BTC 오르는 중일 때만 신규매수"},
    "보통": "자유(기존 규칙)", "공격": "자유(기존 규칙)",
}


def _word(state_label: str) -> str:
    """'🔴 쉼' → '쉼'."""
    for w in ("쉼", "방어", "보통", "공격"):
        if w in (state_label or ""):
            return w
    return ""


def today_weather(state: dict, today: str = None) -> str:
    """봇이 자기 상태 dict로 '오늘 날씨'(쉼·방어·보통·공격, 없으면 '')를 읽음 — 날짜가 오늘인 것만."""
    w = (state or {}).get("weather") or {}
    today = today or datetime.date.today().isoformat()
    return w.get("state", "") if w.get("date") == today else ""


def action_for(bot: str, word: str) -> str:
    a = ACTION_TEXT.get(word, "")
    return a.get(bot, "") if isinstance(a, dict) else a


STOCK_BOTS, COIN_BOTS = ("sbot", "daybot"), ("cbot",)
RELEASE_WORDS = {"주식": STOCK_BOTS, "코인": COIN_BOTS, "전체": STOCK_BOTS + COIN_BOTS}


def apply(board: dict, override: str = None, bots=None) -> list:
    """날씨판 → 각 봇 상태 파일. → [(봇, 날씨, 한 행동 문구)]. override='보통'이면 대장 해제.
    ★ 11-25 대장 "코인과 주식의 날씨해제를 따로": bots로 대상 봇만(None이면 전부)."""
    from common_utils import read_state, update_state
    day = board.get("date") or datetime.date.today().isoformat()
    out = []
    for bot, fname in STATE_FILES.items():
        if bots and bot not in bots:
            continue
        path = os.path.join(_BASE, fname)
        st = read_state(path, default={}) or {}
        layer = BOT_LAYER[bot]
        score, label = board.get(layer, (0, ""))[:2] if board.get(layer) else (0, "")
        word = override or _word(label)
        if not word:
            continue
        kw = {"weather": {"date": day, "state": word, "score": score, "label": label, "override": bool(override)}}
        did = action_for(bot, word)
        manual_pause = st.get("paused") and st.get("pause_reason") != PAUSE_REASON
        if word == "쉼":
            if manual_pause:
                did = "이미 대장/안전장치가 멈춰 둠 — 그대로"
            elif not st.get("paused"):
                kw.update(paused=True, pause_reason=PAUSE_REASON)
        elif st.get("paused") and st.get("pause_reason") == PAUSE_REASON:
            kw.update(paused=False, pause_reason="")
            did = f"날씨판 정지 해제 → {did}"
        ok = update_state(path, **kw)
        out.append((bot, word, did if ok is not False else "⚠️ 상태 파일 저장 실패"))
    return out


def format_actions(rows: list) -> str:
    name = {"sbot": "스윙(sbot)", "daybot": "단타(daybot)", "cbot": "코인(cbot)"}
    return "🗼 **키키 관제** — 오늘 날씨대로 봇 태세 설정\n" + "\n".join(
        f"   {name.get(b, b)}: {w} → {d}" for b, w, d in rows)
