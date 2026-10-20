"""
manual_autosell.py — 리나등록 종목 자동 익절 (2026-10-14 대장 지정)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

대장: "매수와 손절은 내가 하지만, 등록으로 걸어둔 건 리나가 직접 매도." (한선엔지니어링을
일찍 팔았는데 상한가, 삼미금속도 일찍 매도 — 규칙대로 리나가 팔게 하자.) 키움 계좌(수동매매)
에서 키움 API로 주문. 봇들(한투 계좌)과는 계좌가 달라 봇 물량은 건드릴 일이 없음.

매도 규칙(대장 지정, 데이터 쌓이면 조정):
  1. 등록가 대비 +3% 찍으면 추적 시작(그 전엔 아무것도 안 함 — 손절은 대장이 직접)
  2. 추적 중 고점 대비 -4% 밀리면 1차 트레일링 → 남은 수량 전부 매도
     (장중 출렁임이 커서 넉넉하게. 고점이 +3~4%대면 본전 근처에서 나갈 수 있음)
  3. 등록가 대비 +5% 찍으면 30% 익절
  4. 30% 익절 뒤엔 고점 대비 -3% 밀리면 최종 익절 → 나머지 전부 매도
모드(환경변수 LINA_AUTOSELL):
  paper(기본) — 실제 주문 없이 "지금이면 이렇게 팔았을 거야"만 알림(연습). 상태는 판 것처럼 진행
  live        — 키움 시장가 매도(정규장 09:00~15:20만, 등록 수량과 키움 매도가능수량 중 작은 값).
                ★ 10-16: 키움 계좌에 대장 장기 보유 종목이 많아(SK증권 1,132주 등) 수량 없이 등록하면 '매도가능
                수량 전부'가 팔릴 위험 → live에선 수량을 넣고 등록한 종목만 매도(없으면 경고만)
  off         — 매도 신호 알림만(예전 방식)
★ 10-20 대장: '단타' 등록(`단타 종목명 10주` — 한투 데이봇 계좌만) — 하락장 쉬는 날 대장이 고른 1종목을 짧게:
  daybot 매도 로직 그대로 +2% 찍으면 30% 익절 → 나머지는 트레일링(고점 -1.5%, 고점 +4.5% 넘으면 -2%), 최소 +1%는 지킴.
  손절은 대장 직접(다른 리나 등록과 같음).
"""
import os


class KisBroker:
    """★ 10-16 대장 — daybot 계좌(한투)에서 대장이 직접 산 종목도 리나 규칙으로 익절('등록 … 데이봇').
    daybot이 산 종목은 daybot 규칙, 대장이 산 건 리나 규칙(손절 없음)의 이중 구조. KisAPI()는 기본이
    daybot 계좌(KIS_CANO)."""
    label = "한투(데이봇 계좌)"

    def __init__(self, api):
        self.api = api

    def holdings(self) -> dict:
        pos = self.api.get_current_positions(force=True) or {}
        return {c: {"qty": int(p.get("qty", 0) or 0), "avail": int(p.get("qty", 0) or 0)} for c, p in pos.items()}

    def sell_market(self, code: str, qty: int) -> tuple:
        ok = self.api.sell(code, int(qty), price=0)
        return bool(ok), ("접수" if ok else "주문 거부(한투)")

START_PCT = 3.0
TRAIL1_PCT = 4.0
TP1_PCT = 5.0
TP1_RATIO = 0.30
TRAIL2_PCT = 3.0
SELL_START, SELL_END = "0900", "1520"
SCALP_TP = 2.0                       # 단타: 이만큼 오르면 30% 익절 + 트레일링 시작
SCALP_TIGHT, SCALP_WIDE, SCALP_WIDEN_AT, SCALP_FLOOR = 1.5, 2.0, 4.5, 1.0     # daybot과 같은 트레일링 폭·최소 수익


def is_scalp(w: dict) -> bool:
    return w.get("mode") == "scalp"


def scalp_line(entry: float, peak: float) -> float:
    """단타 트레일링 매도선 — 고점 -1.5%(고점 +4.5% 넘으면 -2%), 그래도 평단 +1% 아래로는 안 내려감."""
    trail = SCALP_WIDE if (peak / entry - 1) * 100 > SCALP_WIDEN_AT else SCALP_TIGHT
    return max(peak * (1 - trail / 100), entry * (1 + SCALP_FLOOR / 100))


def trail_desc(w: dict) -> str:
    """30% 익절 뒤 나머지를 어떻게 지켜보는지(알림 문구용)."""
    if is_scalp(w):
        return f"고점 -{SCALP_TIGHT:g}% 트레일링(최소 +{SCALP_FLOOR:g}%)"
    return f"고점 -{TRAIL2_PCT:.0f}%"


