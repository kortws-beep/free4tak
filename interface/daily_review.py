"""
daily_review.py — 자동 일일 복기 시스템
================================================================
[하는 일]
장 종료 후(16:00) 오늘 매매를 자동 분석하여:
  1. Claude가 오늘 매매를 복기 (잘한 점 / 못한 점 / 내일 전략)
  2. 복기 결과를 ai_analyzer.py의 내일 프롬프트에 자동 반영
  3. 디스코드로 저녁 복기 리포트 전송

[핵심 철학]
  - 봇이 오늘의 실수를 기억하고 내일 개선
  - AI 점수 판단에 "어제의 교훈"이 반영됨
  - 수동 개입 없이 자동으로 전략이 진화

[실행 방법]
  1. 직접: python3 daily_review.py
  2. 자동: cron 또는 kiki의 저녁 브리핑에 통합

[cron 등록 (평일 16:10)]
  10 16 * * 1-5 cd /home/free4tak/k-bot/stock_bot && \
      /home/free4tak/k-bot/stock_bot/venv/bin/python3 daily_review.py \
      >> /tmp/daily_review.log 2>&1
"""
import sys as _sys
import os as _os
_BASE = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
for _d in ["core", "intelligence", "interface", "bots", ""]:
    _p = _os.path.join(_BASE, _d)
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

import os
import sys
import json
import sqlite3
import datetime

# 프로젝트 루트
_here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _here)

from dotenv import load_dotenv
# .env 경로 탐색 (interface/ 또는 stock_bot/)
for _ep in [os.path.join(_here, ".env"), os.path.join(os.path.dirname(_here), ".env")]:
    if os.path.exists(_ep):
        load_dotenv(_ep, override=True)
        break

from performance import PerformanceAnalyzer
from anthropic import Anthropic

# ============================================================
# 설정
# ============================================================
TRADE_DB     = os.path.join(os.path.dirname(_here), "master_trades.db")
REVIEW_FILE  = os.path.join(_BASE, "daily_review.json")   # 복기 결과 저장
AI_CACHE_DB  = os.path.join(_BASE, "ai_cache.db")

ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")


# ============================================================
# 오늘 복기 데이터 수집
# ============================================================
def get_today_summary() -> dict:
    """오늘 매매 요약 데이터 수집"""
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    pa = PerformanceAnalyzer(TRADE_DB)

    # 오늘 거래만
    try:
        conn = sqlite3.connect(TRADE_DB, timeout=10)
        conn.execute("PRAGMA query_only = ON")
        cur = conn.execute("PRAGMA table_info(master_trades)").fetchall()
        cols = [c[1] for c in cur]
        c_code = "market as code" if "market" in cols and "code" not in cols else "code"
        c_score = "ai_score" if "ai_score" in cols else "0 as ai_score"

        rows = conn.execute(f"""
            SELECT {c_code}, buy_price, sell_price, qty,
                   profit_rate, sell_reason, buy_time, sell_time,
                   {c_score}, '' as market_status
            FROM master_trades
            WHERE sell_price IS NOT NULL AND sell_price > 0
              AND profit_rate > -99
              AND date(sell_time) = ?
            ORDER BY sell_time
        """, (today,)).fetchall()
        conn.close()
    except Exception as e:
        print(f"⚠️ DB 조회 오류: {e}")
        return {}

    if not rows:
        return {"today": today, "trades": 0, "message": "오늘 완료 거래 없음"}

    # 기본 통계
    profits = [r[4] for r in rows if r[4] is not None]
    wins    = [p for p in profits if p >= 0]
    losses  = [p for p in profits if p < 0]

    win_rate  = len(wins) / len(profits) * 100 if profits else 0
    avg_prof  = sum(profits) / len(profits) if profits else 0
    total_pnl = sum(r[2] * r[3] - r[1] * r[3] for r in rows
                    if r[1] and r[2] and r[3])

    # 시간대별 분석
    by_hour = pa.by_hour(rows)

    # 매도사유별
    by_reason = pa.by_sell_reason(rows)

    # 종목별
    by_stock = pa.by_stock(rows, top_n=5)

    # 오늘 매매 상세
    trade_details = []
    for r in rows:
        code, bp, sp, qty, pr, reason, bt, st, ai_score, _ = r
        trade_details.append({
            "code":     code,
            "buy_price": bp,
            "sell_price": sp,
            "profit_rate": round(pr, 2),
            "sell_reason": reason,
            "buy_time": bt[11:16] if bt else "",
            "sell_time": st[11:16] if st else "",
            "ai_score": ai_score,
        })

    return {
        "today":        today,
        "trades":       len(profits),
        "wins":         len(wins),
        "losses":       len(losses),
        "win_rate":     round(win_rate, 1),
        "avg_profit":   round(avg_prof, 2),
        "total_pnl":    int(total_pnl),
        "avg_win":      round(sum(wins)/len(wins), 2) if wins else 0,
        "avg_loss":     round(sum(losses)/len(losses), 2) if losses else 0,
        "by_hour":      by_hour,
        "by_reason":    by_reason,
        "by_stock":     by_stock[:5],
        "trade_details": trade_details,
    }


