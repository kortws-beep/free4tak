"""
weather_ai.py — 키키 머리(Claude Sonnet)가 날씨판을 직접 검토 (2026-11-17, 대장 "키키에 니 동생 — 능동적 판단을 위해")
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
날씨판 점수(weather_board — 규칙·5년 검증)와 뉴스 층(news_weather — Haiku)만으로는 '사람처럼 종합해서' 보는 단계가 없었음.
→ 키키가 쓰는 모델(ANTHROPIC_MODEL, 기본 claude-sonnet-5)에게 그날 재료를 전부 보여 주고 검토를 받는다:
  층별 점수·이유, 뉴스 층 요약·사건, 다가오는 이벤트(출처), 봇 보유 현황, (2차) 야간선물·오전 흐름.
답: "동의" 또는 "한 칸 더 조심" + 이유 + 오늘 지켜볼 것.
안전장치(대장 원칙·검증 범위 안에서):
  · 키키 AI는 태세를 '더 조심' 쪽으로만 한 칸 옮길 수 있음 — 풀어 주는 건 못 함(풀기는 대장 `!날씨해제`만).
  · 1차(07:10)만 실제 반영, 2차(11:50)는 의견 표시만(2차 점검 자체가 아직 봇 제어 안 함).
  · 모델 호출 실패·형식 오류면 '동의'로 보고 규칙 판단 그대로(멈추거나 풀지 않음).
결과는 money_weather.db board_daily(layer='ai_review')에 이유와 함께 남김 → 나중에 'AI가 조심하자 한 날'의 실제 성적 검증.
"""
import datetime
import json
import os
import re
import sqlite3

_HERE = os.path.dirname(os.path.abspath(__file__))
_BASE = os.path.dirname(_HERE)
MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5")


def _positions() -> str:
    try:
        import sys
        sys.path.insert(0, os.path.join(_BASE, "core"))
        import master_db
        # ★ 은퇴한 봇(nbot·sbo2·sbot2)의 옛 보유 줄이 통합 장부에 남아 있어 지금 운영 봇만(11-17 키키 AI가 옛 종목을 보유로 오해)
        ps = [p for p in master_db.get_all_positions() if p["bot_type"] in ("sbot", "daybot", "cbot")]
        names = {}
        try:
            c = sqlite3.connect(os.path.join(_BASE, "lina_bot", "kr_theme_finance.db"), timeout=5)
            names = dict(c.execute("SELECT code, name FROM kr_stock_master"))
            c.close()
        except Exception:
            pass
        return ", ".join(f"{p['bot_type']}:{p.get('stock_name') or names.get(p['code'], p['code'])}"
                         f"({(p.get('profit_rate') or 0):+.1f}%)" for p in ps[:20]) or "없음"
    except Exception:
        return "(조회 실패)"


