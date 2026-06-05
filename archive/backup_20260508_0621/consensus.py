"""
consensus.py — 한경컨센서스 애널리스트 리포트 조회
========================================================
[기능]
  - 특정 종목의 최근 N일 리포트 수 조회
  - 투자의견 (BUY/HOLD/SELL) 집계
  - 목표주가 평균 계산
  - 가점 계산

[가점 기준]
  최근 7일 리포트 1개  → +3점
  최근 7일 리포트 3개+ → +7점
  최근 7일 리포트 5개+ → +10점
  투자의견 BUY 비율 80%+ → +5점 추가
  목표주가 > 현재가 20%+ → +5점 추가
"""

import datetime
import requests
from bs4 import BeautifulSoup


BASE_URL = "http://consensus.hankyung.com/analysis/list"
HEADERS  = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
}

# 가점 기준
REPORT_DAYS    = 7     # 최근 N일 리포트 수 기준
BONUS_1        = 3     # 리포트 1개+
BONUS_3        = 7     # 리포트 3개+
BONUS_5        = 10    # 리포트 5개+
BONUS_BUY      = 5     # BUY 비율 80%+
BONUS_TARGET   = 5     # 목표주가 현재가 대비 20%+


def get_consensus(code: str, current_price: float = 0,
                  days: int = REPORT_DAYS) -> dict:
    """
    한경컨센서스에서 종목 리포트 정보 조회.

    반환:
    {
        "report_count": 2,        # 최근 N일 리포트 수
        "buy_count":    2,        # BUY 의견 수
        "hold_count":   0,        # HOLD 의견 수
        "sell_count":   0,        # SELL 의견 수
        "avg_target":   180000,   # 평균 목표주가
        "bonus":        10,       # 가점
        "reason":       "리포트2건(+7)|BUY100%(+5)",
    }
    """
    result = {
        "report_count": 0,
        "buy_count":    0,
        "hold_count":   0,
        "sell_count":   0,
        "avg_target":   0,
        "bonus":        0,
        "reason":       "",
    }

    try:
        today    = datetime.date.today()
        sdate    = (today - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
        edate    = today.strftime("%Y-%m-%d")

        params = {
            "skinType":      "business",
            "search_text": code,
            "sdate":         sdate,
            "edate":         edate,
            "report_type":   "CO",
            "pagenum":       "50",
        }

        res  = requests.get(BASE_URL, headers=HEADERS, params=params, timeout=8)
        soup = BeautifulSoup(res.text, "html.parser")

        rows = soup.select("div.table_style01 table tbody tr")
        if not rows:
            return result

        targets  = []
        buy_cnt  = 0
        hold_cnt = 0
        sell_cnt = 0

        for row in rows:
            cols = row.find_all("td")
            if len(cols) < 4: continue

            # 투자의견
            opinion = cols[3].get_text(strip=True).upper()
            if "BUY" in opinion or "매수" in opinion:
                buy_cnt += 1
            elif "HOLD" in opinion or "중립" in opinion or "NEUTRAL" in opinion:
                hold_cnt += 1
            elif "SELL" in opinion or "매도" in opinion:
                sell_cnt += 1

            # 목표주가
            target_text = cols[2].get_text(strip=True).replace(",", "").replace(" ", "")
            try:
                if target_text and target_text.isdigit():
                    targets.append(int(target_text))
            except Exception:
                pass

        total = buy_cnt + hold_cnt + sell_cnt
        result["report_count"] = total
        result["buy_count"]    = buy_cnt
        result["hold_count"]   = hold_cnt
        result["sell_count"]   = sell_cnt
        result["avg_target"]   = int(sum(targets) / len(targets)) if targets else 0

        # ── 가점 계산 ─────────────────────────────────────
        bonus  = 0
        reason = ""

        if   total >= 5: bonus += BONUS_5;  reason += f"|리포트{total}건(+{BONUS_5})"
        elif total >= 3: bonus += BONUS_3;  reason += f"|리포트{total}건(+{BONUS_3})"
        elif total >= 1: bonus += BONUS_1;  reason += f"|리포트{total}건(+{BONUS_1})"

        # BUY 비율 80% 이상
        if total > 0 and buy_cnt / total >= 0.8:
            bonus  += BONUS_BUY
            reason += f"|BUY{int(buy_cnt/total*100)}%(+{BONUS_BUY})"

        # 목표주가 현재가 대비 20% 이상
        if result["avg_target"] > 0 and current_price > 0:
            upside = (result["avg_target"] - current_price) / current_price
            if upside >= 0.20:
                bonus  += BONUS_TARGET
                reason += f"|목표가상승여력{int(upside*100)}%(+{BONUS_TARGET})"

        result["bonus"]  = bonus
        result["reason"] = reason.lstrip("|")

        if total > 0:
            print(
                f"  📋 컨센서스 {code} | {days}일내 {total}건 | "
                f"BUY:{buy_cnt} HOLD:{hold_cnt} SELL:{sell_cnt} | "
                f"목표가:{result['avg_target']:,}원 | 가점:+{bonus}"
            )

    except Exception as e:
        print(f"⚠️ 컨센서스 조회 오류 {code}: {e}")

    return result


def apply_consensus_bonus(code: str, score: int,
                          current_price: float = 0) -> tuple:
    """
    한경컨센서스 가점 적용.
    반환: (보정 점수, 보정 이유)
    """
    data = get_consensus(code, current_price)
    if data["bonus"] == 0:
        return score, ""

    new_score = min(100, score + data["bonus"])
    reason    = data["reason"]
    print(f"   📋 컨센서스 보정 {code}: {score}→{new_score}점 | {reason}")
    return new_score, reason


# ============================================================
# 테스트
# ============================================================
if __name__ == "__main__":
    # 테스트: 인텔리안테크 (189300)
    code  = "189300"
    price = 150000
    print(f"🔍 {code} 컨센서스 조회 테스트...")
    result = get_consensus(code, price)
    print(f"결과: {result}")

    score, reason = apply_consensus_bonus(code, 70, price)
    print(f"보정 점수: {score}점 | 이유: {reason}")
