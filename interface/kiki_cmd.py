"""
kiki_cmd.py — KiKi 명령 처리 모듈
================================================================
디스코드 명령어 처리 함수들
"""
import os, sys, asyncio, datetime, requests, json, time, re

_here = os.path.dirname(os.path.abspath(__file__))
_base = os.path.dirname(_here)
for _d in ["core", "intelligence", "interface", "bots", ""]:
    _p = os.path.join(_base, _d)
    if _p not in sys.path:
        sys.path.insert(0, _p)

for _ep in [os.path.join(_here, ".env"), os.path.join(_base, ".env")]:
    if os.path.exists(_ep):
        from dotenv import load_dotenv
        load_dotenv(_ep, override=True)
        break

import discord
from common_utils import now_kst, today_str, now_hms, now_hhmm, fmt_won, safe_float, safe_int
# ★ read_state/write_state/update_state — 봇 이름 기반 버전 사용
from kiki_briefing import read_state, write_state, update_state

# send_long, wait_cmd_result는 kiki.py에서 주입
send_long = None
execute_command = None

async def wait_cmd_result(bot_name: str, max_attempts: int = 12,
                           interval: float = 5.0) -> str:
    """pending_cmd 처리 결과 폴링 — 자체 구현"""
    import asyncio as _asyncio
    for _ in range(max_attempts):
        await _asyncio.sleep(interval)
        st = read_state(bot_name)
        result = st.get("cmd_result")
        if result:
            update_state(bot_name, cmd_result=None)
            return result
    return "⏱️ 응답 시간 초과"
from kiki_data import (
    get_recent_performance, get_open_positions_from_db,
    get_coin_performance,
    get_today_realized_all, _ro_connect,
    get_active_bots,
)

ai  = None
bot = None
BOT_STATE_FILES = {}
CHANNEL_ID = 0

# DB 경로 상수
_base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRADE_HIST_DB = os.path.join(_base, "trade_history.db")
SBOT_HIST_DB  = os.path.join(_base, "sbot_trade_history.db")
CBOT_HIST_DB  = os.path.join(_base, "cbot_trade_history.db")
BOT_STATE_DIR = _base

async def cmd_status(ctx, bot_name: str = "sbot2"):
    state     = read_state(bot_name)
    status    = state.get("last_status", {})
    pos_rows  = get_open_positions_from_db(bot_name)
    now       = now_kst().strftime("%H:%M:%S")
    paused    = "⏸️ 일시중단" if state.get("paused") else "▶️ 실행중"
    bot_label = "📈 단타봇" if bot_name == "sbot2" else "📊 스윙봇" if bot_name == "sbot" else "🤖 봇"

    lines = [
        f"{bot_label} **영암9 현황** [{now}]",
        f"상태: {paused}",
        f"매수기준: {state.get('score_enter', 55)}점",
    ]
    if status:
        lines += [
            f"💵 예수금: {status.get('cash', 0):,}원",
            f"💰 주문가능: {status.get('psbl_cash', 0):,}원",
            f"📈 총손익: {status.get('total_profit', 0):+,}원",
            f"📊 시장: {status.get('market_status', 'normal')} | "
            f"코스피: {status.get('market_rate', 0):+.2f}%",
        ]
        # 중단기봇 손절카운터 표시
        if bot_name == "sbot2":
            daily_loss = status.get("daily_loss", 0)
            if daily_loss > 0:
                lines.append(f"🛑 당일 손절: {daily_loss}회")

        active = status.get("active_sectors", state.get("active_sectors", []))
        if active:
            lines.append(f"🏭 활성 업종: {' | '.join(active)}")

    pos_detail = status.get("positions_detail", {})
    if pos_detail:
        lines.append("\n**📦 보유종목**")
        for code, info in pos_detail.items():
            emoji = "📈" if info.get("rate", 0) >= 0 else "📉"
            tag   = "🎯" if info.get("buy_tag") == "theme_buy" else "  "
            lines.append(
                f"  {tag}{emoji} {code}({info.get('name', code)}) | "
                f"현재:{info.get('current', 0):,}원 | "
                f"{info.get('rate', 0):+.2f}% | "
                f"{info.get('qty', 0)}주"
            )
    elif pos_rows:
        lines.append("\n**📦 보유종목 (DB 기준)**")
        for code, bp, qty, ais, bt in pos_rows:
            lines.append(f"  {code} | 매수가:{int(bp):,}원 | {qty}주 | AI:{ais}점")
    else:
        lines.append("보유종목 없음")

    await send_long(ctx, "\n".join(lines))


async def cmd_score(ctx, score: int):
    if not 0 <= score <= 100:
        await ctx.send("❌ 점수는 0~100 사이여야 해요")
        return
    update_state("sbot2", score_enter=score)
    await ctx.send(f"✅ 매수 기준 점수 변경: **{score}점**\n(다음 루프부터 적용)")


async def cmd_sell(ctx, code: str, bot_name: str = "sbot2"):
    """단타/스윙 매도 명령. 종목명으로 검색 가능."""
    if not code.isdigit():
        # 종목명 → 코드 변환
        state         = read_state(bot_name)
        code_name_map = state.get("last_status", {}).get("code_name_map", {})
        found = next((c for c, name in code_name_map.items()
                      if code in name or name in code), None)
        if found:
            await ctx.send(f"🔍 종목명 '{code}' → 코드 **{found}** 로 변환")
            code = found
        else:
            db = SBOT_HIST_DB
            try:
                conn = _ro_connect(db)
                row  = conn.execute(
                    "SELECT code FROM trades WHERE sell_price IS NULL "
                    "AND stock_name LIKE ? ORDER BY id DESC LIMIT 1",
                    (f"%{code}%",),
                ).fetchone()
                conn.close()
                if row:
                    await ctx.send(f"🔍 종목명 '{code}' → 코드 **{row[0]}** 로 변환")
                    code = row[0]
                else:
                    await ctx.send(f"❌ '{code}' 종목을 찾을 수 없어요")
                    return
            except Exception as e:
                await ctx.send(f"❌ 종목 검색 오류: {e}")
                return

    # ★ 실계좌 보유 확인 (state → KIS API 순서로)
    state      = read_state(bot_name)
    pos_detail = state.get("last_status", {}).get("positions_detail", {})
    
    if code not in pos_detail:
        # state에 없으면 KIS API 실계좌 직접 조회
        try:
            import sys, os
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'core'))
            from kis_api import KisAPI
            from dotenv import load_dotenv
            load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
            
            if bot_name == "sbot":
                api = KisAPI(
                    appkey=os.getenv("KIS_APPKEY2"),
                    secret=os.getenv("KIS_SECRET2"),
                    cano  =os.getenv("KIS_CANO2"),
                    acnt  =os.getenv("KIS_ACNT_PRDT_CD2"),
                )
            else:
                api = KisAPI()
            
            real_pos = api.get_current_positions()
            if code not in real_pos:
                await ctx.send(f"❌ {code} 실계좌에도 보유 중이 아님")
                return
            await ctx.send(f"✅ 실계좌 확인: {code} {real_pos[code].get('qty',0)}주 보유")
        except Exception as e:
            await ctx.send(f"⚠️ 실계좌 조회 실패: {e}\n봇 루프에서 처리 시도합니다")

    update_state(bot_name, pending_cmd={"type": "sell", "code": code}, cmd_result=None)
    await ctx.send(f"📤 매도 명령 전달: **{code}**\n(다음 루프에서 실행)")

    result = await wait_cmd_result(bot_name)
    if result:
        await ctx.send(f"✅ 결과: {result}")
    else:
        await ctx.send("⚠️ 응답 없음 — 봇 실행 중인지 확인하세요")


