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

from common_utils import now_kst, today_str

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
TRADE_HIST_DB = "trade_history.db"

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
    for bot_name in ("nbot", "sbot", "cbot"):
        state  = _read_state(bot_name)
        status = state.get("last_status", {})
        if status:
            ctx["bots"][bot_name] = {
                "paused":       state.get("paused", False),
                "positions":    status.get("positions", 0),
                "total_profit": status.get("total_profit", 0),
                "daily_loss":   status.get("daily_loss", 0),
            }
            if bot_name == "nbot":
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
        return res.content[0].text.strip()
    except Exception as e:
        print(f"⚠️ AI 능동 알림 오류: {e}")
        return "OK"


# ============================================================
# 백그라운드 태스크
# ============================================================

async def status_listener():
    """10초마다 손익 변동 감지 — 단타/스윙/코인"""
    last_stock_profit = None
    last_swing_profit = None
    last_coin_profit  = None
    # ★ 헬스체크: 마지막 정상 확인 시각
    last_seen    = {"nbot": None, "sbot": None, "cbot": None}
    alerted      = {"nbot": False, "sbot": False, "cbot": False}
    restarted    = {"nbot": False, "sbot": False, "cbot": False}
    restart_time = {"nbot": 0.0,  "sbot": 0.0,  "cbot": 0.0}
    BOT_LABELS   = {"nbot": "📈 단타봇", "sbot": "📊 스윙봇", "cbot": "🪙 코인봇"}
    BOT_SERVICES = {"nbot": "yeongam9-nbot", "sbot": "yeongam9-sbot", "cbot": "yeongam9-cbot"}

    def _is_market_hours():
        n = now_kst()
        return n.weekday() < 5 and 8 <= n.hour < 16

    async def _auto_restart(bot_name: str, label: str, ch):
        """봇 자동 재시작 — 10분 이상 무응답 시"""
        import asyncio, time as _time
        # 재시작 후 5분 이내 재시작 방지
        if _time.time() - restart_time[bot_name] < 300:
            return
        restart_time[bot_name] = _time.time()
        restarted[bot_name] = True
        await ch.send(f"🔄 {label} 자동 재시작 시도 중...")
        try:
            svc = BOT_SERVICES[bot_name]
            ret = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: __import__('subprocess').run(
                    ['sudo', 'systemctl', 'restart', svc],
                    capture_output=True, timeout=30
                )
            )
            if ret.returncode == 0:
                await ch.send(f"✅ {label} 재시작 완료! 30초 후 상태 확인할게요.")
                await asyncio.sleep(30)
                # 재시작 후 상태 확인
                st2 = _read_state(bot_name)
                if st2.get("last_update"):
                    await ch.send(f"✅ {label} 정상 복구!")
                    alerted[bot_name] = False
                    restarted[bot_name] = False
                else:
                    await ch.send(f"❌ {label} 재시작 후에도 응답 없음! 수동 확인 필요")
            else:
                err = ret.stderr.decode()[:100]
                await ch.send(f"❌ {label} 재시작 실패: {err}")
        except Exception as e:
            await ch.send(f"❌ {label} 재시작 오류: {e}")


    while True:
        await asyncio.sleep(10)
        try:
            ch = _bot.get_channel(_channel_id)
            if not ch:
                continue

            # ── 헬스체크 ────────────────────────────────────
            for bot_name, label in BOT_LABELS.items():
                if bot_name != "cbot" and not _is_market_hours():
                    alerted[bot_name] = False
                    continue
                st = _read_state(bot_name)
                last_upd = st.get("last_update", "")
                if last_upd:
                    try:
                        now_t = now_kst()
                        upd_t = now_t.replace(
                            hour=int(last_upd[:2]),
                            minute=int(last_upd[3:5]),
                            second=int(last_upd[6:8]),
                        )
                        diff_sec = (now_t - upd_t).total_seconds()
                        if diff_sec > 600:  # 10분 이상 → 자동 재시작
                            await _auto_restart(bot_name, label, ch)
                        elif diff_sec > 300:  # 5분 이상 → 알림만
                            if not alerted[bot_name]:
                                alerted[bot_name] = True
                                await ch.send(
                                    "🚨 **" + label + " 응답없음!** "
                                    + "(마지막:" + last_upd + ", "
                                    + str(int(diff_sec//60)) + "분경과) "
                                    + "10분 경과시 자동재시작"
                                )
                        else:
                            alerted[bot_name] = False
                            restarted[bot_name] = False
                    except Exception:
                        pass

            # 단타봇
            state  = _read_state("nbot")
            status = state.get("last_status")
            if status:
                profit = status.get("total_profit", 0)
                if (last_stock_profit is not None
                        and abs(profit - last_stock_profit) > 5000):
                    diff = profit - last_stock_profit
                    await ch.send(
                        f"💹 [단타] 손익 변동: {last_stock_profit:+,}원 → "
                        f"{profit:+,}원 ({diff:+,}원)"
                    )
                last_stock_profit = profit

            # 스윙봇
            sstate  = _read_state("sbot")
            sstatus = sstate.get("last_status")
            if sstatus:
                sprofit = sstatus.get("total_profit", 0)
                if (last_swing_profit is not None
                        and abs(sprofit - last_swing_profit) > 10000):
                    diff = sprofit - last_swing_profit
                    await ch.send(
                        f"💹 [스윙] 손익 변동: {last_swing_profit:+,}원 → "
                        f"{sprofit:+,}원 ({diff:+,}원)"
                    )
                last_swing_profit = sprofit

            # 코인봇
            cstate  = _read_state("cbot")
            cstatus = cstate.get("last_status")
            if cstatus:
                cprofit = cstatus.get("total_profit", 0)
                if (last_coin_profit is not None
                        and abs(cprofit - last_coin_profit) > 3000):
                    diff = cprofit - last_coin_profit
                    await ch.send(
                        f"🪙 [코인] 손익 변동: {last_coin_profit:+,}원 → "
                        f"{cprofit:+,}원 ({diff:+,}원)"
                    )
                last_coin_profit = cprofit

        except Exception:
            pass


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

            # ── 1. 단타봇 연속 손절 ────────────────────────
            nbot = ctx["bots"].get("nbot", {})
            if nbot.get("daily_loss", 0) >= 2:
                key = f"nbot_loss_{today_str()}_{nbot['daily_loss']}"
                if _can_alert(key, ttl_minutes=120):
                    dangers.append({
                        "type": "nbot_consecutive_loss",
                        "data": f"단타봇 당일 손절 {nbot['daily_loss']}회",
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
                key = f"low_winrate_nbot_{today_str()}"
                if _can_alert(key, ttl_minutes=180):
                    watches.append({
                        "type": "low_winrate",
                        "data": f"단타봇 최근 10건 승률 {win_rate_n:.1f}%",
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

            # ── 4. 정오 이후 매수 0건 ──────────────────────
            now_h = now_kst().hour
            if 12 <= now_h <= 14:
                nbot = ctx["bots"].get("nbot", {})
                if nbot.get("positions", 0) == 0 and not nbot.get("paused"):
                    key = f"no_buy_today_{today_str()}"
                    if _can_alert(key, ttl_minutes=240):
                        watches.append({
                            "type": "no_buy",
                            "data": "단타봇 오늘 매수 0건 (점심 이후)",
                        })

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
            nbot_state = _read_state("nbot")
            sectors    = nbot_state.get("active_sectors", [])
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
async def proactive_daily_review():
    """📊 평일 15:35 장 마감 직후 종합 리뷰"""
    last_review_date = None

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

            ctx = _gather_bot_context()

            # 단타봇 오늘 거래 (최근 50건)
            nbot_trades = []
            try:
                conn = _ro_connect(TRADE_HIST_DB)
                rows = conn.execute("""
                    SELECT code, profit_rate, sell_reason, ai_score, sell_time
                    FROM trades
                    WHERE sell_price IS NOT NULL AND sell_time >= ?
                    ORDER BY sell_time DESC LIMIT 50
                """, (today,)).fetchall()
                conn.close()
                nbot_trades = [
                    {
                        "code":   r[0],
                        "rate":   round(r[1] or 0, 2),
                        "reason": r[2],
                        "score":  r[3],
                        "time":   r[4][-8:] if r[4] else "",
                    }
                    for r in rows
                ]
            except Exception:
                pass



            review_data = {
                "date":        today,
                "context":     ctx,
                "nbot_trades": nbot_trades,
                "today_pnl":   ctx["today_realized"],
            }
            msg = await _ai_proactive_message(
                ctx, "review", extra_data=review_data,
            )
            if msg and msg != "OK":
                await ch.send(
                    f"📊 **키키 일일 리뷰** [{now.strftime('%m/%d')}]\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n{msg}"
                )
                print(f"✅ 일일 리뷰 전송 {today}")

        except Exception as e:
            print(f"⚠️ proactive_daily_review: {e}")