def mode() -> str:
    m = os.getenv("LINA_AUTOSELL", "paper").strip().lower()
    return m if m in ("paper", "live", "off") else "paper"


def part_qty(qty) -> int:
    """30% 익절 수량 — 최소 1주, 최소 1주는 남김. 수량을 모르거나 1주면 0(팔지 않고 단계만 넘김)."""
    if not qty or qty < 2:
        return 0
    return min(qty - 1, max(1, int(qty * TP1_RATIO)))


def step(w: dict, price: float):
    """가격 한 번 반영 → 할 일 {"type": start|partial|final, "reason"} 또는 None. w의 고점·단계를 갱신
    (실제 수량 차감·단계 2/3 전환은 매도가 된 뒤 apply()에서)."""
    entry = w.get("entry_price") or 0
    stage = w.get("stage", 1 if w.get("peak_price") else 0)
    if entry <= 0 or price <= 0 or stage >= 3:
        return None
    rate = (price / entry - 1) * 100
    if is_scalp(w):
        return _step_scalp(w, price, entry, rate)
    if w.get("peak_price") is None:
        if rate >= START_PCT:
            w["peak_price"], w["stage"] = price, 1
            return {"type": "start", "reason": f"+{rate:.2f}% 도달 — 추적 시작"}
        return None
    w.setdefault("stage", stage)
    if price > w["peak_price"]:
        w["peak_price"] = price
    peak = w["peak_price"]
    if w["stage"] == 1:
        if rate >= TP1_PCT:
            return {"type": "partial", "reason": f"1차익절30%({rate:+.2f}%)"}
        if price <= peak * (1 - TRAIL1_PCT / 100):
            return {"type": "final", "reason": f"1차트레일링(고점 {peak:,.0f} -{TRAIL1_PCT:.0f}%, {rate:+.2f}%)"}
    elif w["stage"] == 2:
        if price <= peak * (1 - TRAIL2_PCT / 100):
            return {"type": "final", "reason": f"최종익절(고점 {peak:,.0f} -{TRAIL2_PCT:.0f}%, {rate:+.2f}%)"}
    return None


def _step_scalp(w: dict, price: float, entry: float, rate: float):
    if w.get("peak_price") is None:
        if rate < SCALP_TP:
            return None
        w["peak_price"], w["stage"] = price, 1
    w["peak_price"] = max(w["peak_price"], price)
    peak = w["peak_price"]
    line = scalp_line(entry, peak)
    if price <= line:
        return {"type": "final", "reason": f"단타 트레일링(고점 {peak:,.0f} → {line:,.0f}, {rate:+.2f}%)"}
    if w.get("stage", 1) == 1:      # 30% 익절 아직(주문 실패 땐 다음 루프에 다시)
        return {"type": "partial", "reason": f"단타 +{SCALP_TP:g}% 30% 익절({rate:+.2f}%)"}
    return None


REGIME_RESUME = float(os.getenv("DAYBOT_PAUSE_BELOW", "35") or 35)


def regime_turn_msg(watches: dict, date: str, breadth: float, prices: dict, resume: float = REGIME_RESUME):
    """★ 10-20 대장: 단타는 봇이 쉬는 동안만 빌려 쓰는 돈 — 국면이 다시 resume% 이상이면 봇에게 시드를 돌려줘야
    하니 애프터장이나 다음 날 09:10 전에 정리. 대장이 직접 판단·처리 → 알림만(주문 없음). 정리할 게 없으면 None."""
    if breadth is None or breadth < resume:
        return None
    rows = []
    for key, w in watches.items():
        if not is_scalp(w) or w.get("stage", 0) >= 3:
            continue
        code = w.get("code") or key.split("@")[0]
        px, entry = prices.get(code), w.get("entry_price") or 0
        rate = f" ({(px / entry - 1) * 100:+.1f}%)" if px and entry else ""
        rows.append(f"   ⚡ {w.get('name', code)}({code}) {w.get('qty') or '?'}주{rate}")
    if not rows:
        return None
    return (f"🔄 **국면 전환 {breadth:.0f}%** ({date[5:]} 종가, 기준 {resume:g}%) — 다음 거래일부터 봇 신규매수 재개\n"
            f"단타 종목 정리해서 봇에게 시드 돌려줘야 해(애프터장 또는 다음 날 09:10 전):\n" + "\n".join(rows)
            + "\n   (알림만 — 주문은 안 나가. 다 팔면 `해제 종목명 D`)")


def in_session(hhmm: str) -> bool:
    return SELL_START <= hhmm < SELL_END