async def cmd_buy(ctx, code: str, qty: int):
    # ★ 종목명 → 코드 변환
    if not code.isdigit():
        state         = read_state("sbot2")
        code_name_map = state.get("last_status", {}).get("code_name_map", {})
        found = next((c for c, nm in code_name_map.items()
                      if code in nm or nm in code), None)
        if found:
            await ctx.send(f"🔍 종목명 '{code}' → 코드 **{found}** 로 변환")
            code = found
        else:
            await ctx.send(f"❌ '{code}' 종목코드를 찾을 수 없어요. 예: !매수 005930 10")
            return
    if qty <= 0:
        await ctx.send("수량은 1 이상이어야 해요")
        return

    state = read_state("sbot2")
    name  = state.get("last_status", {}).get("code_name_map", {}).get(code, code)
    update_state("sbot2", pending_cmd={"type": "buy", "code": code, "qty": qty})
    await ctx.send(f"📤 매수 명령 전달: **{code}({name})** {qty}주\n(다음 루프에서 실행)")

    result = await wait_cmd_result("sbot2")
    if result:
        await ctx.send(f"✅ 결과: {result}")
    else:
        await ctx.send("⚠️ 응답 없음 — nbot.py 실행 중인지 확인하세요")


async def cmd_analyze(ctx, code: str):
    await ctx.send(f"🔍 {code} 분석 중...")
    try:
        conn = _ro_connect(AI_CACHE_DB)
        row  = conn.execute(
            "SELECT score, reason, analyzed_at FROM ai_analysis WHERE code = ?",
            (code,),
        ).fetchone()
        conn.close()
        if row:
            score, reason, at = row
            await ctx.send(
                f"🧠 **{code} AI 분석 결과**\n"
                f"점수: {score}점\n"
                f"이유: {reason}\n"
                f"분석시각: {at}"
            )
        else:
            await ctx.send(f"ℹ️ {code} 분석 기록 없음")
    except Exception as e:
        await ctx.send(f"❌ 조회 오류: {e}")


async def cmd_pause(ctx, pause: bool, bot_name: str = "sbot2"):
    labels = {"sbot2": "단타봇", "sbot": "스윙봇", "cbot": "코인봇"}
    label  = labels.get(bot_name, bot_name)
    if pause:
        update_state(bot_name, paused=True)
        await ctx.send(f"⏸️ **{label} 일시 중단**\n보유 포지션 매도 체크는 계속됩니다")
    else:
        # ★ loss_date도 함께 갱신해 손절카운터 정상 초기화
        update_state(bot_name, paused=False, daily_loss=0,
                    loss_date=today_str())
        # 시장 상태 확인
        sbot2_st = read_state("sbot2")
        mkt_status = sbot2_st.get("last_status", {}).get("market_status", "normal")
        mkt_rate   = sbot2_st.get("last_status", {}).get("market_rate", 0)
        if mkt_status == "stop":
            await ctx.send(
                f"▶️ **{label} 재개** | 손절카운터 초기화 완료\n"
                f"⚠️ 현재 시장 **STOP 모드** (코스피 {mkt_rate:+.2f}%)\n"
                f"📌 신규 매수는 차단됩니다. 코스피 -3% 이상 회복 시 자동 재개."
            )
        elif mkt_status == "weak":
            await ctx.send(
                f"▶️ **{label} 재개** | 손절카운터 초기화 완료\n"
                f"⚠️ 현재 시장 **약세장** (코스피 {mkt_rate:+.2f}%)\n"
                f"📌 보수적 매매 진행 중."
            )
        else:
            await ctx.send(f"▶️ **{label} 재개**\n손절카운터 초기화 완료")


# ============================================================
# 핸들러 — 성과 (★ 모든 봇 합산 표시)
# ============================================================
async def cmd_performance(ctx):
    """오늘 매매 성과 (단타/스윙/종가/코인 모두 합산)"""
    from common_utils import today_str, now_hms, fmt_won
    today  = today_str()
    realized_all = get_today_realized_all()

    sbot2_p = realized_all.get("sbot2", 0)
    sbot_p = realized_all.get("sbot", 0)
    cbot_p = realized_all.get("cbot", 0)
    total  = sbot2_p + sbot_p + cbot_p

    # ★ KDA 스타일 대시보드
    # 최근 매매 이력
    recent = get_recent_performance(limit=20)
    wins   = [r for r in recent if r[0] is not None and r[0] >= 0]
    losses = [r for r in recent if r[0] is not None and r[0] < 0]
    win_rate = len(wins) / len(recent) * 100 if recent else 0
    avg_win  = sum(r[0] for r in wins)   / len(wins)   if wins   else 0
    avg_loss = sum(r[0] for r in losses) / len(losses) if losses else 0

    # 최근 폼 (최근 5건)
    recent5 = recent[:5]
    form_str = ""
    for r in recent5:
        form_str += "✅" if r[0] >= 0 else "❌"

    # 경고 레벨
    if win_rate >= 55:
        warn = "🔥 HOT STREAK"
    elif win_rate >= 45:
        warn = "✅ STABLE"
    else:
        warn = "⚠️ WARNING: Low Win Rate"

    # 합계
    total_emoji = "✅" if total >= 0 else "❌"

    msg  = f"🎮 **[영암9] Trader Performance** [{today}]\n"
    msg += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"⚔️ **Win Rate** : {win_rate:.1f}% (승:{len(wins)} / 패:{len(losses)})\n"
    msg += f"📊 **Recent Form** : {form_str} [{warn}]\n"
    msg += f"💹 **Avg Win** : {avg_win:+.2f}% | **Avg Loss** : {avg_loss:+.2f}%\n"
    msg += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    msg += "💰 **TODAY P&L**\n"
    if sbot2_p: msg += f"  📈 중단기봇: **{sbot2_p:+,}원**\n"
    if sbot_p: msg += f"  📊 스윙봇: **{sbot_p:+,}원**\n"
    if cbot_p: msg += f"  🪙 코인봇: **{cbot_p:+,}원**\n"
    if not (nbot_p or sbot_p or cbot_p):
        msg += "  오늘 실현 매매 없음\n"
    msg += "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"{total_emoji} **합계: {total:+,}원**\n"
    msg += f"🎯 전략: 포지션 3개 | 임계치 70점 | 시장 동적 대응"
    await ctx.send(msg)


