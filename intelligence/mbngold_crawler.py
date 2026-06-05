"""
mbngold_crawler.py — MBN골드 전문가 추천 종목 자동 크롤링
================================================================
[하는 일]
매일 14:30 이후 MBN골드 생쇼 뉴스에서 전문가 추천 종목을 파싱해서
expert_picks DB에 저장하고 nbot/sbot AI 분석 시 가산점 반영.

[URL 구조]
- 목록: https://www.mbngold.com/st/news/news.ls?news_service_id=10020
- 본문: https://www.mbngold.com/st/news/newsview.ls?news_no=MM1005849279&news_service_id=10020

[파싱 데이터]
- 종목코드 (6자리 숫자)
- 종목명
- 매수가 / 목표가 / 손절가 (있는 경우)
- 전문가 이름

[적용]
- nbot/sbot AI 분석 시 전문가 추천 종목 +10~15점 가산
- 키키 !브리핑에 오늘의 추천 종목 표시
================================================================
"""
import os
import re
import sqlite3
import requests
from datetime import datetime, date
from bs4 import BeautifulSoup

# ── 경로 설정 ──────────────────────────────────────────────
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXPERT_DB = os.path.join(_BASE, "intelligence", "expert_picks.db")
os.makedirs(os.path.dirname(EXPERT_DB), exist_ok=True)

# ── MBN골드 URL ────────────────────────────────────────────
BASE_URL    = "https://www.mbngold.com"
LIST_URL    = f"{BASE_URL}/st/news/news.ls?news_service_id=10020"
NEWS_SVC_ID = "10020"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
}