def handle(code: str, w: dict, price: float, hhmm: str, md: str, kiwoom=None) -> dict:
    """한 종목 처리 → {"msgs": [...], "deregister": bool, "changed": bool, "sell_price": float|None}.
    kiwoom: holdings()/sell_market() 가진 객체(live 모드만)."""
    out = {"msgs": [], "deregister": False, "changed": False, "sell_price": None}
    # ★ 10-16 대장: 프리장에서 산 종목은 정규장 기준으로 -3~-5%로 시작할 수 있음 — 프리장(NXT) 가격으로
    #   +3% 추적을 시작해 고점을 잡아두면 정규장 시작하자마자 '고점 -4%'로 시가에 전량 팔릴 위험.
    #   추적·판단 모두 정규장(09:00~15:20) 가격으로만.
    if not in_session(hhmm):
        return out
    name = w.get("name", code)
    before = (w.get("peak_price"), w.get("stage"))
    act = step(w, price)
    out["changed"] = before != (w.get("peak_price"), w.get("stage"))
    if not act:
        return out
    if act["type"] == "start":
        out["msgs"].append(f"📈 [리나등록] {name}({code}) {act['reason']}(고점 {price:,.0f}원) — "
                           f"+{TP1_PCT:.0f}%에 30% 익절, 그 전엔 고점 -{TRAIL1_PCT:.0f}% 밀리면 전량 매도")
        return out
    remain = w.get("qty")
    qty = part_qty(remain) if act["type"] == "partial" else remain
    if act["type"] == "partial" and not qty:
        w["stage"] = 2
        out["changed"] = True
        out["msgs"].append(f"🎯 [리나등록] {name}({code}) {act['reason']} — 수량 {remain or '모름'}이라 "
                           f"30%는 건너뛰고 {trail_desc(w)} 최종 익절로 전환")
        return out
    if md == "off":
        _advance(w, act, qty)
        out["changed"] = True
        out["msgs"].append(f"🔔 [리나등록] {name}({code}) 매도 신호 — {act['reason']} @{price:,.0f}원"
                           + (f" · {qty}주" if qty else "") + " — 키움에서 매도해줘")
        return out
    if md == "paper":
        _advance(w, act, qty)
        out["changed"] = True
        out["msgs"].append(f"🧪 [연습] {name}({code}) {act['reason']} @{price:,.0f}원 — "
                           f"실제였으면 {qty or '전량'}{'주' if qty else ''} 매도했을 거야(주문 안 나감)")
        return out
    # live
    label = getattr(kiwoom, "label", "키움")
    if kiwoom is None:
        out["msgs"].append(f"⚠️ [리나등록] {name}({code}) {act['reason']} — 계좌 연결 없음, 주문 못 냄")
        return out
    if not w.get("qty0") and not w.get("qty"):
        if not w.get("warned_no_qty"):
            w["warned_no_qty"] = True
            out["changed"] = True
            out["msgs"].append(f"⚠️ [리나등록] {name}({code}) {act['reason']} — 등록 수량이 없어 실제 매도 안 함"
                               f"(계좌의 다른 보유분까지 팔 수 있어서). 직접 판단하거나 `등록 {name} 평단가 N주`로 다시 등록")
        return out
    held = (kiwoom.holdings() or {}).get(code, {})
    avail = int(held.get("avail", 0) or 0)
    qty = min(qty, avail) if qty else 0
    if qty <= 0:
        if not w.get("warned_no_qty"):
            w["warned_no_qty"] = True
            out["changed"] = True
            out["msgs"].append(f"⚠️ [리나등록] {name}({code}) {act['reason']} — {label} 매도가능수량 0, 주문 안 냄"
                               f"(이미 팔았으면 `해제 {name}`)")
        return out
    ok, msg = kiwoom.sell_market(code, qty)
    if not ok:
        out["msgs"].append(f"🚨 [리나등록] {name}({code}) 매도 주문 실패({qty}주) — {msg} · 다음 루프에 다시 시도")
        return out
    _advance(w, act, qty)
    out["changed"] = True
    out["sell_price"] = price
    if act["type"] == "final" or (w.get("qty") is not None and w["qty"] <= 0):
        out["deregister"] = True
    out["msgs"].append(f"💰 [리나등록] {name}({code}) {act['reason']} — {label} 시장가 {qty}주 매도 주문 @{price:,.0f}원 부근"
                       + ("" if out["deregister"] else f" · 남은 {w.get('qty')}주 {trail_desc(w)} 추적"))
    return out


def _advance(w: dict, act: dict, qty):
    if act["type"] == "partial":
        w["stage"] = 2
        if w.get("qty") and qty:
            w["qty"] = w["qty"] - qty
    else:
        w["stage"] = 3
        if w.get("qty") is not None:
            w["qty"] = 0