# ============================================================
# 핸들러 — 관심종목
# ============================================================
async def cmd_performance_detail(ctx, days: int = 30):
    """!성과상세 — 샤프/MDD/종목별/시간대별/기간비교 상세 분석"""
    await ctx.send(f"📊 **성과 상세 분석 중...** (최근 {days}일)")
    try:
        loop   = asyncio.get_event_loop()
        mpa    = MultiPerformanceAnalyzer()
        result = await loop.run_in_executor(None, mpa.summary, days)
        await send_long(ctx, result)

        # 단타봇 상세 리포트 추가
        import os
        if os.path.exists(TRADE_HIST_DB):
            pa     = PerformanceAnalyzer(TRADE_HIST_DB)
            report = await loop.run_in_executor(None, pa.full_report, days)
            detail = pa.format_discord(report)
            await send_long(ctx, detail)
    except Exception as e:
        await ctx.send(f"❌ 성과 분석 오류: {e}")


async def cmd_analyze_today(ctx):
    """오늘 매매 AI 분석 — 패턴/원인 파악"""
    await ctx.send("🔍 오늘 매매 분석 중...")
    try:
        import sqlite3
        conn  = sqlite3.connect(TRADE_HIST_DB, timeout=5)
        today = now_kst().strftime("%Y-%m-%d")
        rows  = conn.execute("""
            SELECT code, buy_price, sell_price, profit_rate,
                   sell_reason, buy_time, sell_time, ai_score
            FROM trades
            WHERE sell_price IS NOT NULL AND sell_time >= ?
            ORDER BY sell_time
        """, (today,)).fetchall()
        conn.close()

        if not rows:
            await ctx.send("🦊 키키: 오늘 완료된 매매가 없어요!")
            return

        # AI에게 분석 요청
        trades_str = "\n".join([
            f"  {r[0]}: {r[3]:+.1f}% ({r[4]}) AI:{r[7]}점"
            for r in rows
        ])
        wins   = [r for r in rows if r[3] >= 0]
        losses = [r for r in rows if r[3] < 0]

        prompt = f"""오늘({today}) 단타봇 매매 결과:
{trades_str}

총 {len(rows)}건 | 익절:{len(wins)} | 손절:{len(losses)}

이 매매 패턴을 분석해서 주인(영암9)에게 알려줘:
1. 잘된 점 (1줄)
2. 아쉬운 점 (1줄)
3. 내일 개선할 것 (1줄)
→ 키키 톤으로, 따뜻하게, 총 4줄 이내"""

        loop = asyncio.get_event_loop()
        def _call():
            res = _claude_call(ai.llm, 
                model=DEFAULT_MODEL, max_tokens=300,
                messages=[{"role": "user", "content": prompt}]
            )
            return res.content[0].text.strip()

        analysis = await loop.run_in_executor(None, _call)
        summary  = (f"📊 **오늘 매매 분석** [{today}]\n"
                   f"총 {len(rows)}건 | ✅익절:{len(wins)} | ❌손절:{len(losses)}\n"
                   f"━━━━━━━━━━━━━━━━━━━━\n{analysis}")
        await send_long(ctx, summary)

    except Exception as e:
        await ctx.send(f"❌ 분석 오류: {e}")


async def cmd_analyze_period(ctx, days: int = 7):
    """기간별 성과 AI 분석"""
    await ctx.send(f"🔍 최근 {days}일 분석 중...")
    try:
        from performance import PerformanceAnalyzer
        import os
        pa     = PerformanceAnalyzer(TRADE_HIST_DB)
        report = pa.full_report(days=days)
        b      = report.get("basic", {})
        r      = report.get("risk", {})
        by_h   = report.get("by_hour", {})

        if not b or b.get("total", 0) == 0:
            await ctx.send(f"🦊 키키: 최근 {days}일 매매 이력이 없어요!")
            return

        # 시간대별 최고/최저
        if by_h:
            best_hour  = max(by_h.items(), key=lambda x: x[1]["avg_profit"])
            worst_hour = min(by_h.items(), key=lambda x: x[1]["avg_profit"])
            hour_insight = (f"\n  최고 시간대: {best_hour[0]} "
                           f"(평균{best_hour[1]['avg_profit']:+.1f}%) | "
                           f"최저: {worst_hour[0]} "
                           f"(평균{worst_hour[1]['avg_profit']:+.1f}%)")
        else:
            hour_insight = ""

        prompt = f"""최근 {days}일 단타봇 성과:
승률:{b.get('win_rate',0)}% | 평균:{b.get('avg_profit',0):+.2f}% | PF:{b.get('profit_factor',0):.2f}
MDD:-{r.get('mdd',0):.1f}% | 샤프:{r.get('sharpe',0):.2f}{hour_insight}

이 성과를 보고 주인에게 핵심 인사이트 3가지만 말해줘.
키키 톤으로, 친근하게, 5줄 이내."""

        loop = asyncio.get_event_loop()
        def _call():
            res = _claude_call(ai.llm, 
                model=DEFAULT_MODEL, max_tokens=300,
                messages=[{"role": "user", "content": prompt}]
            )
            return res.content[0].text.strip()

        analysis = await loop.run_in_executor(None, _call)
        result   = pa.format_discord(report)
        await send_long(ctx, result)
        await ctx.send(f"\n🦊 **키키 인사이트**\n{analysis}")

    except Exception as e:
        await ctx.send(f"❌ 분석 오류: {e}")


async def cmd_watchlist(ctx, code: str, bot_name: str = "sbot2"):
    state     = read_state(bot_name)
    watchlist = state.get("watchlist", [])
    wl_expire = state.get("watchlist_expire", {})
    name      = state.get("last_status", {}).get("code_name_map", {}).get(code, code)

    if code in watchlist:
        watchlist.remove(code)
        wl_expire.pop(code, None)
        update_state(bot_name, watchlist=watchlist, watchlist_expire=wl_expire)
        await ctx.send(
            f"👀 관심종목 제거: **{code}({name})**\n"
            f"현재: {', '.join(watchlist) or '없음'}"
        )
    else:
        expire_date = (now_kst() + datetime.timedelta(days=7)).strftime("%Y-%m-%d")
        watchlist.append(code)
        wl_expire[code] = expire_date
        update_state(bot_name, watchlist=watchlist, watchlist_expire=wl_expire)
        await ctx.send(f"👀 관심종목 추가: **{code}({name})**\n만료: {expire_date}")


