"""
neolttwigi.py — '널뛰기장' 매수 후보 알림 (2026-11-20, 대장 규칙)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
대장 규칙(11-20, SCENARIOS 7-8): 국면(20일선 위 종목 비율) 35% 미만인 동안은 '널뛰기장' —
  · 매수: 코스피 -3% 이하 급락일, 장 최저점을 찍고 난 뒤(종가가 최저점이면 애프터장) — 대장이 직접 사서
          리나에 `단타 종목 금액 D 널` / `등록 종목 평단 N주 D 널`로 등록
  · 다음 날: 프리장에서 무너지면 대장이 바로 최소 손절, 버티면 리나 일반 규칙 → 15:10 남은 물량 전부 정리(리나 자동)
  · 외국인 매도 규모는 판단 조건에서 뺌(대장: 이미 수백조 판 뒤라 단순하게) — 알림에 참고 숫자로만.
검증(backtest/neolttwigi_validate.py): 국면<35% & 코스피 -3%↓ 27일 — 지수 다음 날 종가 약 +0.5%, 이틀째 약 +1.9%,
  고무공형 종목은 다음 날 장중 최고 평균 +3.8%인데 종가엔 밀림 → 반등일 장중·15:10 정리가 핵심.
리나가 하는 일(1분마다, 거래일 10:00~15:35):
  ① 장중: 코스피 오늘 저가가 전일 대비 -3% 이하 & 저가를 찍은 뒤 MIN_HOLD분 동안 새 저가 없음 & 지금이 저가보다
     BOUNCE% 이상 위 → '최저점 통과' 알림(하루 1번) — 고무공 상위 후보와 함께
  ② 장 마감 직후(15:31~): 종가 -3% 이하이고 종가가 저가 근처(CLOSE_AT_LOW% 안) → '종가가 최저점, 애프터장 매수' 알림
  `!널뛰기` — 지금 상태 바로 보기(조건 미달이어도 숫자).
"""
import datetime
import os
import sys

import requests

_HERE = os.path.dirname(os.path.abspath(__file__))
_BASE = os.path.dirname(_HERE)
for _d in ("core", "intelligence"):
    if os.path.join(_BASE, _d) not in sys.path:
        sys.path.insert(0, os.path.join(_BASE, _d))

REGIME_CUT = 35.0      # 이 미만이면 널뛰기장
DROP = -3.0            # 코스피 저가(장중)·종가 기준 급락
MIN_HOLD = 15          # 저가 찍고 이 분 동안 새 저가 없으면 '통과'
BOUNCE = 0.3           # 저가 대비 이만큼(%) 올라와 있어야
CLOSE_AT_LOW = 0.3     # 종가가 저가에서 이 % 안이면 '종가가 최저점'
UA = {"User-Agent": "Mozilla/5.0"}


def regime():
    """전날 종가 기준 국면(%) — 없으면 None."""
    try:
        import market_regime
        h = market_regime.history(1)
        return float(h[-1][1]) if h else None
    except Exception:
        return None


def snapshot() -> dict:
    """코스피 지금: close·chg·low·low_chg, 외국인 순매수(억). 실패하면 {}."""
    try:
        b = requests.get("https://m.stock.naver.com/api/index/KOSPI/basic", headers=UA, timeout=5).json()
        it = requests.get("https://m.stock.naver.com/api/index/KOSPI/integration", headers=UA, timeout=5).json()
        num = lambda v: float(str(v).replace(",", "")) if v not in (None, "") else None
        close, chg = num(b.get("closePrice")), num(b.get("fluctuationsRatio"))
        ti = {x.get("code"): x.get("value") for x in it.get("totalInfos", [])}
        low = num(ti.get("lowPrice"))
        prev = close / (1 + chg / 100) if close and chg is not None else None
        d = it.get("dealTrendInfo") or {}
        return {"close": close, "chg": chg, "low": low, "low_chg": (low / prev - 1) * 100 if low and prev else None,
                "foreign": num(d.get("foreignValue")), "institution": num(d.get("institutionalValue"))}
    except Exception as e:
        print(f"⚠️ [널뛰기] 지수 조회 실패: {e}")
        return {}