# ============================================================
# 최근 7일 누적 패턴
# ============================================================
def get_weekly_pattern() -> dict:
    """최근 7일 패턴 분석 — 반복되는 실수 감지"""
    pa = PerformanceAnalyzer(TRADE_DB)
    trades = pa._fetch_trades(days=7)
    if not trades:
        return {}

    by_hour   = pa.by_hour(trades)
    by_reason = pa.by_sell_reason(trades)

    # 최악의 시간대
    worst_hour = min(by_hour.items(),
                     key=lambda x: x[1]["avg_profit"]) if by_hour else None
    # 최고의 시간대
    best_hour  = max(by_hour.items(),
                     key=lambda x: x[1]["avg_profit"]) if by_hour else None

    return {
        "period":      "최근 7일",
        "total":       len(trades),
        "by_hour":     by_hour,
        "by_reason":   by_reason,
        "worst_hour":  worst_hour,
        "best_hour":   best_hour,
    }


# ============================================================
# Claude 복기 분석
# ============================================================
def claude_review(today_summary: dict, weekly_pattern: dict) -> dict:
    """Claude가 오늘 매매를 복기하고 내일 전략을 제안"""
    client = Anthropic()

    # 오늘 매매 상세 텍스트
    trades_text = ""
    for t in today_summary.get("trade_details", []):
        emoji = "✅" if t["profit_rate"] >= 0 else "❌"
        trades_text += (
            f"  {emoji} {t['code']} | {t['buy_time']}매수→{t['sell_time']}매도 | "
            f"{t['profit_rate']:+.2f}% | {t['sell_reason']} | AI:{t['ai_score']}점\n"
        )

    # 시간대별 성과 텍스트
    hour_text = ""
    for hour, data in today_summary.get("by_hour", {}).items():
        hour_text += f"  {hour}: {data['trades']}건 승률{data['win_rate']}% 평균{data['avg_profit']:+.2f}%\n"

    # 7일 패턴
    worst = weekly_pattern.get("worst_hour")
    best  = weekly_pattern.get("best_hour")
    pattern_text = ""
    if worst:
        pattern_text += f"  최악 시간대(7일): {worst[0]} 평균{worst[1]['avg_profit']:+.2f}%\n"
    if best:
        pattern_text += f"  최고 시간대(7일): {best[0]} 평균{best[1]['avg_profit']:+.2f}%\n"

    prompt = f"""당신은 한국 단타 트레이딩 전문가입니다.
오늘 봇의 매매 결과를 분석하고 내일을 위한 구체적인 조언을 주세요.

[오늘 {today_summary.get('today')} 매매 결과]
- 총 거래: {today_summary.get('trades', 0)}건
- 승률: {today_summary.get('win_rate', 0)}%
- 평균 수익: {today_summary.get('avg_profit', 0):+.2f}%
- 평균 익절: {today_summary.get('avg_win', 0):+.2f}%
- 평균 손절: {today_summary.get('avg_loss', 0):+.2f}%
- 순손익: {today_summary.get('total_pnl', 0):+,}원

[오늘 매매 상세]
{trades_text if trades_text else "  없음"}

[오늘 시간대별 성과]
{hour_text if hour_text else "  없음"}

[7일 누적 패턴]
{pattern_text if pattern_text else "  데이터 없음"}

다음 JSON 형식으로 답변하세요:
{{
  "오늘평가": "한 줄 총평 (예: 승률 낮고 손절 다수 — 종목 선별 기준 강화 필요)",
  "잘한점": "구체적으로 잘된 것",
  "못한점": "구체적으로 개선이 필요한 것",
  "반복패턴": "7일간 반복되는 실수나 패턴",
  "내일전략": "내일 구체적으로 바꿀 것 (예: 10시 이전 매수 비중 줄이기)",
  "주의종목유형": "내일 피해야 할 종목 특성 (예: RSI 80 이상 과열 종목)",
  "주목시간대": "내일 집중할 시간대",
  "신뢰도": 1-5
}}"""

    import time as _time
    for _retry in range(3):  # ★ 최대 3회 재시도
        try:
            res = client.messages.create(
                model=ANTHROPIC_MODEL,
                max_tokens=600,
                messages=[{"role": "user", "content": prompt}]
            )
            text = res.content[0].text.strip()
            import re
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                result = json.loads(match.group())
                print("✅ Claude 복기 완료")
                return validate_review(result)
            break
        except Exception as e:
            if "overloaded" in str(e).lower() or "529" in str(e):
                wait = 30 * (_retry + 1)
                print(f"⚠️ API 과부하 — {wait}초 후 재시도 ({_retry+1}/3)")
                _time.sleep(wait)
            else:
                print(f"⚠️ Claude 복기 오류: {e}")
                break

    return {
        "오늘평가": "복기 실패",
        "내일전략": "",
        "주의종목유형": "",
        "신뢰도": 0
    }