def review(board: dict, phase: int = 1, midday: dict = None) -> dict:
    import money_weather
    import news_weather
    day = board.get("date") or datetime.date.today().isoformat()
    ev = money_weather.upcoming(7, day)
    nw = news_weather.today_score(day, phase)

    def items(k):
        return "; ".join(f"{n} {why}({s:+d})" for n, s, why in board[k][2])
    w = datetime.date.fromisoformat(day)
    prompt = f"""오늘은 {day}({'월화수목금토일'[w.weekday()]})이야. 너는 '키키' — 한국 주식(sbot 스윙·daybot 단타)·코인(cbot) 자동매매 봇들의 관제탑이야.
아래 '자금시장 날씨판' 규칙 판단을 검토해 줘. 규칙은 1층 글로벌(5년 검증 통과)이 기준이고, 2층·뉴스가 나쁠 때 한 칸씩 더 조심하는 구조야.
태세: 🟢공격(자유) · 🟡보통(자유) · 🟠방어(daybot 음봉·이벤트 엔진만, sbot 1슬롯, cbot BTC 오를 때만) · 🔴쉼(신규매수 정지, 매도는 계속).

[{phase}차 판단 재료]
1층 글로벌 {board['global'][1]}: {items('global')}
2층 한국 주식 {board['kr'][1]}: {items('kr')}
2층 코인 {board['coin'][1]}: {items('coin')}
뉴스 층: {f'{nw[0]:+d} — {nw[1]}' if nw else '없음'}
규칙 결과: 주식 {board['stock_total'][1]} · 코인 {board['coin_total'][1]}
다가오는 이벤트(7일): {'; '.join(f'{e[0][5:]} {e[1]} {e[2]} {e[3]}' for e in ev) or '없음'}
봇 보유: {_positions()}
""" + (f"2차 점검(11:50): 나스닥 선물 {midday.get('nq')}, 코스피 {midday.get('kospi')}, 코스닥 {midday.get('kosdaq')}\n" if midday else "") + """
판단 기준: 규칙이 놓친 위험(큰 이벤트 직전, 뉴스와 숫자의 어긋남, 보유 종목이 몰린 분야의 악재 등)이 분명할 때만 "한 칸 더 조심".
애매하면 "동의". 풀어 주는 판단은 하지 마(권한 없음). 없는 사실을 지어내지 마.
JSON만 답해: {"verdict": "동의" 또는 "한 칸 더 조심", "target": "주식" 또는 "코인" 또는 "둘 다", "reason": "한두 문장", "watch": ["오늘 지켜볼 것"] (최대 3)}"""
    try:
        import anthropic
        res = anthropic.Anthropic().messages.create(model=MODEL, max_tokens=3000, output_config={"effort": "medium"},
                                                    messages=[{"role": "user", "content": prompt}])
        if res.stop_reason == "refusal":
            return {"verdict": "동의", "reason": "(모델 거절 — 규칙 판단 유지)", "watch": [], "error": "refusal"}
        txt = "".join(b.text for b in res.content if b.type == "text")
        m = re.search(r"\{.*\}", txt, re.S)
        d = json.loads(m.group(0)) if m else {}
        if d.get("verdict") not in ("동의", "한 칸 더 조심"):
            d["verdict"] = "동의"
        return d
    except Exception as e:
        return {"verdict": "동의", "reason": f"(검토 실패 — 규칙 판단 유지: {str(e)[:120]})", "watch": [], "error": str(e)[:200]}


def apply_to_board(board: dict, r: dict) -> dict:
    """'한 칸 더 조심'이면 대상 태세를 한 칸 아래로(쉼은 그대로). 원래 값은 *_rule에 남김."""
    import weather_board
    labels = [lab for _, lab in weather_board.STATES]
    if r.get("verdict") != "한 칸 더 조심":
        return board
    tgt = r.get("target") or "둘 다"
    for key, name in (("stock_total", "주식"), ("coin_total", "코인")):
        if tgt in (name, "둘 다"):
            sc, lab = board[key]
            board[key + "_rule"] = (sc, lab)
            board[key] = (sc, labels[min(labels.index(lab) + 1, len(labels) - 1)])
    return board


def save(day: str, phase: int, r: dict) -> None:
    try:
        import weather_board
        c = sqlite3.connect(weather_board.MW_DB, timeout=10)
        c.execute("CREATE TABLE IF NOT EXISTS board_daily (date TEXT, layer TEXT, score INTEGER, state TEXT, items TEXT, "
                  "created_at TEXT, PRIMARY KEY(date, layer))")
        c.execute("INSERT OR REPLACE INTO board_daily VALUES (?,?,?,?,?,?)",
                  (day, f"ai_review{phase}", 0, r.get("verdict", ""), json.dumps(dict(r, model=MODEL), ensure_ascii=False),
                   datetime.datetime.now().isoformat(timespec="seconds")))
        c.commit()
        c.close()
    except Exception as e:
        print(f"⚠️ [키키 AI 검토] 기록 실패: {e}")


def format_review(r: dict, phase: int) -> str:
    w = "\n".join(f"   👀 {x}" for x in (r.get("watch") or [])[:3])
    tgt = f"({r.get('target')})" if r.get("verdict") == "한 칸 더 조심" else ""
    act = "→ 태세 한 칸 더 조심 적용" if (phase == 1 and r.get("verdict") == "한 칸 더 조심") else \
        ("→ 의견만(2차는 봇 안 바꿈)" if phase == 2 else "")
    return f"🦊 **키키 AI 검토 {phase}차** ({MODEL}): **{r.get('verdict')}**{tgt} {act}\n   {r.get('reason', '')}" + (f"\n{w}" if w else "")
