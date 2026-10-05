"""
kiki_monitor.py — 키키 모니터링 / 능동 알림 모듈
================================================================
[이 파일이 하는 일]
  사용자 명령 없이도 키키가 능동적으로 알림을 보냄.

[백그라운드 태스크 4종]
  status_listener()          10초  — 손익 변동 감지 (단타/스윙/코인)
  proactive_danger_watcher() 5분   — 🚨 위험 신호 즉시 알림
  proactive_watch_monitor()  30분  — ⚠️ 주의 신호
  proactive_insight_provider() 1시간 — 💡 패턴/인사이트
  proactive_daily_review()   15:35 — 📊 장마감 일일 리뷰

[안전장치]
  - 중복 알림 30분 내 차단 (_can_alert)
  - 23:00~07:00 조용 시간 (위험 신호만 허용)
  - 시간당 AI 호출 20회 제한 (_can_call_ai)
  - AI가 'OK' 답하면 침묵

[kiki.py 에서 사용법]
  from kiki_monitor import (
      init_monitor,
      status_listener,
      proactive_danger_watcher,
      proactive_watch_monitor,
      proactive_insight_provider,
      proactive_daily_review,
  )
  # on_ready() 에서:
  init_monitor(bot, ai, CHANNEL_ID, read_state, BOT_STATE_FILES,
               get_today_realized_all, get_recent_performance,
               _ro_connect, send_long, DEFAULT_MODEL)
  asyncio.ensure_future(status_listener())
  asyncio.ensure_future(proactive_danger_watcher())
  ...
"""

import os
import json
import asyncio
import sqlite3
import time

from common_utils import now_kst, today_str, extract_claude_text

# ============================================================
# 모듈 전역 — on_ready()에서 init_monitor()로 주입
# ============================================================
_bot               = None
_ai                = None
_channel_id        = 0
_read_state        = None    # callable: (bot_name) → dict
_bot_state_files   = {}
_get_today_realized = None   # callable: () → dict
_get_recent_perf   = None    # callable: (limit) → list
_ro_connect        = None    # callable: (db_file) → Connection
_send_long         = None    # callable: (ch, msg) → None
_default_model     = "claude-haiku-4-5-20251001"

# DB 경로 (kiki.py와 동일)

# 알림 중복 방지 캐시
_alert_cache: dict = {}
# 시간당 AI 호출 카운터
_ai_call_count: dict = {"hour": "", "count": 0}
AI_CALL_LIMIT_PER_HOUR = 20


def init_monitor(
    bot, ai, channel_id, read_state_fn,
    bot_state_files, get_today_realized_fn,
    get_recent_perf_fn, ro_connect_fn,
    send_long_fn, default_model,
):
    """kiki.py on_ready()에서 한 번 호출 — 의존성 주입"""
    global _bot, _ai, _channel_id, _read_state, _bot_state_files
    global _get_today_realized, _get_recent_perf, _ro_connect
    global _send_long, _default_model

    _bot               = bot
    _ai                = ai
    _channel_id        = channel_id
    _read_state        = read_state_fn
    _bot_state_files   = bot_state_files
    _get_today_realized = get_today_realized_fn
    _get_recent_perf   = get_recent_perf_fn
    _ro_connect        = ro_connect_fn
    _send_long         = send_long_fn
    _default_model     = default_model
    print("✅ kiki_monitor 초기화 완료")


# ============================================================
# 내부 헬퍼
# ============================================================
def _is_quiet_hours() -> bool:
    """조용 시간(23:00~07:00) — 위험 신호만 허용"""
    h = now_kst().hour
    return h >= 23 or h < 7


def _can_alert(key: str, ttl_minutes: int = 30) -> bool:
    """같은 key는 ttl 분 내 한 번만 허용 (중복 알림 차단)"""
    now_ts = time.time()
    last   = _alert_cache.get(key, 0)
    if now_ts - last < ttl_minutes * 60:
        return False
    _alert_cache[key] = now_ts
    return True


def _can_call_ai() -> bool:
    """시간당 AI 호출 20회 제한 (비용 보호)"""
    now_h = now_kst().strftime("%Y%m%d%H")
    if _ai_call_count["hour"] != now_h:
        _ai_call_count["hour"]  = now_h
        _ai_call_count["count"] = 0
    if _ai_call_count["count"] >= AI_CALL_LIMIT_PER_HOUR:
        return False
    _ai_call_count["count"] += 1
    return True


