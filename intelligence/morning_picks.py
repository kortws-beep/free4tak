"""
morning_picks.py — 아침 09:10 '오늘의 후보' 한 장: 고무공 · 반도체(김태성) · 방패 (2026-11-26, 대장)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
대장: "!고무공, !반도체, !방패에서 오늘 나오는 종목들을 대시보드에 한 세션으로 만들어 아침 9시 10분쯤 보여주면 좋겠다 —
바쁘면 잊어먹고 지나갈 수 있거든." → 리나가 장 시작 10분 뒤(특별일은 그만큼 밀림) 세 가지를 한 번에 계산해 파일에 저장,
대시보드 🎯 후보 탭이 그 파일을 보여줌. 리나 DM에도 짧게 한 번(상위 몇 개만).
  · 🏀 고무공: intelligence/rubber_ball.py (3개월 주도 유지 & 1개월 고점 -15%↓, 되돌림 성격 순)
  · 🔬 반도체: intelligence/semi_pick.py (소부장 6개월 고점 -50%↓ & 올해 영업이익 증가, 교체 후보, 내 보유 판정)
  · 🛡️ 방패: 대장 관심그룹 '방패_' 분야 — 오늘 분야 등락·종목별 등락, AI 분야 평균과 비교(AI 빠지는데 방패 버티면 헤지 작동 중)
매매 신호 아님 — 대장 판단 재료. 저장: intelligence/morning_picks.json(덮어씀)
실행(수동): venv/bin/python intelligence/morning_picks.py
"""
import datetime
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_BASE = os.path.dirname(_HERE)
for _d in ("core", "intelligence"):
    if os.path.join(_BASE, _d) not in sys.path:
        sys.path.insert(0, os.path.join(_BASE, _d))

OUT = os.path.join(_HERE, "morning_picks.json")
RUN_HHMM = "0910"


def shield(data: dict = None) -> dict:
    """섹터맵 데이터에서 '방패_' 분야 → {"groups":[{group, chg_w, members:[{name,code,chg}]}], "avg", "ai"}."""
    import kr_heatmap
    data = data or kr_heatmap.build(force=True)
    gs = [x for x in data.get("sectors", []) if x["group"].startswith("방패_")]
    out = [{"group": x["group"].replace("방패_", ""), "chg_w": x.get("chg_w"),
            "members": sorted([{"name": m["name"], "code": m["code"], "chg": m["chg"]} for m in x["members"]],
                              key=lambda m: -(m["chg"] if m["chg"] is not None else -99))} for x in gs]
    out.sort(key=lambda g: -(g["chg_w"] if g["chg_w"] is not None else -99))
    sh = data.get("shield") or {}
    return {"groups": out, "avg": sh.get("avg"), "ai": sh.get("ai")}


def format_shield(s: dict) -> str:
    if not s.get("groups"):
        return "🛡️ 방패 분야 없음 — 한투 관심그룹 이름을 '방패_…'로 만들면 잡힘"
    now = datetime.datetime.now().strftime("%H:%M")
    state = ""
    if s.get("avg") is not None and s.get("ai") is not None:
        gap = s["avg"] - s["ai"]
        state = ("  → AI 빠지고 방패 버팀 = 헤지 작동 중 🛡️" if s["ai"] < 0 and gap > 1 else
                 "  → AI 반등일 — 방패는 쉬는 날" if s["ai"] > 0 and gap < 0 else "")
    L = [f"🛡️ **방패** {now} · 방패 평균 {s['avg']:+.2f}% vs 🤖 AI 분야 {s['ai']:+.2f}%{state}"]
    for g in s["groups"]:
        top = " · ".join(f"{m['name']} {m['chg']:+.1f}%" for m in g["members"][:4] if m["chg"] is not None)
        L.append(f"   {g['group']} {g['chg_w']:+.2f}% — {top}")
    L.append("   (방패 = 대장 관심그룹 '방패_' · 11-26 검증: AI만 빠지는 날 진짜 헤지, 시장 전체 투매 땐 덜 빠지는 쿠션)")
    return "\n".join(L)


def run(api=None) -> dict:
    """세 가지 계산 → 파일 저장 → 결과 dict. 하나가 실패해도 나머지는 저장."""
    import kr_heatmap
    out = {"date": datetime.date.today().isoformat(), "at": datetime.datetime.now().strftime("%H:%M")}
    try:
        import rubber_ball
        rows = rubber_ball.candidates(api=api)
        out["rubber"] = [{k: r.get(k) for k in ("name", "code", "groups", "dd1m", "r3m", "rb_avg", "rb_hit", "mat", "chg")}
                         for r in rows[:10]]
    except Exception as e:
        out["rubber_err"] = str(e)
    try:
        import semi_pick
        rows = semi_pick.candidates()
        good = sorted([r for r in rows if r["dd"] <= semi_pick.BUY_DD and semi_pick.grow_ok(r)], key=lambda r: -(r.get("op_diff") or 0))
        pick = lambda r: {k: r.get(k) for k in ("name", "code", "groups", "dd", "op_this", "op_yoy", "op_diff", "y_this", "chg")}
        out["semi"] = {"good": [pick(r) for r in good[:8]],
                       "swap": [pick(r) for r in rows if r["dd"] <= semi_pick.BUY_DD and not semi_pick.grow_ok(r)][:6],
                       "hold": [list(h) for h in semi_pick.holdings_view(rows)]}
    except Exception as e:
        out["semi_err"] = str(e)
    try:
        out["shield"] = shield(kr_heatmap.build(force=True))
    except Exception as e:
        out["shield_err"] = str(e)
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    os.replace(tmp, OUT)
    return out


def load() -> dict:
    try:
        with open(OUT, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def brief(d: dict) -> str:
    """리나 DM 짧은 알림 — 각 3개."""
    L = [f"🎯 **오늘의 후보** {d.get('at', '')} — 자세히는 대시보드 🎯 후보 탭"]
    r = d.get("rubber") or []
    L.append("🏀 고무공: " + (", ".join(f"{x['name']}({x['dd1m']:.0f}%)" for x in r[:3]) if r else "없음"))
    g = (d.get("semi") or {}).get("good") or []
    L.append("🔬 반도체: " + (", ".join(f"{x['name']}({x['dd']:.0f}%)" for x in g[:3]) if g else "없음"))
    s = d.get("shield") or {}
    if s.get("groups"):
        L.append(f"🛡️ 방패: 평균 {s['avg']:+.2f}% vs AI {s['ai']:+.2f}% · " + ", ".join(f"{x['group']} {x['chg_w']:+.1f}%" for x in s["groups"][:3]))
    return "\n".join(L)


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_BASE, ".env"))
    d = run()
    print(brief(d))
    print(format_shield(d.get("shield") or {}))
