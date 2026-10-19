"""
sbot_exit_watch.py — sbot 기존 보유종목 '반등 때 빠져나오기' 알림 (2026-10-19, 알림 전용·주문 없음)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
10-19 sbot 6종목 중 4종목(홀드)은 대세 하락장에 산 것이라 -19~-29%, 합계 -184만. 슬롯·돈이 묶여 새 규칙(하락장 쉬기·
장중 하락 감지)으로 새로 살 자리가 없음. 대장: "반등 때 나눠 정리하는 방식으로 알려주면, 용인할 손실이면 하나씩 정리하겠다."
→ 매수가가 아니라 '기준가'(--init 실행한 날의 가격, 10-19 종가)를 잡고 장중 3분마다:
  ① 기준가 +5% 반등 → "30% 정리 검토" (지금 팔면 손실 얼마인지 같이)
  ② 반등 뒤 고점 -4% 꺾임 → "남은 물량 정리 검토" — 그 뒤 그 가격을 새 기준가로 다시 지켜봄
  ③ 기준가 -5%씩 더 빠질 때마다 → 위험 알림
대장이 팔면 sbot 계좌 잔고에서 사라지니 자동으로 감시 끝. 키키 채널 알림([SBOT]).
실행:  python intelligence/sbot_exit_watch.py --init   (오늘 가격을 기준가로 등록)
       python intelligence/sbot_exit_watch.py          (cron 평일 09:03~15:18, 3분마다)
"""
import os
import sys
import json
import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "core"))
sys.path.insert(0, os.path.join(BASE, "interface"))
STATE = os.path.join(BASE, "sbot_exit_watch.json")
REBOUND, TRAIL, DROP = 5.0, 4.0, 5.0


def won(x: float) -> str:
    return f"{x:+,.0f}원"


def evaluate(it: dict, name: str, price: float, avg: float, qty: int) -> list:
    """종목 하나 상태(it, 제자리 갱신) + 지금가 → 알림 문구 목록."""
    base = it["base"]
    r = round((price / base - 1) * 100, 4)          # -10%가 -9.9999…로 계산돼 단계가 덜 오르는 것 방지
    loss = (price - avg) * qty
    tail = f"지금 팔면 {won(loss)} (매수가 대비 {(price / avg - 1) * 100:+.1f}%, {qty}주)"
    out = []
    if it.get("armed"):
        it["peak"] = max(it["peak"], price)
        if price <= it["peak"] * (1 - TRAIL / 100):
            out.append(f"📉 [탈출감시] {name} 반등 꺾임 — 고점 {it['peak']:,.0f} 대비 -{TRAIL:g}% · 지금 {price:,.0f}\n"
                       f"→ 남은 물량 정리 검토 · {tail}")
            it.update(base=price, armed=False, peak=None, drop_lv=0)
    elif r >= REBOUND:
        it.update(armed=True, peak=price)
        out.append(f"📈 [탈출감시] {name} 반등 {r:+.1f}% (기준 {base:,.0f} → {price:,.0f})\n"
                   f"→ 30% 정리 검토 · {tail}")
    lv = int(-r // DROP) if r <= -DROP else 0
    if lv > it.get("drop_lv", 0):
        it["drop_lv"] = lv
        out.append(f"⚠️ [탈출감시] {name} 기준가 대비 {r:+.1f}% 추가 하락 (기준 {base:,.0f} → {price:,.0f}) · {tail}")
    return out


def load() -> dict:
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"items": {}}


def save(st: dict):
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE)


def sbot_api():
    from dotenv import dotenv_values
    from kis_api import KisAPI
    v = dotenv_values(os.path.join(BASE, ".env"))
    return KisAPI(appkey=v["KIS_APPKEY2"], secret=v["KIS_SECRET2"], cano=v["KIS_CANO2"], acnt=v["KIS_ACNT_PRDT_CD2"])


if __name__ == "__main__":
    api = sbot_api()
    now = datetime.datetime.now()
    pos = api.get_current_positions(force=True) or {}
    px = api.get_multi_price(list(pos)) if pos else {}
    st = load()
    if "--init" in sys.argv:
        st = {"base_date": now.date().isoformat(),
              "items": {c: {"name": p.get("name", c), "base": px[c]["price"], "armed": False, "peak": None, "drop_lv": 0}
                        for c, p in pos.items() if px.get(c, {}).get("price")}}
        save(st)
        print(f"기준가 등록 {now:%Y-%m-%d %H:%M}: " + ", ".join(f"{v['name']} {v['base']:,.0f}" for v in st["items"].values()))
        sys.exit(0)
    if api.is_market_open() is False:
        sys.exit(0)
    from notifier import Notifier
    note = Notifier(name="sbot")
    items = st.get("items", {})
    for code in [c for c in items if c not in pos]:
        print(f"{items[code]['name']} 잔고 없음 — 감시 끝")
        items.pop(code)
    for code, it in items.items():
        q = px.get(code, {}).get("price")
        if not q:
            continue
        for m in evaluate(it, it["name"], q, pos[code]["entry_price"], int(pos[code]["qty"])):
            print(m.splitlines()[0])
            note.send(f"[SBOT] {m}", critical=False)
    save(st)
