"""
judal_probe.py — 주달(judal.co.kr) 페이지 구조 확인용 (2026-10-10, 수집기 만들기 전 1회용)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

대장: 관심그룹 재편에 주달의 테마 분류를 쓰자. 클라우드 작업 환경에선 주달 접속이
막혀 있어서, 서버에서 이걸 한 번 돌려 페이지 구조(테마 목록·테마별 종목표 모양,
robots.txt)를 찍어 보고 그 결과로 수집기를 만든다. 아무것도 저장·변경하지 않는다.
실행:  python intelligence/judal_probe.py            (첫 화면 + robots.txt)
       python intelligence/judal_probe.py <URL>      (그 페이지 하나 — 테마 상세 등)
"""
import re
import sys
from collections import Counter

import requests

BASE = "https://www.judal.co.kr"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}


def show(url: str) -> None:
    r = requests.get(url, headers=UA, timeout=15)
    r.encoding = r.apparent_encoding or r.encoding
    html = r.text
    print(f"\n===== {url} → {r.status_code}, {len(html):,}자, {r.headers.get('content-type')}")
    title = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
    print("제목:", title.group(1).strip()[:100] if title else "-")
    links = re.findall(r'href="([^"#]+)"[^>]*>(.*?)</a>', html, re.S | re.I)
    pats = Counter(re.sub(r"\d+", "N", re.sub(r"\?.*", "?…", h))[:60] for h, _ in links)
    print(f"링크 {len(links)}개 — 주소 모양 상위:")
    for p, n in pats.most_common(15):
        print(f"   {n:>4}  {p}")
    print("예시 링크(텍스트 있는 것 20개):")
    shown = 0
    for h, t in links:
        t = re.sub(r"<[^>]+>|\s+", " ", t).strip()
        if t and shown < 20:
            print(f"   {t[:24]:<24} → {h[:90]}")
            shown += 1
    tables = re.findall(r"<table[^>]*>(.*?)</table>", html, re.S | re.I)
    print(f"표 {len(tables)}개")
    for k, tb in enumerate(tables[:3]):
        heads = [re.sub(r"<[^>]+>|\s+", " ", h).strip() for h in re.findall(r"<th[^>]*>(.*?)</th>", tb, re.S | re.I)]
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", tb, re.S | re.I)
        print(f"   표{k + 1}: 머리 {heads[:10]} · 행 {len(rows)}개")
        for row in rows[1:3]:
            cells = [re.sub(r"<[^>]+>|\s+", " ", c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S | re.I)]
            print(f"      {cells[:8]}")
    codes = set(re.findall(r"(?<!\d)(\d{6})(?!\d)", html))
    print(f"6자리 숫자(종목코드 후보) {len(codes)}개 · 예: {sorted(codes)[:8]}")
    apis = set(re.findall(r'["\'](/[a-zA-Z0-9_/\-]*(?:api|json|ajax)[a-zA-Z0-9_/\-\.]*)', html, re.I))
    if apis:
        print("API로 보이는 주소:", sorted(apis)[:10])
    if len(html) < 3000 or "<script" in html and len(tables) == 0:
        print("※ 표가 없고 스크립트 위주 — 자바스크립트로 그리는 페이지일 수 있음")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        show(sys.argv[1])
    else:
        try:
            rb = requests.get(f"{BASE}/robots.txt", headers=UA, timeout=10)
            print(f"===== robots.txt → {rb.status_code}\n{rb.text[:1500]}")
        except Exception as e:
            print(f"robots.txt 실패: {e}")
        show(BASE + "/")