def validate_review(review: dict) -> dict:
    """
    ★ Death Spiral 방지 가드레일
    AI 복기가 핵심 원칙을 침해하지 않는지 검증.
    극단적 제안은 완화하여 봇이 점점 소극적으로 퇴화하는 것을 방지.
    """
    if not review:
        return review

    strategy = review.get("내일전략", "")
    caution  = review.get("주의종목유형", "")
    trust    = review.get("신뢰도", 3)

    # 1. 매수 완전 금지 → 신중한 진입으로 완화
    danger_words = ["매수 금지", "매매 중단", "전면 중단", "매수 자제",
                    "매수하지 마", "사지 마", "진입 금지"]
    for w in danger_words:
        if w in strategy:
            print(f"⚠️ 가드레일 발동: '{w}' → 완화 처리")
            strategy = strategy.replace(w, "신중한 진입 유지")

    # 2. 신뢰도 낮으면 전략 반영 제한
    if trust <= 1:
        print("⚠️ 가드레일: 신뢰도 너무 낮음 → 기본 전략 유지")
        strategy = "기본 전략 유지 (복기 신뢰도 낮음)"

    # 3. 모든 시간대 금지 → 1개 이상 허용
    if "전면 금지" in strategy and "시간대" in strategy:
        strategy = strategy.replace("전면 금지", "주의")
        print("⚠️ 가드레일: 전면 시간대 금지 → 주의로 완화")

    review["내일전략"]    = strategy
    review["주의종목유형"] = caution
    return review


# ============================================================
# 복기 결과 저장 (→ 내일 ai_analyzer.py가 읽음)
# ============================================================
def save_review(today_summary: dict, review: dict):
    """복기 결과를 JSON으로 저장 — 내일 AI 프롬프트에 주입"""
    data = {
        "date":          today_summary.get("today"),
        "saved_at":      datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "summary":       today_summary,
        "review":        review,
    }
    with open(REVIEW_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"✅ 복기 저장: {REVIEW_FILE}")


# ============================================================
# 복기 결과 로드 (ai_analyzer.py에서 호출)
# ============================================================
def load_yesterday_review() -> dict:
    """
    어제 복기 결과 로드.
    ai_analyzer.py의 _build_prompt()에서 호출하여 프롬프트에 주입.
    """
    if not os.path.exists(REVIEW_FILE):
        return {}
    try:
        with open(REVIEW_FILE, encoding="utf-8") as f:
            data = json.load(f)
        # 오늘 날짜와 다르면 어제 복기 (유효)
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        if data.get("date") != today:
            return data.get("review", {})
    except Exception:
        pass
    return {}


# ============================================================
# 디스코드 전송용 포맷
# ============================================================
def format_discord(today_summary: dict, review: dict) -> str:
    """키키 저녁 브리핑에 삽입할 복기 텍스트"""
    today = today_summary.get("today", "")
    trades = today_summary.get("trades", 0)

    if not trades:
        return f"📋 **{today} 자동 복기**\n오늘 완료 거래 없음"

    win_rate = today_summary.get("win_rate", 0)
    avg_prof = today_summary.get("avg_profit", 0)
    total_pnl = today_summary.get("total_pnl", 0)
    pnl_emoji = "✅" if total_pnl >= 0 else "❌"

    lines = [
        f"📋 **{today} 자동 복기**",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"{pnl_emoji} {trades}건 | 승률 {win_rate}% | 평균 {avg_prof:+.2f}% | 순손익 {total_pnl:+,}원",
        "",
        f"💬 **오늘 평가**: {review.get('오늘평가', '-')}",
        f"✅ **잘한 점**: {review.get('잘한점', '-')}",
        f"⚠️ **못한 점**: {review.get('못한점', '-')}",
        f"🔄 **반복 패턴**: {review.get('반복패턴', '-')}",
        "",
        f"📌 **내일 전략**: {review.get('내일전략', '-')}",
        f"🚫 **주의 종목**: {review.get('주의종목유형', '-')}",
        f"⏰ **집중 시간대**: {review.get('주목시간대', '-')}",
    ]
    return "\n".join(lines)


