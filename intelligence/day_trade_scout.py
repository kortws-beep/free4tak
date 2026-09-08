"""
day_trade_scout.py — 수동 단타 리서치 스카우트
================================================================
[배경]
2026-09-09, 대장이 키움 수동매매(5일 이내 단타)용으로, sbot이 안 쓰는
"단타류" 검색식 종목을 뉴스/텔레그램과 엮어서 오늘 살 만한 종목을
추려달라고 요청. 자동매매(sbot/sbo2/cbot)와는 완전히 무관한 별도
리서치 도구 — 실제 매수는 대장이 키움으로 직접 함, 이 스크립트는
후보 제시만 함.

[후보 소스] 키움 조건검색식 4개 (대장이 직접 지정)
  - 단타000
  - 장개장직후 종목찾기
  - 5본봉거래대금단타(시총50조이하)
  - 주도주검색식3 (sbot도 쓰지만 조회만 하는 별도 도구라 안 부딪힘)

[타이밍]
장 시작 전엔 위 조건들(특히 "장개장직후"/"5본봉")이 당일 분봉 데이터가
없어 의미가 없음(대장 지적) — 09:35(장 시작 35분 후)에 실행해서 실제
당일 단타 후보가 잡히게 함.

[근거 데이터]
  - stock_event_bonus 테이블(telegram_monitor.py가 이미 쌓아둔 텔레그램/
    공시 기반 종목별 가산점+사유) — 신규 조회 로직 안 만들고 재사용.
  - KIS API로 현재가/등락률/거래량 조회.
  - 위 데이터를 Claude(소넷5)에게 넘겨 "오늘 매수 후보 Top N + 이유" 요약.

[전송]
sbot/sbo2/cbot과 동일한 Notifier(키키봇 DM)로 전송, 태그는 [스카우트].

[섹터 교체 트리거] (★ 2026-09-09 추가, 대장 아이디어)
09:35 정기 스캔 하나로는 그 이후 등장하는 새 주도테마를 못 잡음 —
sector_monitor.py가 이미 쌓고 있는 sector_flow 데이터로 "바톤터치"
(detect_baton_touch, 급가속>+30%)가 감지되면 그때도 추가로 스캔.
같은 테마로 하루에 여러 번 알림 보내지 않도록 상태파일(day_trade_
scout_state.json)에 "오늘 이미 알림 보낸 테마" 기록.

[실행 방법]
  python3 intelligence/day_trade_scout.py            # 09:35 정기 스캔
  python3 intelligence/day_trade_scout.py --sector-check  # 섹터 교체 확인(신호 있을 때만 스캔)

  # cron 등록
  35 9 * * 1-5 cd /home/free4tak/k-bot/stock_bot && \\
      /home/free4tak/k-bot/stock_bot/venv/bin/python3 intelligence/day_trade_scout.py \\
      >> /home/free4tak/k-bot/stock_bot/logs/day_trade_scout.log 2>&1
  */10 9-15 * * 1-5 cd /home/free4tak/k-bot/stock_bot && \\
      /home/free4tak/k-bot/stock_bot/venv/bin/python3 intelligence/day_trade_scout.py --sector-check \\
      >> /home/free4tak/k-bot/stock_bot/logs/day_trade_scout.log 2>&1
================================================================
"""
import os
import sys
import json
import asyncio
import datetime
import sqlite3

_here = os.path.dirname(os.path.abspath(__file__))
_base = os.path.dirname(_here)
for _d in ["core", "intelligence", "interface", "bots", ""]:
    _p = os.path.join(_base, _d)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from dotenv import load_dotenv
for _ep in [os.path.join(_here, ".env"), os.path.join(_base, ".env")]:
    if os.path.exists(_ep):
        load_dotenv(_ep)
        break

from kiwoom_api import KiwoomAPI
from kis_api import KisAPI
from telegram_monitor import get_stock_event_bonus
from sector_monitor import detect_baton_touch, DB_PATH as SECTOR_DB_PATH

CONDITION_KEYWORDS = ["단타000", "장개장직후", "5본봉", "주도주"]
MAX_CANDIDATES_TO_LLM = 25   # 프롬프트 비대화 방지 — 조회순 상위 N개만 넘김
STATE_FILE = os.path.join(_here, "day_trade_scout_state.json")
BATON_ACCEL_THRESHOLD = 30.0  # detect_baton_touch의 "급가속" 기준과 동일(재확인용)


def _load_state() -> dict:
    today = datetime.date.today().isoformat()
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            st = json.load(f)
    except Exception:
        st = {}
    if st.get("date") != today:
        st = {"date": today, "notified_themes": [], "baseline_done": False}
    return st


def _save_state(st: dict):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ 상태파일 저장 오류: {e}")


def _check_new_sector_signal(state: dict) -> str:
    """오늘 아직 알림 안 보낸 급가속 테마가 있으면 테마명 반환, 없으면 빈 문자열."""
    try:
        conn = sqlite3.connect(SECTOR_DB_PATH, timeout=5)
        conn.execute("PRAGMA query_only=ON")
        signals = detect_baton_touch(conn)
        conn.close()
    except Exception as e:
        print(f"⚠️ 섹터 신호 조회 오류: {e}")
        return ""

    notified = set(state.get("notified_themes", []))
    for s in signals:
        if s["status"] == "급가속🔥" and s["theme_nm"] not in notified:
            return s["theme_nm"]
    return ""


