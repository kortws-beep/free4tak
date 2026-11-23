"""
stock_name_alias.py — 한글로 적힌 알파벳 종목명 ↔ 영문 약칭 자동표 (2026-11-23, 대장)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
거래소엔 "에이치브이엠"·"케이엠더블유"·"한국피아이엠"처럼 알파벳을 한글 발음으로 적은 이름이 많은데,
방송·텔레그램·AI는 "HVM"·"KMW"·"한국PIM"으로 부름 → 종목명 검사기가 못 찾거나 한 글자 차이 종목(HMM)으로 잘못 고침.
(11-23 매경 텔레그램 코너 HVM→HMM 해운 오보정, 그 전에 SPG·KMW·TFE·한국PIM을 틀릴 때마다 하나씩 등록)
→ 전체 종목 이름에서 알파벳 발음(에이=A, 비=B, … 더블유=W)이 2글자 이상 이어진 부분을 영문으로 바꾼 약칭을 자동으로 만든다.
  · 에이치브이엠 → HVM, 엘에스머트리얼즈 → LS머트리얼즈, 한국피아이엠 → 한국PIM
  · 약칭이 실제 다른 종목 이름과 같으면(예: HMM) 진짜 종목이 우선 — 자동 약칭은 버림
  · 두 종목이 같은 약칭이 되면 헷갈리니 둘 다 버림
"""
import re

# 긴 것부터 맞춰야 "에이치"가 "에이"+"치"로 안 쪼개짐
LETTERS = [
    ("더블유", "W"), ("에이치", "H"),
    ("에이", "A"), ("에프", "F"), ("에스", "S"), ("엑스", "X"), ("브이", "V"), ("제이", "J"), ("케이", "K"),
    ("아이", "I"), ("와이", "Y"), ("제트", "Z"),
    ("비", "B"), ("씨", "C"), ("디", "D"), ("이", "E"), ("지", "G"), ("엘", "L"), ("엠", "M"), ("엔", "N"),
    ("오", "O"), ("피", "P"), ("큐", "Q"), ("알", "R"), ("티", "T"), ("유", "U"),
]


def _letters_at(s: str, i: int):
    """s[i:]에서 알파벳 발음을 최대한 길게 이어 읽기 → (영문, 끝 위치)."""
    out, j = "", i
    while j < len(s):
        for ko, en in LETTERS:
            if s.startswith(ko, j):
                out += en
                j += len(ko)
                break
        else:
            break
    return out, j


def to_alias(name: str) -> str:
    """알파벳 발음이 2글자 이상 이어진 부분만 영문으로. 바뀐 게 없으면 ""."""
    res, i, changed = "", 0, False
    while i < len(name):
        en, j = _letters_at(name, i)
        if len(en) >= 2 and re.search(r"[^AEIOUY]", en):   # 바이오→바EO 같은 모음만 덩어리는 제외
            res += en
            i = j
            changed = True
        else:
            res += name[i]
            i += 1
    return res if changed else ""


def build(names) -> dict:
    """{약칭(공백 없음): 진짜 종목명} — 실제 종목명과 겹치거나 두 종목이 같은 약칭이면 뺌."""
    real = {n.replace(" ", "") for n in names}
    table, dup = {}, set()
    for n in names:
        a = to_alias(n.replace(" ", ""))
        if not a or not re.search(r"[A-Z].*[A-Z]", a) or a in real:
            continue
        if a in table and table[a] != n:
            dup.add(a)
        table[a] = n
    for a in dup:
        table.pop(a, None)
    return table


if __name__ == "__main__":
    import os
    import sqlite3
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import youtube_stock_monitor as Y
    c = sqlite3.connect(Y.THEME_DB)
    t = build([r[0] for r in c.execute("SELECT name FROM kr_stock_master")])
    print(len(t), "개")
    for k in sorted(t)[: int(sys.argv[1]) if len(sys.argv) > 1 else 40]:
        print(f"{k:16s} → {t[k]}")
