"""
sports_crawler.py — 네이버 스포츠 KBO 경기결과 크롤러
"""

import re
import datetime
from bs4 import BeautifulSoup


TEAM_ALIASES = {
    "기아": "KIA", "kia": "KIA", "KIA": "KIA", "타이거즈": "KIA",
    "한화": "한화", "이글스": "한화",
    "삼성": "삼성", "라이온즈": "삼성",
    "lg": "LG", "LG": "LG", "트윈스": "LG",
    "두산": "두산", "베어스": "두산",
    "롯데": "롯데", "자이언츠": "롯데",
    "ssg": "SSG", "SSG": "SSG", "랜더스": "SSG",
    "nc": "NC", "NC": "NC", "다이노스": "NC",
    "kt": "KT", "KT": "KT", "위즈": "KT",
    "키움": "키움", "히어로즈": "키움",
}


def get_kbo_results(date: str = None) -> list:
    """
    KBO 경기결과 조회.
    date: "20260506" 형식 (기본값: 오늘)
    반환: [{"winner": "한화", "loser": "KIA", "winner_score": "7", "loser_score": "2", "stadium": "광주"}, ...]
    """
    try:
        from playwright.sync_api import sync_playwright

        if not date:
            date = datetime.date.today().strftime("%Y%m%d")

        url = f"https://m.sports.naver.com/kbaseball/schedule/index?date={date}"

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page    = browser.new_page()
            page.goto(url, wait_until="networkidle", timeout=15000)
            page.wait_for_timeout(2000)
            content = page.content()
            browser.close()

        soup    = BeautifulSoup(content, "html.parser")
        matches = [li for li in soup.find_all("li")
                   if "MatchBox_match_item__WiPhj" in " ".join(li.get("class", []))]

        results = []
        for match in matches:
            try:
                # 승리팀 div
                winner_div = match.find("div", class_=lambda x:
                    isinstance(x, list) and any("type_winner" in c for c in x))
                # 패배팀 div
                loser_div  = match.find("div", class_=lambda x:
                    isinstance(x, list) and any("type_loser"  in c for c in x))

                if not winner_div or not loser_div:
                    continue

                # 팀명 (name_info 클래스)
                def get_team_name(div):
                    name_tag = div.find("div", class_=lambda x:
                        isinstance(x, list) and any("name_info" in c for c in x))
                    if name_tag:
                        # 홈 마크 제거
                        home_tag = name_tag.find("span", class_=lambda x:
                            isinstance(x, list) and any("home_mark" in c for c in x))
                        if home_tag:
                            home_tag.decompose()
                        return name_tag.get_text(strip=True)
                    return ""

                # 스코어 (score_wrap 클래스)
                def get_score(div):
                    score_tag = div.find("div", class_=lambda x:
                        isinstance(x, list) and any("score_wrap" in c for c in x))
                    if score_tag:
                        text = score_tag.get_text(strip=True)
                        m    = re.search(r"스코어(\d+)", text)
                        return m.group(1) if m else "?"
                    return "?"

                winner_name  = get_team_name(winner_div)
                loser_name   = get_team_name(loser_div)
                winner_score = get_score(winner_div)
                loser_score  = get_score(loser_div)

                # 경기장
                stadium_tag = match.find("div", class_=lambda x:
                    isinstance(x, list) and any("stadium" in c for c in x))
                stadium = ""
                if stadium_tag:
                    st_text = stadium_tag.get_text(strip=True)
                    st_match = re.search(r"경기장(.+)", st_text)
                    if st_match:
                        stadium = st_match.group(1).strip()

                if winner_name and loser_name:
                    results.append({
                        "winner":       winner_name,
                        "loser":        loser_name,
                        "winner_score": winner_score,
                        "loser_score":  loser_score,
                        "stadium":      stadium,
                    })
            except Exception as e:
                print(f"  파싱 오류: {e}")
                continue

        return results

    except Exception as e:
        print(f"⚠️ KBO 크롤링 오류: {e}")
        return []


def get_team_result(team: str, date: str = None) -> str:
    """특정 팀의 오늘 경기결과 반환"""
    normalized = TEAM_ALIASES.get(team, team)
    results    = get_kbo_results(date)

    if not results:
        return f"오늘 KBO 경기 결과를 가져오지 못했어요."

    for r in results:
        winner = r["winner"]
        loser  = r["loser"]

        if normalized in winner or team in winner:
            return (
                f"⚾ {winner} {r['winner_score']} : {r['loser_score']} {loser} "
                f"**승리** 🎉" + (f" | {r['stadium']}" if r['stadium'] else "")
            )
        elif normalized in loser or team in loser:
            return (
                f"⚾ {loser} {r['loser_score']} : {r['winner_score']} {winner} "
                f"**패배** 😢" + (f" | {r['stadium']}" if r['stadium'] else "")
            )

    return f"오늘 {team} 경기가 없거나 아직 진행 중이에요."


def format_all_results(date: str = None) -> str:
    """오늘 전체 KBO 경기결과 포맷"""
    results = get_kbo_results(date)
    if not results:
        return "오늘 KBO 경기 결과를 가져오지 못했어요."

    today = datetime.date.today().strftime("%Y년 %m월 %d일")
    lines = [f"⚾ **KBO 오늘 경기결과** [{today}]"]
    for r in results:
        lines.append(
            f"  {r['winner']} {r['winner_score']} : {r['loser_score']} {r['loser']}"
            + (f" ({r['stadium']})" if r['stadium'] else "")
        )
    return "\n".join(lines)


if __name__ == "__main__":
    print("⚾ KBO 오늘 경기결과 테스트...")
    print(format_all_results())
    print()
    print(get_team_result("기아"))
