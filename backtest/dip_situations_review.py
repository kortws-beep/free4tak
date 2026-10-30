"""
dip_situations_review.py — 음봉 엔진 상황별 성적 (2026-10-31, 대장 "바닥권 하락 등 여러 상황 대입")
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
dip_situations_prep.py가 만든 backtest/data/dip_situations.json(거래마다 그날 상황 꼬리표)을
상황별로 나눠 성적을 본다 — "어떤 장에서만 음봉 엔진을 켜면 되나".
  나누는 기준: 국면 깊이(전날 20일선 위 비율) · 반등 조짐(국면이 전날보다 오름) · 지수 연속 하락일수 ·
               지수 20일 고점 대비 낙폭 · 지수 60일선 위/아래 · 조합(바닥권 & 반등 조짐 등)
  표마다: 건수 · 승률 · 평균 · 앞 절반/뒤 절반 평균(둘 다 +여야 믿을 만함)
실행: venv/bin/python backtest/dip_situations_review.py [매도규칙 이름, 기본 '현행 +3/-3.5']
"""
import os
import sys
import json

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(BASE, "backtest", "data", "dip_situations.json")


def depth(b):
    return "1 <20 바닥권" if b < 20 else "2 20~35" if b < 35 else "3 35~50" if b < 50 else "4 50+"


def streak(s):
    return "0일" if s == 0 else "1일" if s == 1 else "2일" if s == 2 else "3일+"


def dd(x):
    return "1 -15%↓" if x <= -15 else "2 -10~-15%" if x <= -10 else "3 -5~-10%" if x <= -5 else "4 -5% 이내"


GROUPS = [
    ("국면 깊이", lambda r: depth(r["breadth"])),
    ("국면 반등 조짐(전날보다 오름)", lambda r: "오름" if r["breadth_up"] else "내림"),
    ("지수 연속 하락일수(전날까지)", lambda r: streak(r.get("streak", 0))),
    ("지수 20일 고점 대비", lambda r: dd(r.get("dd20", 0))),
    ("지수 60일선", lambda r: "위" if r.get("above60") else "아래"),
    ("바닥권 × 반등 조짐", lambda r: ("바닥권" if r["breadth"] < 20 else "그 외") + " · " + ("오름" if r["breadth_up"] else "내림")),
    ("60일선 × 국면", lambda r: ("60일선 위" if r.get("above60") else "60일선 아래") + " · " + ("국면<35" if r["breadth"] < 35 else "국면35+")),
]


def line(label, xs, exit_name):
    rs = [x["ret"][exit_name] for x in sorted(xs, key=lambda x: x["date"])]
    n = len(rs)
    if not n:
        return None
    h = n // 2
    a1 = sum(rs[:h]) / h if h else float("nan")
    a2 = sum(rs[h:]) / (n - h)
    ok = "✅" if n >= 8 and a1 > 0 and a2 > 0 else ""
    return (f"   {label:<22}{n:>4}건 승률{sum(r > 0 for r in rs) / n * 100:>4.0f}% 평균{sum(rs) / n:>+6.2f}%"
            f"  앞{a1:>+6.2f} 뒤{a2:>+6.2f} {ok}")


def main():
    exit_name = next((a for a in sys.argv[1:]), "현행 +3/-3.5")
    rec = json.load(open(DATA, encoding="utf-8"))
    print(f"매도 규칙: {exit_name}  (✅ = 8건↑ & 앞뒤 절반 모두 플러스)")
    for entry in ("반등+0.5%(현행)", "14:30 고정"):
        for flt in ("전체", "NEW·SUB"):
            xs = [r for r in rec if r["entry"] == entry and r["filter"] == flt]
            print(f"\n■ {entry} · {flt}")
            print(line("전체", xs, exit_name))
            for title, key in GROUPS:
                print(f"  [{title}]")
                for k in sorted({key(r) for r in xs}):
                    out = line(k, [r for r in xs if key(r) == k], exit_name)
                    if out:
                        print(out)


if __name__ == "__main__":
    sys.exit(main())
