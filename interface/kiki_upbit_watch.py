"""
kiki_upbit_watch.py — 업비트 서버 점검 미리 알림 + 점검 시작/끝 알림 (2026-10-19)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
10-19 업비트 정기점검(02:00~08:00) 동안 cbot이 6시간 못 움직였는데, 공지는 10-16 11시에 이미 떠 있었음.
대장: "업비트가 안내할 텐데 그걸 캐치해서 알림 주는 게 낫다 — 관리 차원이니 키키에." 두 곳을 본다(키 불필요).
  · 고객센터 공지(support.upbit.com, 1시간마다) — 제목에 서버/긴급/정기/시스템 점검이 든 공지가 새로 뜨거나
    수정되면 알림(본문의 '점검 시간'을 뽑아서), 시작 1시간 전에 한 번 더. 코인별 입출금 점검은 너무 잦아 뺌
  · 점검 상태 스위치(pages.upbit.com/api/kr_status.json, 10분마다) — 업비트 점검 페이지가 읽는 파일.
    켜지면 '점검 시작', 꺼지면 '점검 끝' 알림 → 공지 없는 비상 점검도 잡힘
본 공지·상태는 kiki_upbit_watch.json에 저장(키키 재시작해도 같은 알림 반복 안 함). 주문 없음.
"""
import os
import re
import json
import html
import time
import asyncio
import datetime

import requests

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_FILE = os.path.join(BASE, "kiki_upbit_watch.json")
STATUS_URL = "https://pages.upbit.com/api/kr_status.json"
NOTICE_URL = "https://support.upbit.com/api/v2/help_center/ko/articles.json"
KST = datetime.timezone(datetime.timedelta(hours=9))
STATUS_EVERY = 600
NOTICE_EVERY = 3600
REMIND_BEFORE = datetime.timedelta(hours=1)

_SERVER_RE = re.compile(r"(서버|시스템|서비스|긴급|정기|임시)\s*점검")
_TIME_RE = re.compile(r"점검\s*시간\s*[:：]?\s*(\d{4})-(\d{1,2})-(\d{1,2})\s*(?:\([^)]*\))?\s*(\d{1,2}):(\d{2})"
                      r"\s*~\s*(?:(\d{4})-(\d{1,2})-(\d{1,2})\s*(?:\([^)]*\))?\s*)?(\d{1,2}):(\d{2})")