async def cmd_watchlist_show(ctx, bot_name: str = "sbot2"):
    state     = read_state(bot_name)
    watchlist = state.get("watchlist", [])
    wl_expire = state.get("watchlist_expire", {})
    wl_source = state.get("watchlist_source", {})
    name_map  = state.get("last_status", {}).get("code_name_map", {})
    name_map.update(state.get("hts_watchlist", {}))
    bot_label = "단타봇" if bot_name == "sbot2" else "스윙봇"

    today   = today_str()
    expired = [c for c in watchlist
               if wl_source.get(c, "manual") == "manual"
               and wl_expire.get(c, "9999-12-31") < today]
    if expired:
        for c in expired:
            watchlist.remove(c)
            wl_expire.pop(c, None)
        update_state(bot_name, watchlist=watchlist, watchlist_expire=wl_expire)

    if watchlist:
        items = []
        for c in watchlist:
            src = wl_source.get(c, "manual")
            tag = ("🏭" if "sector" in src else
                   "🎯" if "theme"  in src else
                   "🆕" if "new"    in src else "✋")
            items.append(f"  {tag} {c}({name_map.get(c, c)}) ~{wl_expire.get(c, '?')}")
        await ctx.send(f"👀 **관심종목 목록** ({bot_label})\n" + "\n".join(items))
    else:
        await ctx.send("👀 관심종목 없음")


async def cmd_all_status(ctx):
    active = get_active_bots()
    if not active:
        await ctx.send("⚠️ 실행 중인 봇 없음")
        return
    for bot_name, _ in active:
        if bot_name == "cbot":
            await cmd_cbot_status(ctx)
        else:
            await cmd_status(ctx, bot_name)


async def cmd_restart_all(ctx):
    """전체 봇 재시작 (kiki 제외) — nbot/sbot/cbot/telegram/sector"""
    import subprocess as _sp
    import asyncio as _ac

    SERVICES = [
        ("sbot2",     "yeongam9-nbot"),
        ("sbot",     "yeongam9-sbot"),
        ("cbot",     "yeongam9-cbot"),
        ("telegram", "yeongam9-telegram"),
        ("sector",   "yeongam9-sector"),
    ]

    await ctx.send("🔄 전체 재시작 시작... (kiki 제외)")
    results = []

    for name, svc in SERVICES:
        try:
            ret = _sp.run(["sudo", "systemctl", "restart", svc],
                          capture_output=True, timeout=15)
            if ret.returncode == 0:
                results.append(f"✅ {name}")
            else:
                err = ret.stderr.decode('utf-8', errors='ignore').strip()[:50]
                results.append(f"❌ {name}: {err or 'returncode=' + str(ret.returncode)}")
        except Exception as e:
            results.append(f"❌ {name}: {e}")
        await _ac.sleep(2)  # 2초 간격으로 안정적 재시작

    await ctx.send("\n".join(results) + "\n\n🎯 전체 재시작 완료!")

    # ★ 재시작 후 5초 대기 후 상태 자동 출력
    await _ac.sleep(5)
    await cmd_all_status(ctx)


# ============================================================
# ============================================================
# 핸들러 — 업종/테마
# ============================================================
async def cmd_theme_status(ctx):
    state          = read_state("sbot2")
    active_sectors = state.get("active_sectors", [])
    sector_updated = state.get("sector_updated_at", "")
    name_map       = state.get("last_status", {}).get("code_name_map", {})
    name_map.update(state.get("hts_watchlist", {}))
    watchlist = state.get("watchlist", [])
    wl_source = state.get("watchlist_source", {})

    lines = [f"🏭 **당일 강세 업종/테마** [{now_kst().strftime('%H:%M')}]"]

    if active_sectors:
        lines.append(f"✅ 활성 업종: **{' | '.join(active_sectors)}**")
        sector_codes = [c for c in watchlist if wl_source.get(c) == "hts_sector"]
        if sector_codes:
            names = ", ".join(f"{c}({name_map.get(c, c)})" for c in sector_codes[:8])
            lines.append(f"  📌 관련 종목: {names}")
    else:
        lines.append("❌ 현재 활성 업종 없음")

    if sector_updated:
        lines.append(f"\n⏰ 마지막 업종 체크: {sector_updated}")
        lines.append("💡 nbot이 매시 20분 자동 체크합니다")

    theme_codes = [c for c in watchlist if wl_source.get(c) == "hts_theme"]
    new_codes   = [c for c in watchlist if wl_source.get(c) == "hts_new"]

    if theme_codes:
        names = ", ".join(f"{c}({name_map.get(c, c)})" for c in theme_codes[:6])
        extra = f" 외 {len(theme_codes) - 6}개" if len(theme_codes) > 6 else ""
        lines.append(f"\n🎯 테마 종목 ({len(theme_codes)}개): {names}{extra} (+5점)")
    if new_codes:
        names = ", ".join(f"{c}({name_map.get(c, c)})" for c in new_codes[:5])
        lines.append(f"🆕 신규추천 ({len(new_codes)}개): {names} (+7점)")

    await send_long(ctx, "\n".join(lines))


# ============================================================
# 키움 HTS 관심그룹 동기화 (검증된 로직 그대로)
# ============================================================
def _get_kiwoom_token_sync() -> str:
    """키움 토큰 동기 발급"""
    appkey = os.getenv("KIWOOM_APPKEY", "")
    secret = os.getenv("KIWOOM_SECRETKEY", "")
    if not appkey or not secret:
        return ""
    try:
        res = requests.post(
            "https://api.kiwoom.com/oauth2/token",
            json={"grant_type": "client_credentials",
                  "appkey": appkey, "secretkey": secret},
            timeout=10,
        ).json()
        token = res.get("token", "")
        print("✅ 키움 토큰 발급 완료 (관심그룹용)")
        return token
    except Exception as e:
        print(f"⚠️ 키움 토큰 발급 실패: {e}")
        return ""