def _gather_bot_context() -> dict:
    """모든 봇 상태 종합 → AI 컨텍스트로 전달"""
    ctx = {
        "now":            now_kst().strftime("%H:%M"),
        "today_realized": _get_today_realized(),
        "bots":           {},
    }
    # ★ 2026-10-06: 폐기된 nbot 대신 sbot에 시장상태 정보를 붙임
    for bot_name in ("sbot", "cbot"):
        state  = _read_state(bot_name)
        status = state.get("last_status", {})
        if status:
            ctx["bots"][bot_name] = {
                "paused":       state.get("paused", False),
                "positions":    status.get("positions", 0),
                "total_profit": status.get("total_profit", 0),
                "daily_loss":   status.get("daily_loss", 0),
            }
            if bot_name == "sbot":
                ctx["bots"][bot_name]["market_status"] = status.get("market_status", "normal")
                ctx["bots"][bot_name]["kospi_rate"]    = status.get("market_rate", 0)
                ctx["bots"][bot_name]["score_enter"]   = state.get("score_enter", 55)
            elif bot_name == "cbot":
                ctx["bots"][bot_name]["btc_rate"]      = status.get("btc_rate", 0)
                ctx["bots"][bot_name]["fear_greed"]    = status.get("fear_greed", 50)
                ctx["bots"][bot_name]["market_status"] = status.get("market_status", "normal")
                ctx["bots"][bot_name]["daily_pnl"]     = status.get("daily_pnl", 0)
    return ctx


async def _ai_proactive_message(
    context: dict,
    purpose: str,
    extra_data: dict = None,
) -> str:
    """
    AI에게 능동 알림 메시지 생성 요청.
    purpose: "danger" / "watch" / "insight" / "review"
    반환: 알림 메시지 (또는 'OK' = 알릴 게 없음)
    """
    from kiki_briefing import _claude_call

    if not _can_call_ai():
        return "OK"

    purpose_prompt = {
        "danger":  "위험 신호. 다급하지만 친근한 톤으로, 핵심만 1~2줄.",
        "watch":   "주의 신호. 정보 전달, 평온한 톤, 1~2줄.",
        "insight": "흥미로운 패턴이나 인사이트. 호기심 자극, 2~3줄.",
        "review":  "오늘 매매 회고. 따뜻하고 분석적인 톤, 4~6줄.",
    }.get(purpose, "")

    extra_str = (
        f"\n[추가 데이터]\n{json.dumps(extra_data, ensure_ascii=False, indent=2)}"
        if extra_data else ""
    )

    prompt = f"""너는 키키(꼬리 두 달린 여우정령, 장난스런 여동생). 영암9 자동매매 봇들의 비서야.
주인(사용자)에게 능동적으로 알림을 보내려는 상황이야.

[현재 봇 상황]
{json.dumps(context, ensure_ascii=False, indent=2)}
{extra_str}

[목적]
{purpose_prompt}

[규칙]
- 알릴 만한 게 진짜 없으면 'OK' 한 단어만 답해.
- 알림 보내려면 한국어로, 너의 톤 유지하면서 짧게.
- 숫자/퍼센트는 정확히. 과장 X.
- 매매 권유 X (정보·관찰만).
- 디스코드 메시지 형식. 마크다운 가능. 이모지 1~2개.

답변:"""

    try:
        loop = asyncio.get_event_loop()
        res  = await loop.run_in_executor(
            None,
            lambda: _claude_call(
                _ai.llm,
                model=_default_model,
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}],
            ),
        )
        return extract_claude_text(res)
    except Exception as e:
        print(f"⚠️ AI 능동 알림 오류: {e}")
        return "OK"


# ============================================================
# 백그라운드 태스크
# ============================================================

# ★ 2026-10-06: 여기 있던 status_listener(헬스체크+자동재시작 포함)는 kiki.py의
#   같은 이름 함수에 가려져 한 번도 실행된 적이 없어 제거(봇별 하트비트
#   워치독이 재시작을 담당). 손익변동 알림은 kiki.py의 status_listener가 담당.


