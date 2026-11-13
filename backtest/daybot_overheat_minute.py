"""
daybot_overheat_minute.py — '과열 제외' 필터를 분봉으로 재검증 (2026-11-14, 대장 "dbot은 분봉이 있어야 제대로 테스트")
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
일봉 재검증(candidate_pool_filter_test.py)에서 '과열(1달 +20%↑ 또는 3달 +80%↑) 종목 제외'가 유일하게 앞·뒤 기간 모두
버텼다. 그런데 일봉으론 '몇 시에 사서, 그 뒤 손절선을 먼저 찍었나 익절을 먼저 찍었나'를 모름.
→ daybot_replay.py(10-08 분봉 재현 엔진)로 신호가 뜬 그 분부터 1분봉으로 실제 매도 규칙을 다시 돌림:
  · 신호: cands(daybot 후보로그) + obs(리나 파이썬판 검색식 통과 기록 — daybot 슬롯이 차서 못 본 것까지)
  · 매도 규칙 두 가지: 현행(손절 -3.5%·트레일링·3영업일) / 당일 청산(15:20까지 못 타면 그날 정리 — 대장 "그날 승부")
  · 과열 판정은 신호 날 '전날까지' 일봉으로(buy_feature_analysis와 같음)
결과: 과열 걸림 vs 통과 묶음의 승률·평균(수수료 0.2% 포함), 기간 앞/뒤 반 각각.
실행: venv/bin/python backtest/daybot_overheat_minute.py [최근 N일, 기본 45] [--src cands|obs|both]
"""
import os
import sys
from dataclasses import replace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import buy_feature_analysis as bfa  # noqa: E402
import daybot_replay as dr  # noqa: E402

OVERHEAT_R20, OVERHEAT_R60 = 20.0, 80.0


def overheat_flags(signals: list) -> None:
    """각 신호에 s['r20'], s['r60'], s['hot'] (제자리). 일봉 부족하면 hot=None."""
    cache = bfa._cache()
    for s in signals:
        b = bfa.bars(s["code"], s["date"], cache)
        prev = [x for x in b if x[0] < s["date"]]
        if len(prev) < 61:
            s["hot"] = None
            continue
        c = [x[4] for x in prev]
        s["r20"] = (c[-1] / c[-21] - 1) * 100
        s["r60"] = (c[-1] / c[-61] - 1) * 100
        s["hot"] = s["r20"] >= OVERHEAT_R20 or s["r60"] >= OVERHEAT_R60


def show(label: str, res: list):
    days = sorted({r["date"] for r in res})
    mid = days[len(days) // 2] if days else ""
    print(f"\n■ {label} — 전체 {dr.fmt(dr.stats([r['ret'] for r in res]))}")
    for lab, part in (("전체", res), (f"앞반(~{mid})", [r for r in res if r["date"] < mid]),
                      ("뒷반", [r for r in res if r["date"] >= mid])):
        ok = [r["ret"] for r in part if r.get("hot") is False]
        ng = [r["ret"] for r in part if r.get("hot") is True]
        print(f"   {lab:<15} 통과 {dr.fmt(dr.stats(ok))}")
        print(f"   {'':<15} 과열 {dr.fmt(dr.stats(ng))}")


def main():
    n = next((int(a) for a in sys.argv[1:] if a.isdigit()), 45)
    src = sys.argv[sys.argv.index("--src") + 1] if "--src" in sys.argv else "both"
    sys.path.insert(0, os.path.join(bfa.BASE, "core"))
    from dotenv import load_dotenv
    load_dotenv(os.path.join(bfa.BASE, ".env"))
    from kis_api import KisAPI
    store = dr.MinuteStore(KisAPI())
    srcs = ["cands", "obs"] if src == "both" else [src]
    rules = [("현행 규칙", dr.CURRENT), ("당일 청산(15:20)", replace(dr.CURRENT, eod="152000"))]
    for sname in srcs:
        sig = dr.load_signals(sname, n)
        overheat_flags(sig)
        sig = [s for s in sig if s.get("hot") is not None]
        hot = sum(s["hot"] for s in sig)
        print(f"\n━━━ 신호 {sname}: {len(sig)}개(최근 {n}일, 종목·날짜별 첫 신호) · 과열 {hot}개 ━━━")
        for rlabel, rule in rules:
            res = dr.run_signals(store, sig, rule)          # ret은 수수료·세금 0.2% 뺀 값(daybot_replay.simulate)
            show(f"{sname} · {rlabel} (분봉 재현 {len(res)}개, 한투 호출 누적 {store.calls}회)", res)


if __name__ == "__main__":
    main()