def candidates_text(top: int = 5) -> str:
    try:
        import rubber_ball as rb
        rows = rb.candidates()
    except Exception as e:
        return f"   (고무공 후보 조회 실패: {e})"
    if not rows:
        return "   (고무공 후보 없음 — 대장 눈에 띄는 종목으로)"
    out = []
    for i, r in enumerate(rows[:top], 1):
        today = (f" · 오늘 {r['chg']:+.1f}%" if r.get("chg") is not None else "") + \
                (f"(저가 대비 +{r['bounce']:.1f}%)" if r.get("bounce") else "")
        rb_ = f"과거 급락 뒤 5일 최고 +{r['rb_avg']:.0f}%" if r.get("rb_avg") is not None else "급락 이력 없음"
        out.append(f"   {i}. {'🔥' if r.get('mat') else ''}**{r['name']}**({r['code']}) 1개월 {r['dd1m']:.0f}% · "
                   f"3개월 {r['r3m']:+.0f}% · {rb_}{today}")
    return "\n".join(out)


def _rules() -> str:
    return ("   사면 `단타 종목 금액 D 널` 또는 `등록 종목 평단 N주 D 널` — 다음 날 프리장에서 무너지면 대장이 바로 최소 손절, "
            "버티면 리나 일반 규칙 → **15:10 남은 물량 리나가 전부 정리**(오버나잇 금지)")


def _head(kind: str, s: dict, rg) -> str:
    f = s.get("foreign")
    return (f"🎢 **널뛰기장 {kind}** {datetime.datetime.now():%H:%M} — 코스피 {s['close']:,.2f} ({s['chg']:+.2f}%) · "
            f"저가 {s['low']:,.2f} ({s['low_chg']:+.2f}%) · 국면 {rg:.0f}%(전날)"
            + (f" · 외국인 {f:+,.0f}억(참고)" if f is not None else ""))


def check(state: dict, now: datetime.datetime = None) -> str:
    """1분마다 호출. 알림 낼 때만 메시지, 아니면 "". state는 호출 쪽이 들고 있는 dict(날짜별 초기화)."""
    now = now or datetime.datetime.now()
    day, hhmm = now.date().isoformat(), now.strftime("%H%M")
    if state.get("day") != day:
        state.clear()
        state["day"] = day
    if not ("1000" <= hhmm <= "1535"):
        return ""
    rg = state.get("regime")
    if rg is None:
        rg = state["regime"] = regime()
    if rg is None or rg >= REGIME_CUT:
        return ""
    s = snapshot()
    if not s or s.get("low") is None or s.get("low_chg") is None:
        return ""
    if state.get("low") is None or s["low"] < state["low"] - 1e-9:
        state["low"], state["low_at"] = s["low"], now
    if hhmm <= "1530":
        if state.get("sent_intraday") or s["low_chg"] > DROP:
            return ""
        held = (now - state["low_at"]).total_seconds() / 60
        if held >= MIN_HOLD and s["close"] >= s["low"] * (1 + BOUNCE / 100):
            state["sent_intraday"] = True
            return (_head("최저점 통과", s, rg) + f"\n   저가 찍고 {held:.0f}분째 새 저가 없음 · 저가 대비 "
                    f"+{(s['close'] / s['low'] - 1) * 100:.1f}% — 대장 검토 후 매수\n" + candidates_text() + "\n" + _rules())
        return ""
    # 15:31~15:35 — 종가가 최저점이면 애프터장
    if state.get("sent_close") or s["chg"] > DROP:
        return ""
    if s["close"] <= s["low"] * (1 + CLOSE_AT_LOW / 100):
        state["sent_close"] = True
        return (_head("종가가 최저점", s, rg) + "\n   오늘 저가 근처에서 마감 — **애프터장 매수** 후보\n"
                + candidates_text() + "\n" + _rules())
    return ""


def status() -> str:
    """`!널뛰기` — 지금 상태(조건 미달이어도)."""
    rg, s = regime(), snapshot()
    if not s or s.get("low_chg") is None:
        return "⚠️ 코스피 지수 조회 실패 — 잠시 뒤 다시 `!널뛰기`"
    mode = "🎢 널뛰기장" if (rg is not None and rg < REGIME_CUT) else "🙂 널뛰기장 아님(국면 35% 이상 — 스윙 가능 장)"
    hit = "✅ 급락일 조건(저가 -3%↓) 충족" if s["low_chg"] <= DROP else f"❌ 급락일 아님(저가 {s['low_chg']:+.2f}% > -3%)"
    f = s.get("foreign")
    return (f"{mode} — 국면 {f'{rg:.0f}' if rg is not None else '-'}%(전날) · 코스피 {s['close']:,.2f} ({s['chg']:+.2f}%) · "
            f"저가 {s['low']:,.2f} ({s['low_chg']:+.2f}%)" + (f" · 외국인 {f:+,.0f}억" if f is not None else "")
            + f"\n{hit}\n" + _rules())


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_BASE, ".env"))
    print(status())