# ─────────────────────────────────────────────────────────────
# 1️⃣ 위험 신호 (5분 간격) — 즉시 알림
# ─────────────────────────────────────────────────────────────
async def proactive_danger_watcher():
    """🚨 위험 신호 즉시 알림 — 연속손절/BTC급락/일일손실한도"""
    while True:
        await asyncio.sleep(300)  # 5분
        try:
            ch = _bot.get_channel(_channel_id)
            if not ch:
                continue

            ctx     = _gather_bot_context()
            dangers = []

            # ── 1. 스윙봇 연속 손절 ────────────────────────
            sbot = ctx["bots"].get("sbot", {})
            if sbot.get("daily_loss", 0) >= 2:
                key = f"sbot_loss_{today_str()}_{sbot['daily_loss']}"
                if _can_alert(key, ttl_minutes=120):
                    dangers.append({
                        "type": "sbot_consecutive_loss",
                        "data": f"스윙봇 당일 손절 {sbot['daily_loss']}회",
                    })

            # ── 2. 코인봇 BTC 급락 ─────────────────────────
            cbot     = ctx["bots"].get("cbot", {})
            btc_rate = cbot.get("btc_rate", 0)
            if btc_rate <= -3.5:
                key = f"btc_crash_{today_str()}_{int(btc_rate)}"
                if _can_alert(key, ttl_minutes=60):
                    dangers.append({
                        "type": "btc_crash",
                        "data": f"BTC {btc_rate:+.2f}% 급락",
                    })

            # ── 3. 일일 손실 합계 ──────────────────────────
            today_total = sum(ctx["today_realized"].values())
            if today_total <= -100_000:
                key = f"big_loss_{today_str()}"
                if _can_alert(key, ttl_minutes=60):
                    dangers.append({
                        "type": "big_daily_loss",
                        "data": f"오늘 합계 손실 {today_total:+,}원",
                    })

            # ── 4. 봇 자동 일시중단 ────────────────────────
            for bot_name, b in ctx["bots"].items():
                if b.get("paused"):
                    state = _read_state(bot_name)
                    if state.get("last_status", {}).get("daily_loss", 0) >= 2:
                        key = f"auto_pause_{bot_name}_{today_str()}"
                        if _can_alert(key, ttl_minutes=240):
                            dangers.append({
                                "type": "auto_pause",
                                "data": f"{bot_name} 자동 일시중단 (손절한도)",
                            })

            if dangers:
                # 조용 시간이라도 위험 신호는 전송
                msg = await _ai_proactive_message(
                    ctx, "danger", extra_data={"dangers": dangers},
                )
                if msg and msg != "OK":
                    await ch.send(f"🚨 **키키 긴급 알림**\n{msg}")

        except Exception as e:
            print(f"⚠️ proactive_danger_watcher: {e}")


# ─────────────────────────────────────────────────────────────
# 2️⃣ 주의 신호 (30분 간격)
# ─────────────────────────────────────────────────────────────
async def proactive_watch_monitor():
    """⚠️ 주의 신호 — 승률 저하 / 매수 없음 / 시장 변화 / 공포탐욕"""
    last_market_status = {}

    while True:
        await asyncio.sleep(1800)  # 30분
        try:
            if _is_quiet_hours():
                continue

            ch = _bot.get_channel(_channel_id)
            if not ch:
                continue

            ctx     = _gather_bot_context()
            watches = []

            # ── 1. 시장 상태 변화 ──────────────────────────
            for bot_name, b in ctx["bots"].items():
                if "market_status" in b:
                    cur  = b["market_status"]
                    prev = last_market_status.get(bot_name, "normal")
                    if cur != prev and cur != "normal":
                        watches.append({
                            "type": "market_change",
                            "data": f"{bot_name} 시장 {prev}→{cur}",
                        })
                    last_market_status[bot_name] = cur

            # ── 2. 단타봇 최근 승률 저하 ───────────────────
            perf_n = _get_recent_perf(limit=10)
            # ★ 버그수정: get_recent_performance는 dict 반환
            if perf_n and isinstance(perf_n, dict):
                win_rate_n = perf_n.get("win_rate", 100)
            elif perf_n and isinstance(perf_n, list):
                profits = [r[0] for r in perf_n if r[0] is not None]
                win_rate_n = len([p for p in profits if p >= 0]) / len(profits) * 100 if profits else 100
            else:
                win_rate_n = 100
            if win_rate_n < 35:
                key = f"low_winrate_sbot_{today_str()}"
                if _can_alert(key, ttl_minutes=180):
                    watches.append({
                        "type": "low_winrate",
                        # ★ 2026-10-06: _get_recent_perf는 sbot DB를 읽음(nbot 폐기 잔재 라벨)
                        "data": f"스윙봇(sbot) 최근 10건 승률 {win_rate_n:.1f}%",
                    })

            # ── 3. 코인봇 극단 공포 ─────────────────────────
            cbot = ctx["bots"].get("cbot", {})
            fg   = cbot.get("fear_greed", 50)
            if fg < 30:
                key = f"fear_low_{today_str()}_{fg // 5}"
                if _can_alert(key, ttl_minutes=120):
                    watches.append({
                        "type": "extreme_fear",
                        "data": f"공포탐욕 {fg} (극단공포)",
                    })

            # ★ 2026-10-06: "4. 정오 이후 매수 0건"(폐기된 nbot 기준) 제거

            if watches:
                msg = await _ai_proactive_message(
                    ctx, "watch", extra_data={"watches": watches},
                )
                if msg and msg != "OK":
                    await ch.send(f"🦊 키키: {msg}")

        except Exception as e:
            print(f"⚠️ proactive_watch_monitor: {e}")