def _notify(msg: str, critical: bool = False):
    try:
        from notifier import Notifier
        Notifier(name="스카우트").send(f"[스카우트] {msg}", critical=critical)
    except Exception as e:
        print(f"⚠️ 알림 전송 오류: {e}")


def _gather_candidates() -> list:
    """4개 조건검색식에서 후보 종목 수집."""
    kiwoom = KiwoomAPI()
    if not kiwoom.enabled:
        print("⚠️ 키움 비활성 — 스캔 불가")
        return []

    code_name_map = {}
    code_tag_map  = {}
    loop = asyncio.new_event_loop()
    try:
        codes = loop.run_until_complete(
            kiwoom.get_condition_codes(
                use_keywords=CONDITION_KEYWORDS,
                code_name_map=code_name_map,
                code_tag_map=code_tag_map,
            )
        )
    finally:
        loop.close()

    return [(c, code_name_map.get(c, c), code_tag_map.get(c, "")) for c in codes]


def _enrich(candidates: list, kis: KisAPI) -> list:
    """각 후보에 현재가/등락률/거래량 + 텔레그램·공시 가산점 붙이기."""
    enriched = []
    for code, name, cond_name in candidates:
        mdata = kis.get_market_data(code) or {}
        price  = mdata.get("stck_prpr", "0")
        chg    = mdata.get("prdy_ctrt", "0")
        vol    = mdata.get("acml_vol", "0")
        bonus, reason = get_stock_event_bonus(code, bot_type="sbot")
        enriched.append({
            "code": code, "name": name, "cond": cond_name,
            "price": price, "chg": chg, "vol": vol,
            "bonus": bonus, "reason": reason,
        })
    return enriched


def _build_prompt(enriched: list) -> str:
    lines = []
    for e in enriched[:MAX_CANDIDATES_TO_LLM]:
        tag = f" | 텔레그램/공시: {e['reason']}" if e["reason"] else ""
        lines.append(
            f"- {e['name']}({e['code']}) [{e['cond']}] "
            f"현재가:{e['price']}원 등락률:{e['chg']}% 거래량:{e['vol']}{tag}"
        )
    candidate_text = "\n".join(lines) if lines else "(조건검색 후보 없음)"

    return (
        "너는 대한민국 주식 단타 트레이더를 보좌하는 리서치 참모야.\n"
        "아래는 오늘 장 시작 35분 후, 4개 단타 계열 조건검색식에 걸린 종목 목록과 "
        "각 종목의 현재가/등락률/거래량, 그리고 최근 텔레그램/공시 동향(있는 경우)이야.\n\n"
        f"[오늘의 조건검색 후보]\n{candidate_text}\n\n"
        "🚨 [작성 지침]\n"
        "1. 이 중 오늘 단타(수일 내 매도 목표)로 매수할 만한 종목을 최대 5개까지 골라줘.\n"
        "2. 텔레그램/공시 근거가 있는 종목을 우선하되, 없어도 등락률·거래량이 뚜렷하면 포함해.\n"
        "3. 각 종목마다 '왜 오늘인지' 한 줄 이유를 붙여.\n"
        "4. 데이터에 없는 내용은 절대 지어내지 마.\n"
        "5. 후보가 마땅치 않으면 '오늘은 마땅한 후보 없음'이라고 솔직히 말해."
    )


def _call_claude(prompt: str) -> str:
    import anthropic
    from common_utils import extract_claude_text
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    res = client.messages.create(
        model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5"),
        max_tokens=1200,
        messages=[{"role": "user", "content": prompt}],
    )
    return extract_claude_text(res)


def _run_scan(trigger_label: str, notify_on_empty: bool = False):
    print(f"🔎 [스카우트] 단타 후보 스캔 시작 ({trigger_label})")
    candidates = _gather_candidates()
    if not candidates:
        print("   후보 없음")
        if notify_on_empty:
            _notify(f"오늘 조건검색 후보가 없어 ({trigger_label})", critical=False)
        return
    print(f"   후보 {len(candidates)}개 수집")

    kis = KisAPI()
    enriched = _enrich(candidates, kis)

    prompt = _build_prompt(enriched)
    summary = _call_claude(prompt)
    if not summary:
        summary = "(AI 요약 실패 — 원본 후보 목록만 첨부)\n" + "\n".join(
            f"{e['name']}({e['code']})" for e in enriched[:10]
        )

    _notify(f"오늘의 단타 후보 ({trigger_label})\n\n{summary}", critical=False)
    print("✅ 전송 완료")


def main():
    sector_check_mode = "--sector-check" in sys.argv
    state = _load_state()

    if not sector_check_mode:
        # 09:35 정기 스캔 — 항상 실행, 후보 없어도 확인차 알림
        _run_scan("09:35 정기 스캔", notify_on_empty=True)
        state["baseline_done"] = True
        _save_state(state)
        return

    # 섹터 교체 확인 모드 — 정기 스캔이 아직 안 됐으면 스킵(중복 방지),
    # 새로운 급가속 테마가 있을 때만 추가 스캔
    if not state.get("baseline_done"):
        print("⏭️ [스카우트] 09:35 정기 스캔 전이라 섹터체크 스킵")
        return
    theme = _check_new_sector_signal(state)
    if not theme:
        print("😴 [스카우트] 새로운 섹터 교체 신호 없음")
        return

    _run_scan(f"섹터 교체 감지: {theme}")
    state.setdefault("notified_themes", []).append(theme)
    _save_state(state)


if __name__ == "__main__":
    main()