async def _fetch_kiwoom_watchlist_ws() -> list:
    """키움 WebSocket으로 관심그룹 전체 종목 조회"""
    try:
        import websockets as _ws
    except ImportError:
        print("⚠️ websockets 패키지 없음: pip install websockets")
        return []

    token = _get_kiwoom_token_sync()
    if not token:
        return []

    codes = []
    seen  = set()

    try:
        async with _ws.connect(
            "wss://api.kiwoom.com:10000/api/dostk/websocket",
            ping_interval=None,
        ) as ws:
            # 로그인
            await ws.send(json.dumps({"trnm": "LOGIN", "token": token}))
            res = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            if res.get("return_code") != 0:
                print(f"⚠️ 키움 로그인 실패")
                return []
            print("✅ 키움 WebSocket 로그인 (관심그룹)")

            # 관심그룹 목록
            await ws.send(json.dumps({"trnm": "INTSLST"}))
            grp_list = []
            while True:
                try:
                    res = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                    if res.get("trnm") == "PING":
                        await ws.send(json.dumps(res))
                        continue
                    if res.get("trnm") == "INTSLST":
                        grp_list = res.get("data", [])
                        break
                except asyncio.TimeoutError:
                    break

            print(f"  📂 키움 관심그룹 전체: {len(grp_list)}개")

            for grp in grp_list:
                if isinstance(grp, dict):
                    grp_no   = str(grp.get("grp_no", grp.get("intstock_grp_no", "")))
                    grp_name = grp.get("grp_name", grp.get("intstock_grp_name", ""))
                elif isinstance(grp, list):
                    grp_no   = str(grp[0]) if len(grp) > 0 else ""
                    grp_name = grp[1]      if len(grp) > 1 else ""
                else:
                    continue

                # 업종_* / 테마 / new 그룹만 사용
                is_sector = grp_name.startswith("업종")
                is_theme  = grp_name == "테마" or grp_name.startswith("테마")
                is_new    = grp_name.lower() in ("new", "신규추천", "신규")

                if not (is_sector or is_theme or is_new):
                    print(f"  ⏭️ [{grp_no}]{grp_name} 제외")
                    continue

                source = ("hts_sector" if is_sector
                          else "hts_theme" if is_theme
                          else "hts_new")

                # 그룹 종목 조회
                await ws.send(json.dumps({
                    "trnm": "INTSTKL",
                    "intstock_grp_no": grp_no,
                }))
                fetched = 0
                while True:
                    try:
                        res = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
                        if res.get("trnm") == "PING":
                            await ws.send(json.dumps(res))
                            continue
                        if res.get("return_code") != 0:
                            break
                        for item in (res.get("data") or []):
                            if isinstance(item, dict):
                                raw  = item.get("stk_code", item.get("9001", ""))
                                name = item.get("stk_name", item.get("302", ""))
                            elif isinstance(item, list):
                                raw  = item[0] if item else ""
                                name = item[1] if len(item) > 1 else ""
                            else:
                                continue
                            code = raw.lstrip("A") if raw.startswith("A") else raw
                            if code and code.isdigit() and code not in seen:
                                seen.add(code)
                                codes.append((code, name.strip(), source))
                                fetched += 1
                        if res.get("cont_yn") != "Y":
                            break
                    except asyncio.TimeoutError:
                        break

                label = ("🏭업종" if is_sector
                         else "🎯테마" if is_theme
                         else "🆕new")
                print(f"  {label} [{grp_no}]{grp_name}: +{fetched}개")

    except Exception as e:
        print(f"⚠️ 키움 WebSocket 관심그룹 오류: {e}")

    s = sum(1 for _, _, src in codes if src == "hts_sector")
    t = sum(1 for _, _, src in codes if src == "hts_theme")
    n = sum(1 for _, _, src in codes if src == "hts_new")
    print(f"✅ 키움 관심그룹 총 {len(codes)}개 (업종:{s} 테마:{t} new:{n})")
    return codes


def _sync_watchlist_to_state(codes: list) -> dict:
    """키움 관심그룹을 단타/스윙 봇 상태에 동기화"""
    if not codes:
        return {"added": 0, "removed": 0, "total": 0, "codes": []}

    all_codes   = {code: name   for code, name, _   in codes}
    all_sources = {code: source for code, _, source in codes}
    hts_codes   = list(all_codes.keys())
    expire_date = (now_kst() + datetime.timedelta(days=30)).strftime("%Y-%m-%d")
    summary     = {"added": 0, "removed": 0, "total": len(hts_codes), "codes": hts_codes}

    for bot_name in ("sbot2", "sbot"):
        state     = read_state(bot_name)
        watchlist = state.get("watchlist", [])
        wl_expire = state.get("watchlist_expire", {})
        wl_source = state.get("watchlist_source", {})

        old_hts = {c for c, src in wl_source.items() if src.startswith("hts_")}
        new_hts = set(hts_codes)

        # 신규
        for code in new_hts - old_hts:
            if code not in watchlist:
                watchlist.append(code)
            wl_expire[code] = expire_date
            wl_source[code] = all_sources.get(code, "hts_theme")
            summary["added"] += 1

        # 유지 — source만 갱신 (그룹 변경 반영)
        for code in new_hts & old_hts:
            wl_source[code] = all_sources.get(code, wl_source.get(code, "hts_theme"))

        # 제거 (HTS에서 빠진 종목)
        for code in old_hts - new_hts:
            if code in watchlist:
                watchlist.remove(code)
            wl_expire.pop(code, None)
            wl_source.pop(code, None)
            summary["removed"] += 1

        # 종목명 매핑 갱신
        code_name_map = state.get("last_status", {}).get("code_name_map", {})
        code_name_map.update(all_codes)

        state["watchlist"]        = watchlist
        state["watchlist_expire"] = wl_expire
        state["watchlist_source"] = wl_source
        state["hts_watchlist"]    = all_codes
        state["hts_updated_at"]   = now_kst().strftime("%Y-%m-%d %H:%M")
        write_state(bot_name, state)

    print(f"✅ HTS 동기화: +{summary['added']} -{summary['removed']} 총{summary['total']}개")
    return summary