# ============================================================
# DB 초기화
# ============================================================
def init_db():
    conn = sqlite3.connect(EXPERT_DB, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS expert_picks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            date        TEXT NOT NULL,           -- YYYYMMDD
            code        TEXT NOT NULL,           -- 종목코드 6자리
            stock_name  TEXT DEFAULT '',         -- 종목명
            expert_name TEXT DEFAULT '',         -- 전문가명
            buy_price   REAL DEFAULT 0,          -- 매수가
            target_price REAL DEFAULT 0,         -- 목표가
            stop_price  REAL DEFAULT 0,          -- 손절가
            news_no     TEXT DEFAULT '',         -- 뉴스 번호
            created_at  TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ep_date ON expert_picks(date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ep_code ON expert_picks(code)")
    conn.commit()
    conn.close()


# ============================================================
# 뉴스 목록 크롤링
# ============================================================
def fetch_today_news_list(limit: int = 4) -> list:
    """
    오늘 날짜 뉴스 목록 조회.
    MBN골드는 날짜가 HTML에 없어서 뉴스 번호 순서로 상위 N개 = 오늘 것으로 처리.
    하루 4명 × 1건 = 4건 기준 (limit=4)
    반환: [(news_no, title, url), ...]
    """
    try:
        resp = requests.get(LIST_URL, headers=HEADERS, timeout=10)
        resp.encoding = "euc-kr"
        soup = BeautifulSoup(resp.text, "html.parser")

        result = []
        seen   = set()

        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "newsview.ls" not in href:
                continue
            m = re.search(r"news_no=(MM\d+)", href)
            if not m:
                continue
            news_no = m.group(1)
            if news_no in seen:
                continue
            seen.add(news_no)

            title    = a.get_text(strip=True)
            full_url = f"{BASE_URL}/st/news/newsview.ls?news_no={news_no}&news_service_id={NEWS_SVC_ID}"
            result.append((news_no, title, full_url))

            if len(result) >= limit:
                break

        print(f"📰 MBN골드 오늘 뉴스 상위 {len(result)}건 (limit={limit})")
        return result

    except Exception as e:
        print(f"⚠️ MBN골드 목록 조회 오류: {e}")
        return []


# ============================================================
# 뉴스 본문 파싱 → 종목 추출
# ============================================================
def parse_picks_from_news(news_no: str, url: str) -> list:
    """
    뉴스 본문 파싱.
    구조: 종목명(idx-1) → (코드) 매수:X / 목표:X / 손절:X (손절라인)
    """
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.encoding = "euc-kr"
        soup = BeautifulSoup(resp.text, "html.parser")
        lines_txt = [l.strip() for l in soup.get_text(separator="\n").split("\n")
                     if len(l.strip()) > 1]

        # 전문가명
        expert = ""
        title_tag = soup.find("title")
        if title_tag:
            em = re.search(r"\]\s*([가-힣]{2,4})\s*(?:의|씨|전문가)", title_tag.get_text())
            if em:
                expert = em.group(1)

        # 손절 라인 탐색
        for idx, line in enumerate(lines_txt):
            if "손절" not in line or "원" not in line:
                continue

            # 가격 추출
            nums = re.findall(r"[0-9]{2,3}(?:,[0-9]{3})+", line)
            buy_p = target_p = stop_p = 0
            if len(nums) >= 3:
                buy_p    = float(nums[0].replace(",",""))
                target_p = float(nums[1].replace(",",""))
                stop_p   = float(nums[2].replace(",",""))
            elif len(nums) == 2:
                target_p = float(nums[0].replace(",",""))
                stop_p   = float(nums[1].replace(",",""))

            # 코드: 손절 라인 자체에서 (6자리) 추출
            code = ""
            name = ""
            cm = re.search(r"\((\d{6})\)", line)
            if cm:
                code = cm.group(1)
                # 종목명: 바로 위 줄
                if idx - 1 >= 0:
                    candidate = lines_txt[idx - 1].strip()
                    if re.search(r"[가-힣A-Za-z]", candidate) and len(candidate) <= 20:
                        name = candidate
            else:
                # 손절 라인 바로 위 줄에서 (코드) 탐색
                # 예: "(034220) [매수:... / 손절:...]" → 손절이 같은 줄에 있어서 이미 처리됨
                # 위 줄에 코드 있는 경우
                if idx - 1 >= 0:
                    prev = lines_txt[idx - 1]
                    cm2 = re.search(r"\((\d{6})\)", prev)
                    if cm2:
                        code = cm2.group(1)
                        if idx - 2 >= 0:
                            candidate = lines_txt[idx - 2].strip()
                            if re.search(r"[가-힣A-Za-z]", candidate) and len(candidate) <= 20:
                                name = candidate

            if code:
                print(f"  📌 {url[-30:]} → {len([code])}종목 파싱")
                return [( {"code": code, "name": name,
                           "buy": buy_p, "target": target_p, "stop": stop_p},
                          expert )]

        # 폴백
        print(f"  📌 {url[-30:]} → 0종목 파싱")
        return []

    except Exception as e:
        print(f"⚠️ 본문 파싱 오류 ({news_no}): {e}")
        return []


def save_picks(picks_list: list, news_no: str):
    """파싱된 추천 종목 DB 저장"""
    today = date.today().strftime("%Y%m%d")
    conn  = sqlite3.connect(EXPERT_DB, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")

    saved = 0
    for pick, expert in picks_list:
        code = pick["code"]
        # 오늘 이미 저장된 종목이면 스킵
        exists = conn.execute(
            "SELECT id FROM expert_picks WHERE date=? AND code=? AND news_no=?",
            (today, code, news_no)
        ).fetchone()
        if exists:
            continue

        conn.execute("""
            INSERT INTO expert_picks
                (date, code, stock_name, expert_name,
                 buy_price, target_price, stop_price, news_no)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (today, code, pick.get("name",""), expert,
              pick.get("buy",0), pick.get("target",0),
              pick.get("stop",0), news_no))
        saved += 1

    conn.commit()
    conn.close()
    if saved:
        print(f"  💾 {saved}건 저장 완료")


# ============================================================
# 메인 실행
# ============================================================
def run_crawler():
    """오늘 MBN골드 추천 종목 크롤링 실행"""
    print(f"\n🔍 MBN골드 추천 종목 크롤링 시작 [{datetime.now().strftime('%H:%M:%S')}]")
    init_db()

    news_list = fetch_today_news_list()
    if not news_list:
        # 목록 조회 실패 시 최신 뉴스 번호 직접 시도
        print("⚠️ 목록 조회 실패 — 최근 뉴스 번호 직접 시도")
        # 오늘 날짜의 마지막 known 번호 기반으로 탐색
        news_list = _try_latest_news()

    total_picks = 0
    for news_no, title, url in news_list:
        picks = parse_picks_from_news(news_no, url)
        if picks:
            save_picks(picks, news_no)
            total_picks += len(picks)

    print(f"✅ MBN골드 크롤링 완료: {total_picks}개 추천 종목 저장\n")
    return total_picks


def _try_latest_news() -> list:
    """목록 조회 실패 시 최근 뉴스 번호 순차 탐색"""
    # 오늘 날짜 기반으로 뉴스 번호 탐색 (최근 5건)
    result = []
    try:
        resp = requests.get(LIST_URL, headers=HEADERS, timeout=10)
        resp.encoding = "euc-kr"
        # href에서 news_no 추출
        for m in re.finditer(r'news_no=(MM\d+)', resp.text):
            news_no = m.group(1)
            url = f"{BASE_URL}/st/news/newsview.ls?news_no={news_no}&news_service_id={NEWS_SVC_ID}"
            result.append((news_no, "", url))
            if len(result) >= 5:
                break
    except Exception:
        pass
    return result


# ============================================================
# 외부 사용 함수 — 봇에서 호출
# ============================================================
def get_recent_picks(days: int = 1) -> list:
    """
    최근 N일 전문가 추천 종목 조회.
    days=1: 오늘만 / days=2: 오늘+어제 / days=3: 3일치
    반환: [{"code":..., "name":..., "date":..., "target":..., "stop":...}, ...]
    """
    try:
        conn = sqlite3.connect(EXPERT_DB, timeout=5)
        conn.execute("PRAGMA query_only = ON")
        rows = conn.execute("""
            SELECT code, stock_name, expert_name,
                   buy_price, target_price, stop_price, date
            FROM expert_picks
            WHERE date >= date('now', 'localtime', ? || ' days')
            GROUP BY code
            ORDER BY date DESC
        """, (f"-{days-1}",)).fetchall()
        conn.close()
        return [
            {
                "code":   r[0],
                "name":   r[1],
                "expert": r[2],
                "buy":    r[3],
                "target": r[4],
                "stop":   r[5],
                "date":   r[6],
            }
            for r in rows
        ]
    except Exception as e:
        print(f"⚠️ expert_picks 조회 오류: {e}")
        return []


def get_today_picks(date_str: str = None) -> list:
    """오늘 전문가 추천 종목 조회 (하위 호환용)"""
    return get_recent_picks(days=1)


def get_expert_bonus(code: str, bot_type: str = "nbot") -> tuple:
    """
    봇 AI 분석 시 전문가 추천 가산점 조회.

    [유효기간]
    - nbot (단타): D+1까지 (오늘 슬롯 꽉 차면 다음날 매수)
    - sbot (스윙): D+2까지 (스윙은 진입 기회 더 넓게)

    반환: (가산점, 이유)
    - 당일 추천: +15점
    - D+1 추천:  +10점 (약간 감쇠)
    - D+2 추천:  +7점  (더 감쇠)
    - 목표가 15% 이상: +5점 추가
    """
    days = 2 if bot_type == "nbot" else 3  # nbot=D+1, sbot=D+2
    picks = get_recent_picks(days=days)

    from datetime import date as _date
    today_str = _date.today().strftime("%Y%m%d")

    for p in picks:
        if p["code"] == code:
            # 날짜에 따른 가산점 감쇠
            pick_date = p["date"].replace("-", "")
            if pick_date == today_str:
                bonus = 15
                day_label = "당일"
            elif pick_date >= (_date.today() - __import__('datetime').timedelta(days=1)).strftime("%Y%m%d"):
                bonus = 10
                day_label = "D+1"
            else:
                bonus = 7
                day_label = "D+2"

            reason = f"전문가추천[{p['expert'] or 'MBN골드'}]({day_label})"

            # 목표가 괴리율 추가 가산
            if p["target"] > 0 and p["buy"] > 0:
                upside = (p["target"] - p["buy"]) / p["buy"] * 100
                if upside >= 15:
                    bonus += 5
                    reason += f"(목표+{upside:.0f}%)"

            return bonus, reason
    return 0, ""


if __name__ == "__main__":
    run_crawler()
    # 저장된 종목 출력
    picks = get_today_picks()
    if picks:
        print(f"\n📋 오늘의 전문가 추천 종목 ({len(picks)}개):")
        for p in picks:
            print(f"  {p['code']}({p['name']}) "
                  f"매수:{p['buy']:,.0f} 목표:{p['target']:,.0f} "
                  f"손절:{p['stop']:,.0f} [{p['expert']}]")
    else:
        print("오늘 추천 종목 없음")