# ─────────────────────────────────────────────────────────────
# 3️⃣ 인사이트 (1시간 간격)
# ─────────────────────────────────────────────────────────────
async def proactive_insight_provider():
    """💡 패턴 / 기회 / 흥미로운 변화 감지"""
    last_total_profit = {}

    while True:
        await asyncio.sleep(3600)  # 1시간
        try:
            if _is_quiet_hours():
                continue

            # 장 시간 외 스킵 (코인은 24h이지만 인사이트는 장중만)
            now_h = now_kst().hour
            now_w = now_kst().weekday()
            if not ((now_w < 5) and (9 <= now_h <= 15)):
                continue

            ch = _bot.get_channel(_channel_id)
            if not ch:
                continue

            ctx      = _gather_bot_context()
            insights = []

            # ── 1. 봇별 1시간 손익 변화 ───────────────────
            for bot_name, b in ctx["bots"].items():
                cur  = b.get("total_profit", 0)
                prev = last_total_profit.get(bot_name)
                if prev is not None:
                    change = cur - prev
                    if abs(change) >= 30_000:
                        insights.append({
                            "type": "hourly_change",
                            "data": f"{bot_name} 1시간 변동 {change:+,}원",
                        })
                last_total_profit[bot_name] = cur

            # ── 2. 강세 업종 변화 ──────────────────────────
            # ★ 2026-10-06: nbot 상태파일(폐기) 대신 sbot 상태 기준
            sectors    = _read_state("sbot").get("active_sectors", [])
            if sectors:
                key = f"sectors_{','.join(sectors)}_{today_str()}"
                if _can_alert(key, ttl_minutes=180):
                    insights.append({
                        "type": "active_sectors",
                        "data": f"활성 업종: {', '.join(sectors[:3])}",
                    })

            # ── 3. 공포탐욕 탐욕 구간 ─────────────────────
            cbot = ctx["bots"].get("cbot", {})
            fg   = cbot.get("fear_greed", 50)
            if fg >= 70:
                key = f"fg_high_{today_str()}_{fg // 5}"
                if _can_alert(key, ttl_minutes=240):
                    insights.append({
                        "type": "fear_greed_high",
                        "data": f"공포탐욕 {fg} (탐욕 구간)",
                    })

            if insights:
                msg = await _ai_proactive_message(
                    ctx, "insight", extra_data={"insights": insights},
                )
                if msg and msg != "OK":
                    await ch.send(f"💡 **키키 인사이트**\n{msg}")

        except Exception as e:
            print(f"⚠️ proactive_insight_provider: {e}")


# ─────────────────────────────────────────────────────────────
# 4️⃣ 일일 리뷰 (15:35 — 장 마감 후)
# ─────────────────────────────────────────────────────────────
# ★ 2026-07-02: 기존엔 nbot 기준 서술형 AI 복기(오늘평가/잘한점/못한점/
#   내일전략 등)였음 — nbot이 폐기되면서 더 이상 필요 없어짐. sbot/sbo2/
#   cbot 실전봇 기준으로 "보유종목 등락률 + 금일 실현손익"만 보여주는
#   단순 요약으로 교체 (별도 interface/daily_review.py 20:10 cron은
#   비활성화 — 이 함수가 대체).
async def proactive_daily_review():
    """📊 평일 15:35 장 마감 직후 — sbot/sbo2/cbot 보유종목 + 금일 실현손익"""
    last_review_date = None
    from master_db import get_all_positions, get_today_summary

    while True:
        await asyncio.sleep(60)  # 1분 간격 체크
        try:
            now      = now_kst()
            today    = now.strftime("%Y-%m-%d")
            now_hhmm = now.strftime("%H%M")

            # 평일 15:35~15:40 사이 1회
            if not (now.weekday() < 5
                    and "1535" <= now_hhmm <= "1540"
                    and last_review_date != today):
                continue

            last_review_date = today
            ch = _bot.get_channel(_channel_id)
            if not ch:
                continue

            positions   = get_all_positions()
            pnl_summary = get_today_summary()

            lines = [f"📊 **키키 일일 리뷰** [{now.strftime('%m/%d')}]",
                     "━━━━━━━━━━━━━━━━━━━━"]
            for bot_type in ["sbot", "sbo2", "cbot"]:
                bot_pos = [p for p in positions if p["bot_type"] == bot_type]
                if bot_pos:
                    pos_str = ", ".join(
                        f"{p.get('stock_name') or p['code']}"
                        f"({(p.get('profit_rate') or 0):+.1f}%)"
                        for p in bot_pos
                    )
                else:
                    pos_str = "없음"
                pnl = pnl_summary.get(bot_type, {}).get("pnl", 0)
                lines.append(f"**{bot_type}** : 보유종목({pos_str}) | 금일손익: {pnl:+,}원")

            await ch.send("\n".join(lines))
            print(f"✅ 일일 리뷰 전송 {today}")

        except Exception as e:
            print(f"⚠️ proactive_daily_review: {e}")