async def cmd_watchlist_hts(ctx):
    """!관심HTS 명령어 — 키움 관심그룹 즉시 동기화"""
    await ctx.send("📋 키움 관심그룹 동기화 중... (업종/테마/new 그룹만)")

    codes   = await _fetch_kiwoom_watchlist_ws()
    summary = _sync_watchlist_to_state(codes)

    total   = summary.get("total", 0)
    added   = summary.get("added", 0)
    removed = summary.get("removed", 0)

    if total == 0:
        await ctx.send(
            "⚠️ 키움 관심그룹 조회 실패\n"
            "확인: KIWOOM_APPKEY / KIWOOM_SECRETKEY 환경변수\n"
            "HTS 관심그룹명에 '업종' / '테마' / 'new' 포함 여부 확인"
        )
        return

    state    = read_state("sbot2")
    name_map = state.get("last_status", {}).get("code_name_map", {})
    name_map.update(state.get("hts_watchlist", {}))
    watchlist = state.get("watchlist", [])
    wl_source = state.get("watchlist_source", {})

    sector_items = [c for c in watchlist if wl_source.get(c) == "hts_sector"]
    theme_items  = [c for c in watchlist if wl_source.get(c) == "hts_theme"]
    new_items    = [c for c in watchlist if wl_source.get(c) == "hts_new"]
    manual_items = [c for c in watchlist if not wl_source.get(c, "").startswith("hts_")]

    msg = f"📋 **키움 관심그룹 동기화 완료**\n총 {total}개 | ✅ 추가:{added} | ❌ 제거:{removed}\n\n"

    if sector_items:
        names = ", ".join(f"{c}({name_map.get(c, c)})" for c in sector_items[:6])
        extra = f" 외 {len(sector_items) - 6}개" if len(sector_items) > 6 else ""
        msg  += f"🏭 **업종대표** ({len(sector_items)}개): {names}{extra}\n"
        msg  += "   → 강세 업종 감지 시 가점 +10점\n\n"
    if theme_items:
        names = ", ".join(f"{c}({name_map.get(c, c)})" for c in theme_items[:6])
        extra = f" 외 {len(theme_items) - 6}개" if len(theme_items) > 6 else ""
        msg  += f"🎯 **테마대표** ({len(theme_items)}개): {names}{extra}\n"
        msg  += "   → 항상 풀 포함 + 가점 +5점\n\n"
    if new_items:
        names = ", ".join(f"{c}({name_map.get(c, c)})" for c in new_items[:5])
        msg  += f"🆕 **신규추천** ({len(new_items)}개): {names}\n"
        msg  += "   → 항상 풀 포함 + 가점 +7점\n\n"
    if manual_items:
        names = ", ".join(f"{c}({name_map.get(c, c)})" for c in manual_items)
        msg  += f"✋ **수동추가** ({len(manual_items)}개): {names}\n\n"

    msg += "💡 HTS 관심그룹 변경 → 09:00 / 11:00 / 14:00 자동 반영"
    await send_long(ctx, msg)


# ============================================================
# 브리핑 (모닝 / 저녁)
# ============================================================
def _translate_to_korean(text: str) -> str:
    """영문 검색결과를 한국어 1줄로 요약. 검색 결과 없으면 보간하지 않음."""
    if not text or text in ("정보 없음", "검색 결과 없음"):
        return "검색 결과 없음"   # ★ AI 보간 차단 — 빈값 그대로 반환
    clean = text.replace("[요약]", "").replace("[Summary]", "").strip()
    korean_count = sum(1 for c in clean if "\uac00" <= c <= "\ud7a3")
    if len(clean) > 0 and korean_count / len(clean) > 0.2:
        return clean[:80]
    try:
        llm = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        res = _claude_call(llm, 
            model=DEFAULT_MODEL, max_tokens=80,
            messages=[{
                "role": "user",
                "content": (
                    "다음 검색 결과를 한국어 1줄(30자 이내)로 요약해줘. "
                    "숫자/퍼센트는 정확히 그대로 유지해. "
                    "검색 결과에 없는 숫자는 절대 추가하지 마:\n"
                    f"{clean[:300]}"
                ),
            }],
        )
        return res.content[0].text.strip()
    except Exception:
        return clean[:80]


# ── 브리핑 모듈 import ──────────────────────────────────────
from kiki_briefing import (
    _get_finnhub_events,
    _format_finnhub_events,
    _claude_call,
    _get_global_market,
    _format_global_market,
    _get_us_events,
    _get_foreign_flow_summary,
    _build_briefing_msg,
    _build_evening_briefing_msg,
)
import kiki_briefing as _kb

# ============================================================
# 도움말
# ============================================================
async def cmd_total_performance(ctx, days: int = 30):
    """master_trades 기반 전체 봇 통합 성과"""
    try:
        from master_db import get_performance, get_today_summary
        import datetime

        today_sum = get_today_summary()
        perf      = get_performance(days=days)
        perf_n    = get_performance(days=days, bot_type="sbot2")
        perf_s    = get_performance(days=days, bot_type="sbot")
        perf_c    = get_performance(days=days, bot_type="cbot")

        today_total = sum(v["pnl"] for v in today_sum.values())
        today_lines = []
        for bot, v in today_sum.items():
            emoji = "📈" if v["pnl"] >= 0 else "📉"
            today_lines.append(
                f"  {emoji} {bot}: {v['pnl']:+,}원 ({v['count']}건 승률{v['win_rate']}%)"
            )

        sep = "-" * 20
        today_body = chr(10).join(today_lines or ["  데이터 없음"])
        parts_msg = [
            "📊 영암9 통합 성과 (" + str(days) + "일)",
            sep,
            "오늘",
            today_body,
            "  합계: " + "{:+,}".format(today_total) + "원",
            sep,
            str(days) + "일 누적",
            "  전체: " + str(perf["total"]) + "건 | 승률:" + str(perf["win_rate"]) + "% | 누적:" + "{:+,}".format(perf["total_krw"]) + "원",
            "  평균:" + "{:+.2f}".format(perf["avg_rate"]) + "% | 최고:" + "{:+.2f}".format(perf["best"]) + "% | 최저:" + "{:+.2f}".format(perf["worst"]) + "%",
            sep,
            "봇별 성과",
            "  단타: " + str(perf_n["total"]) + "건 | 승률:" + str(perf_n["win_rate"]) + "% | " + "{:+,}".format(perf_n["total_krw"]) + "원",
            "  스윙: " + str(perf_s["total"]) + "건 | 승률:" + str(perf_s["win_rate"]) + "% | " + "{:+,}".format(perf_s["total_krw"]) + "원",
            "  코인: " + str(perf_c["total"]) + "건 | 승률:" + str(perf_c["win_rate"]) + "% | " + "{:+,}".format(perf_c["total_krw"]) + "원",
        ]
        msg = chr(10).join(parts_msg)
        await send_long(ctx, msg)
    except Exception as e:
        await ctx.send("❌ 전체성과 오류: " + str(e))