# ============================================================
# 메인
# ============================================================
def main():
    print(f"\n{'='*50}")
    print(f"  영암9 자동 복기 — {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*50}\n")

    # 1) 오늘 매매 수집
    print("📊 오늘 매매 분석 중...")
    today_summary = get_today_summary()
    if not today_summary or today_summary.get("trades", 0) == 0:
        print("ℹ️ 오늘 완료 거래 없음 — 복기 생략")
        return

    print(f"   거래 {today_summary['trades']}건 | 승률 {today_summary['win_rate']}% | "
          f"순손익 {today_summary['total_pnl']:+,}원")

    # 2) 7일 패턴
    print("📈 7일 패턴 분석 중...")
    weekly = get_weekly_pattern()

    # 3) Claude 복기
    print("🤖 Claude 복기 분석 중...")
    review = claude_review(today_summary, weekly)
    print(f"   평가: {review.get('오늘평가', '-')}")
    print(f"   내일: {review.get('내일전략', '-')}")

    # 4) 저장
    save_review(today_summary, review)

    # ★ score_enter 자동 조정 (nbot)
    try:
        import sys as _sys
        _sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'core'))
        from common_utils import read_state, write_state
        win_rate  = today_summary.get("win_rate", 0)
        st        = read_state("nbot")
        cur_score = st.get("score_enter", 70)
        new_score = cur_score
        if win_rate >= 60:
            new_score = max(60, cur_score - 3)
            reason = f"승률 {win_rate}% 높음 → 기준 -3점"
        elif win_rate < 40:
            new_score = min(90, cur_score + 5)
            reason = f"승률 {win_rate}% 낮음 → 기준 +5점"
        elif win_rate < 50:
            new_score = min(90, cur_score + 3)
            reason = f"승률 {win_rate}% 부진 → 기준 +3점"
        else:
            reason = f"승률 {win_rate}% 유지 → 변경 없음"
        if new_score != cur_score:
            write_state("nbot", {"score_enter": new_score})
            print(f"📊 nbot 매수기준 조정: {cur_score}점 → {new_score}점 ({reason})")
        else:
            print(f"📊 nbot 매수기준 유지: {cur_score}점 ({reason})")
    except Exception as e:
        print(f"⚠️ score_enter 조정 오류: {e}")

    # 5) 디스코드 출력 (kiki가 읽어서 전송)
    discord_msg = format_discord(today_summary, review)
    print(f"\n{'='*50}")
    print("📱 디스코드 전송 내용:")
    print(discord_msg)

    # 디스코드 봇으로 직접 전송
    bot_token  = os.getenv("DISCORD_BOT_TOKEN", "")
    channel_id = os.getenv("DISCORD_CHANNEL_ID", "")
    sent = False
    if bot_token and channel_id:
        try:
            import requests as _req
            url = f"https://discord.com/api/v10/channels/{channel_id}/messages"
            headers = {
                "Authorization": f"Bot {bot_token}",
                "Content-Type": "application/json"
            }
            # 메시지가 길면 분할 전송
            max_len = 1900
            chunks = [discord_msg[i:i+max_len]
                      for i in range(0, len(discord_msg), max_len)]
            for chunk in chunks:
                r = _req.post(url, headers=headers,
                              json={"content": chunk}, timeout=5)
                if r.status_code not in (200, 201):
                    print(f"⚠️ 디스코드 전송 오류: {r.status_code}")
                    break
            else:
                print("✅ 디스코드 전송 완료")
                sent = True
        except Exception as e:
            print(f"⚠️ 디스코드 전송 실패: {e}")

    if not sent:
        # 파일로 저장 (백업)
        review_txt = os.path.join(_here, "daily_review_discord.txt")
        with open(review_txt, "w", encoding="utf-8") as f:
            f.write(discord_msg)
        print(f"✅ 파일 저장: {review_txt}")


if __name__ == "__main__":
    main()