def text_of(body: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", body or ""))).strip()


def is_server_notice(title: str) -> bool:
    return bool(_SERVER_RE.search(title or "")) and "입출금" not in (title or "")


def parse_window(text: str):
    """본문의 '점검 시간 : 2026-10-19(월) 02:00 ~ 08:00' → (시작, 끝) KST naive. 못 찾으면 None."""
    m = _TIME_RE.search(text or "")
    if not m:
        return None
    y, mo, d, h, mi, y2, mo2, d2, h2, mi2 = m.groups()
    start = datetime.datetime(int(y), int(mo), int(d), int(h), int(mi))
    end_day = datetime.date(int(y2), int(mo2), int(d2)) if y2 else start.date()
    end = datetime.datetime.combine(end_day, datetime.time(int(h2), int(mi2)))
    if end <= start:
        end += datetime.timedelta(days=1)
    return start, end


def fmt_window(w) -> str:
    s, e = w
    wd = "월화수목금토일"
    span = (e - s).total_seconds() / 3600
    e_txt = f"{e:%H:%M}" if e.date() == s.date() else f"{e:%m/%d} {e:%H:%M}"
    return f"{s:%m/%d}({wd[s.weekday()]}) {s:%H:%M} ~ {e_txt} (약 {span:g}시간)"


def load_state(path: str = STATE_FILE) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(st: dict, path: str = STATE_FILE):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def check_status(st: dict, fetch) -> list:
    """점검 스위치가 바뀌었으면 알림 문구 목록. 처음 보는데 꺼져 있으면 조용히 기록만."""
    d = fetch(STATUS_URL, {"nonce": int(time.time())})
    on = bool(d.get("maintenance"))
    prev = st.get("maintenance")
    st["maintenance"] = on
    if prev is None and not on:
        return []
    if on and prev is not True:
        detail = ""
        if d.get("content1_url"):
            try:
                detail = text_of(fetch(d["content1_url"], None, text=True))
            except Exception:
                pass
        st["maint_since"] = datetime.datetime.now(KST).isoformat(timespec="minutes")
        return [f"🔧 **업비트 점검 시작** — {d.get('title') or '서비스 점검 중'}\n{detail[:200]}".rstrip()]
    if not on and prev is True:
        since = st.pop("maint_since", None)
        dur = ""
        if since:
            mins = (datetime.datetime.now(KST) - datetime.datetime.fromisoformat(since)).total_seconds() / 60
            dur = f" (약 {mins / 60:.1f}시간)"
        return [f"✅ **업비트 점검 끝**{dur} — cbot 시세·주문 다시 가능"]
    return []


def check_notices(st: dict, fetch, now: datetime.datetime) -> list:
    """새로 뜨거나 수정된 서버 점검 공지 알림. 처음 실행 땐 이미 지난 점검은 조용히 '본 것'으로만."""
    d = fetch(NOTICE_URL, {"sort_by": "updated_at", "sort_order": "desc", "per_page": 30})
    seen, sched = st.setdefault("seen", {}), st.setdefault("schedule", {})
    first = not st.get("notices_init")
    out = []
    for a in d.get("articles", []):
        if not is_server_notice(a.get("title", "")):
            continue
        aid, ver = str(a["id"]), a.get("edited_at") or a.get("updated_at") or ""
        if seen.get(aid) == ver:
            continue
        seen[aid] = ver
        w = parse_window(text_of(a.get("body", "")))
        if w:
            sched[aid] = {"title": a["title"], "start": w[0].isoformat(), "end": w[1].isoformat(),
                          "reminded": sched.get(aid, {}).get("reminded", False)}
        if first and (not w or w[1] <= now):
            continue
        if w and w[1] <= now:
            continue
        when = fmt_window(w) if w else "시간은 공지 확인"
        tag = "진행 중" if w and w[0] <= now else "예정"
        out.append(f"📢 **업비트 서버 점검 {tag}** — {a['title']}\n🕒 {when}\n"
                   f"그동안 업비트 전체 이용 불가 → cbot 매매·손절 멈춤\n{a.get('html_url', '')}".rstrip())
    st["notices_init"] = True
    for aid in [k for k, v in sched.items() if datetime.datetime.fromisoformat(v["end"]) < now - datetime.timedelta(days=7)]:
        sched.pop(aid)
    return out


def due_reminders(st: dict, now: datetime.datetime) -> list:
    out = []
    for v in st.get("schedule", {}).values():
        s = datetime.datetime.fromisoformat(v["start"])
        if not v.get("reminded") and s - REMIND_BEFORE <= now < s:
            v["reminded"] = True
            w = (s, datetime.datetime.fromisoformat(v["end"]))
            out.append(f"⏰ **업비트 점검 {int((s - now).total_seconds() // 60)}분 전** — {fmt_window(w)}\n"
                       f"cbot 보유 코인은 점검 동안 손절 불가 — 필요하면 지금 정리 판단")
    return out


def _fetch(url: str, params, text: bool = False):
    r = requests.get(url, params=params, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    return r.content.decode("utf-8", errors="replace") if text else r.json()   # 텍스트 파일은 charset 없이 와서 깨짐


def _cbot_holdings() -> str:
    try:
        with open(os.path.join(BASE, "cbot_state.json"), encoding="utf-8") as f:
            pos = list((json.load(f).get("positions") or {}).keys())
        return f"\n📦 cbot 보유 {len(pos)}개: {', '.join(p.replace('KRW-', '') for p in pos)}" if pos else ""
    except (OSError, ValueError):
        return ""


_started = False


async def upbit_maintenance_watcher(bot, channel_id: int):
    """kiki on_ready에서 한 번만 띄움(재연결로 on_ready가 또 불려도 중복 실행 안 함)."""
    global _started
    if _started:
        return
    _started = True
    loop = asyncio.get_event_loop()
    last_notice = 0.0
    while True:
        msgs = []
        st = load_state()
        now = datetime.datetime.now(KST).replace(tzinfo=None)
        try:
            msgs += await loop.run_in_executor(None, check_status, st, _fetch)
        except Exception as e:
            print(f"⚠️ 업비트 점검상태 조회 오류: {e}")
        if time.time() - last_notice >= NOTICE_EVERY:
            try:
                msgs += await loop.run_in_executor(None, check_notices, st, _fetch, now)
                last_notice = time.time()
            except Exception as e:
                print(f"⚠️ 업비트 공지 조회 오류: {e}")
        msgs += due_reminders(st, now)
        try:
            save_state(st)
        except OSError as e:
            print(f"⚠️ 업비트 점검 상태 저장 오류: {e}")
        ch = bot.get_channel(channel_id) if msgs else None
        for m in msgs:
            print(f"📢 {m.splitlines()[0]}")
            if ch:
                try:
                    await ch.send(m + _cbot_holdings())
                except Exception as e:
                    print(f"⚠️ 업비트 점검 알림 전송 오류: {e}")
        await asyncio.sleep(STATUS_EVERY)