async def cmd_event(ctx):
    """텔레그램 빅 이벤트 현황 조회"""
    try:
        import sqlite3, os
        db = os.path.join('/home/free4tak/k-bot/stock_bot/intelligence', 'telegram_events.db')
        if not os.path.exists(db):
            await ctx.send("⚠️ 텔레그램 이벤트 DB 없음")
            return

        conn = sqlite3.connect(db, timeout=3)

        # 활성 이벤트 보너스
        bonus_rows = conn.execute("""
            SELECT theme, bonus_score, reason, expires_at
            FROM event_bonus
            WHERE expires_at > datetime('now','localtime')
            ORDER BY bonus_score DESC
        """).fetchall()

        # 최근 이벤트 5건
        recent_rows = conn.execute("""
            SELECT channel, keywords, themes, score, created_at
            FROM telegram_events
            ORDER BY id DESC LIMIT 5
        """).fetchall()
        conn.close()

        msg = "🚨 **텔레그램 빅 이벤트 현황**\n━━━━━━━━━━━━━━━━━━━━\n"

        if bonus_rows:
            msg += "**🔥 활성 가산점 (2시간 유효)**\n"
            for theme, score, reason, exp in bonus_rows:
                msg += f"  +{score}점 | {theme} | {reason[:30]}\n"
        else:
            msg += "활성 이벤트 없음\n"

        msg += "\n**📰 최근 감지 이벤트**\n"
        if recent_rows:
            for ch, kw, th, sc, ts in recent_rows:
                msg += f"  [{ts[11:16]}] {ch} | {kw} | +{sc}점\n"
        else:
            msg += "최근 이벤트 없음\n"

        await ctx.send(msg)
    except Exception as e:
        await ctx.send(f"❌ 오류: {e}")


async def cmd_risk(ctx):
    """통합 리스크 상태 조회"""
    try:
        import sys, os
        sys.path.insert(0, '/home/free4tak/k-bot/stock_bot/core')
        from master_db import get_risk_state, set_pause_all, set_daily_loss_limit

        state = get_risk_state()
        if not state:
            await ctx.send("⚠️ 리스크 데이터 없음")
            return

        loss        = state.get('total_loss_krw', 0)
        profit      = state.get('total_profit_krw', 0)
        limit       = state.get('daily_loss_limit', 700000)
        ratio       = loss / limit * 100 if limit > 0 else 0
        level       = state.get('risk_level', 'normal')
        paused      = state.get('paused_all', False)
        warn_line   = int(limit * 0.7)

        emoji = {'normal': '🟢', 'warning': '🟡', 'danger': '🔴'}.get(level, '⚪')
        pause_str = '⛔ 전봇 중단 중!' if paused else '✅ 정상 운영'

        msg = (
            f"{emoji} **리스크 상태** — {pause_str}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"당일 손실: {loss:,.0f}원 ({ratio:.0f}%)\n"
            f"당일 수익: {profit:,.0f}원\n"
            f"순손익:   {profit-loss:+,.0f}원\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"nbot: {state.get('nbot_loss_krw',0):,.0f}원\n"
            f"sbot: {state.get('sbot_loss_krw',0):,.0f}원\n"
            f"cbot: {state.get('cbot_loss_krw',0):,.0f}원\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"경고선: {warn_line:,.0f}원 | 한도: {limit:,.0f}원\n"
        )
        await ctx.send(msg)
    except Exception as e:
        await ctx.send(f"❌ 리스크 조회 오류: {e}")


async def cmd_risk_pause(ctx):
    """전봇 긴급중단"""
    try:
        import sys
        sys.path.insert(0, '/home/free4tak/k-bot/stock_bot/core')
        from master_db import set_pause_all
        set_pause_all(True)
        await ctx.send("🚨 **전봇 긴급중단 ON** — 매수 중단됨\n재개: !리스크재개")
    except Exception as e:
        await ctx.send(f"❌ 오류: {e}")


async def cmd_risk_resume(ctx):
    """전봇 긴급중단 해제"""
    try:
        import sys
        sys.path.insert(0, '/home/free4tak/k-bot/stock_bot/core')
        from master_db import set_pause_all
        set_pause_all(False)
        await ctx.send("✅ **전봇 재개** — 정상 운영 중")
    except Exception as e:
        await ctx.send(f"❌ 오류: {e}")


async def cmd_news(ctx):
    """오늘 뉴스 감성 분석 결과 조회"""
    try:
        import sqlite3 as _sl
        import os as _os
        from datetime import datetime as _dt
        _db = _os.path.join(
            _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
            "intelligence", "news_sentiment.db"
        )
        if not _os.path.exists(_db):
            await ctx.send("⚠️ 뉴스 DB 없음 — 08:30 수집 후 사용 가능")
            return

        today = _dt.now().strftime("%Y%m%d")
        conn = _sl.connect(_db, timeout=3)
        conn.execute("PRAGMA query_only = ON")

        # 오늘 데이터 있는지 확인
        total = conn.execute(
            "SELECT COUNT(*) FROM news_sentiment WHERE date=?", (today,)
        ).fetchone()[0]

        if total == 0:
            await ctx.send(f"⚠️ 오늘({today}) 뉴스 데이터 없음 — 08:30 수집 후 사용 가능")
            conn.close()
            return

        # 키워드별 감성 점수
        rows = conn.execute("""
            SELECT keyword,
                   AVG(CASE sentiment
                       WHEN '긍정' THEN 1
                       WHEN '부정' THEN -1
                       ELSE 0 END) as score,
                   COUNT(*) as cnt,
                   SUM(CASE WHEN sentiment='긍정' THEN 1 ELSE 0 END) as pos,
                   SUM(CASE WHEN sentiment='부정' THEN 1 ELSE 0 END) as neg
            FROM news_sentiment
            WHERE date=?
            GROUP BY keyword
            ORDER BY score DESC
        """, (today,)).fetchall()

        # 전체 감성
        all_rows = conn.execute("""
            SELECT sentiment, COUNT(*) FROM news_sentiment
            WHERE date=? GROUP BY sentiment
        """, (today,)).fetchall()
        conn.close()

        sent_map = dict(all_rows)
        pos_total = sent_map.get('긍정', 0)
        neg_total = sent_map.get('부정', 0)
        neu_total = sent_map.get('중립', 0)

        lines = [f"📊 **{today} 뉴스 감성 리포트** (총 {total}건)"]
        lines.append(f"긍정 {pos_total/total*100:.0f}% | 부정 {neg_total/total*100:.0f}% | 중립 {neu_total/total*100:.0f}%")
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        for kw, score, cnt, pos, neg in rows:
            emoji = "▲" if score > 0.2 else ("▼" if score < -0.2 else "●")
            lines.append(f"{emoji} **{kw}**: {score:+.2f} ({cnt}건, 긍{pos}/부{neg})")

        # AI 요약 파일
        summary_path = _os.path.join(
            _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
            "intelligence", "latest_ai_summary.txt"
        )
        if _os.path.exists(summary_path):
            with open(summary_path, encoding="utf-8") as f:
                summary = f.read()
            lines.append("━━━━━━━━━━━━━━━━━━━━")
            lines.append("💡 **AI 종합 의견**")
            # 종합 의견 줄만 추출
            for line in summary.split("\n"):
                if "종합 의견" in line or "💡" in line:
                    lines.append(line)

        await ctx.send("\n".join(lines))
    except Exception as e:
        await ctx.send(f"❌ 뉴스 조회 오류: {e}")

async def cmd_help(ctx):
    msg = """🦊 **키키 — 명령어 안 외워도 돼요!**
그냥 말하면 알아서 해줍니다 😄

━━━ 💬 이렇게 말하면 돼요 ━━━
  "지금 어때?"          → 전체 봇 현황
  "오늘 얼마 벌었어?"   → 오늘 손익
  "왜 손해났지?"        → 오늘 매매 분석
  "MINA 팔아줘"         → 코인 매도 (확인 후)
  "단타봇 멈춰줘"       → 정지 (확인 후)
  "점수 60으로 올려줘"  → 기준점수 변경
  "코스피 어때?"        → 시황 검색
  "반도체 강해?"        → 업종 분석

━━━ ⌨️ 명령어 직접 입력 ━━━
**📈 단타봇**  `!상태` `!정지` `!시작` `!매도 코드` `!점수기준 숫자`
**📊 스윙봇**  `!s상태` `!s정지` `!s시작` `!s매도 코드`
**🪙 코인봇**  `!c상태` `!c정지` `!c시작` `!c매도 BTC`

**📊 성과/분석**
  `!성과`         — 오늘 손익
  `!성과상세`     — 샤프/MDD/시간대별 (30일)
  `!성과상세 60`  — 최근 60일 상세
  `!분석오늘`     — 오늘 매매 AI 분석
  `!분석이번주`   — 이번주 패턴 분석

**🌐 공통**
  `!전체상태`  `!브리핑`  `!테마`  `!관심HTS`

━━━ 🌟 Tip ━━━
매도/정지는 확인 후 실행 → "네"=실행 / "아니"=취소
"""
    await send_long(ctx, msg)

async def cmd_cbot_status(ctx):
    state  = read_state("cbot")
    status = state.get("last_status", {})
    paused = "⏸️ 일시중단" if state.get("paused") else "▶️ 실행중"
    now    = now_kst().strftime("%H:%M:%S")
    coins  = status.get("coin_pool", status.get("coins", []))

    lines = [
        f"🪙 **[코인봇] 영암9 COIN 현황** [{now}]",
        f"상태: {paused}",
        f"💵 KRW 잔고: {status.get('krw', 0):,}원",
        f"📈 평가손익: {status.get('total_profit', 0):+,}원",
        f"💰 당일PNL: {status.get('daily_pnl', 0):+,}원",
        f"📊 포지션: {status.get('positions', 0)}/{3}",
        f"📉 당일 손절: {status.get('daily_loss', 0)}회",
        f"😨 공포탐욕: {status.get('fear_greed', 50)} | "
        f"BTC: {status.get('btc_rate', 0):+.2f}% | "
        f"시장: {status.get('market_status', 'normal')}",
        f"🪙 종목 풀: {len(coins)}개",
        "", "**📦 보유 코인**",
    ]
    pos_detail = status.get("positions_detail", {})
    if pos_detail:
        for market, info in pos_detail.items():
            emoji = "📈" if info.get("rate", 0) >= 0 else "📉"
            lines.append(
                f"  {emoji} {market} | 현재:{info.get('current', 0):,}원 | "
                f"{info.get('rate', 0):+.2f}% | {info.get('qty', 0):.6f}개"
            )
    else:
        lines.append("  보유 코인 없음")

    perf_rows = get_coin_performance(limit=10)
    if perf_rows:
        profits = [r[0] for r in perf_rows if r[0] is not None]
        if profits:
            wins = [p for p in profits if p >= 0]
            lines.append(
                f"\n📊 최근 {len(profits)}건 | "
                f"승률:{round(len(wins) / len(profits) * 100, 1)}% | "
                f"평균:{round(sum(profits) / len(profits), 2):+.2f}%"
            )
    await send_long(ctx, "\n".join(lines))


async def cmd_cbot_sell(ctx, market: str):
    """
    코인 매도.
    ★ 개선: FIXED_COINS 외 보유 코인도 매도 가능 (cbot 상태에서 동적 조회)
    """
    if not market.startswith("KRW-"):
        market = f"KRW-{market.upper()}"

    # ★ 동적 valid 코인 — cbot의 현재 보유 코인 + 종목 풀에서 조회
    cbot_state = read_state("cbot")
    cbot_status = cbot_state.get("last_status", {})
    held = list(cbot_status.get("positions_detail", {}).keys())
    pool = cbot_status.get("coin_pool", cbot_status.get("coins", []))
    valid = list(set(held + pool))

    if not valid:
        valid = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL"]

    if market not in valid:
        if held:
            await ctx.send(
                f"❌ '{market}' 미보유\n"
                f"현재 보유: {', '.join(held)}\n"
                f"예) !c매도 BTC"
            )
        else:
            await ctx.send(
                f"❌ '{market}' 매도 불가 (코인봇 미실행 또는 미보유)\n"
                f"예) !c매도 BTC"
            )
        return

    update_state("cbot", pending_cmd={"type": "sell", "market": market})
    await ctx.send(f"📤 코인 매도 명령: **{market}**\n(다음 루프 ~5분 내 실행)")

    result = await wait_cmd_result("cbot", max_attempts=12, interval=5.0)
    if result:
        await ctx.send(f"✅ {result}")
    else:
        await ctx.send("⚠️ 응답 없음 — cbot.py 실행 중인지 확인하세요")


async def cmd_cbot_performance(ctx):
    rows = get_coin_performance(limit=20)
    if not rows:
        await ctx.send("🪙 코인봇 매매 이력 없음")
        return
    profits = [r[0] for r in rows if r[0] is not None]
    if not profits:
        await ctx.send("🪙 코인봇 매매 이력 없음")
        return
    wins   = [p for p in profits if p >= 0]
    w_rate = round(len(wins) / len(profits) * 100, 1)
    avg    = round(sum(profits) / len(profits), 2)
    msg  = f"🪙 **코인봇 최근 {len(profits)}건 성과**\n"
    msg += f"승률: {w_rate}% | 평균: {avg:+.2f}%\n"
    msg += f"최고: {max(profits):+.2f}% | 최저: {min(profits):+.2f}%\n\n"
    msg += "**최근 매매 내역**\n"
    for pr, sr, ais, market, bp, sp, bt, st in rows[:10]:
        emoji = "✅" if (pr or 0) >= 0 else "❌"
        msg  += f"  {emoji} {market} | {(pr or 0):+.2f}% | {sr}\n"
    await send_long(ctx, msg)

