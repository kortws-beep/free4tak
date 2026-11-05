"""
mtn_morning_brief.py — MTN 아침 방송(전일장·미장 정리) 요약 (2026-11-03, 대장)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
대장: "매일 06시55분부터 07시35분정도까지 MTN에서 전일장·미장 정리를 하고 있거든 꽤 깔끔해..
      라이브로 하니까.. 우리 유튜브캐치를 그대로 쓰면 되."
youtube_live_monitor(07~20시, 60분 단위 종목 캐치)와 같은 방식 — 라이브 오디오를 yt-dlp로 받아
로컬 Whisper(CPU)로 글로 바꾼 뒤, 소넷이 핵심만 요약해서 리나 이름으로 보냄.
  06:54 크론 → MTN이 라이브면 07:41까지 녹음(07:40쯤 끝나는 '오늘 주목' 섹터 소개까지)(아직이면 07:30까지 1분마다 다시 봄) → 전사 → 요약 → 발송
  라이브가 아니면(주말·휴일 등) 조용히 끝냄.
youtube_live_monitor를 import하지 않는 이유: 그 파일은 import 순간 자기 임시폴더를 비워서
  돌고 있는 서비스의 녹음 파일을 지울 수 있음 — 캡처 몇 줄만 여기 따로 둠.
================================================================
"""
import os
import sys
import re
import time
import datetime
import subprocess
import tempfile

_here = os.path.dirname(os.path.abspath(__file__))
_base = os.path.dirname(_here)
for _d in ["core", "intelligence", "interface", "bots", ""]:
    _p = os.path.join(_base, _d)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from dotenv import load_dotenv
for _ep in [os.path.join(_here, ".env"), os.path.join(_base, ".env")]:
    if os.path.exists(_ep):
        load_dotenv(_ep)
        break

import requests

HANDLE = "mtn"
END_HHMM = "0741"           # 11-03 대장: 끝의 "오늘 주목"(매일 돌아가며 섹터 소개)까지 07:40쯤 — +1분 여유
WAIT_UNTIL = "0730"         # 이때까지 라이브 안 잡히면 그날은 쉼 — 11-04 06:54~07:05 내내 못 잡음(07:26에야 잡힘) → 늘림
MAX_SEC = 50 * 60
AUDIO_FORMAT = "233"
YTDLP_BIN = os.path.join(os.path.dirname(sys.executable), "yt-dlp")
SAVE_DIR = os.path.join(_base, "logs", "mtn_morning")   # 전사 원문 보관(요약 검증용)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
}


def live_video_id(handle: str = HANDLE) -> str:
    """페이지의 isLiveNow로 먼저 보고, 못 찾으면 yt-dlp로 한 번 더 — 11-04 대장이 라이브를 보고 있었는데
    페이지 확인은 06:54~07:25 내내 '라이브 아님'(유튜브가 다른 페이지를 준 듯)."""
    return _page_live_id(handle) or _ytdlp_live_id(handle)


def _ytdlp_live_id(handle: str) -> str:
    try:
        res = subprocess.run([YTDLP_BIN, "--skip-download", "--print", "%(id)s %(is_live)s",
                              f"https://www.youtube.com/@{handle}/live"],
                             capture_output=True, text=True, timeout=60)
        parts = res.stdout.split()
        if len(parts) >= 2 and parts[1] == "True":
            print(f"{datetime.datetime.now():%H:%M:%S} yt-dlp로 라이브 확인 {parts[0]}")
            return parts[0]
        print(f"{datetime.datetime.now():%H:%M:%S} yt-dlp도 라이브 아님: {res.stdout.strip()[:80]} {res.stderr.strip()[-120:]}")
    except Exception as e:
        print(f"⚠️ yt-dlp 라이브 확인 오류: {e}")
    return ""


def _page_live_id(handle: str) -> str:
    try:
        r = requests.get(f"https://www.youtube.com/@{handle}/live", headers=HEADERS, timeout=15)
        if '"isLiveNow":true' not in r.text:
            title = re.search(r"<title>(.*?)</title>", r.text)
            print(f"{datetime.datetime.now():%H:%M:%S} 라이브 아님 — HTTP {r.status_code} · {len(r.text)}자 · "
                  f"{title.group(1)[:60] if title else '제목없음'}")
            return ""
        m = re.search(r'"videoId":"([^"]+)"', r.text)
        return m.group(1) if m else ""
    except Exception as e:
        print(f"⚠️ 라이브 확인 오류: {e}")
        return ""


def seconds_until(hhmm: str, now: datetime.datetime = None) -> int:
    now = now or datetime.datetime.now()
    end = now.replace(hour=int(hhmm[:2]), minute=int(hhmm[2:]), second=0, microsecond=0)
    return max(0, min(MAX_SEC, int((end - now).total_seconds())))


def capture(video_id: str, sec: int, out_path: str) -> bool:
    cmd = [YTDLP_BIN, "-f", AUDIO_FORMAT, "--downloader", "ffmpeg",
           "--downloader-args", f"ffmpeg_i:-t {sec}", "-o", out_path,
           f"https://www.youtube.com/watch?v={video_id}"]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=sec + 120)
        if res.returncode != 0 or not os.path.exists(out_path):
            print(f"⚠️ 캡처 실패: {res.stderr[-500:]}")
            return False
        return True
    except Exception as e:
        print(f"⚠️ 캡처 오류: {e}")
        return False


def transcribe(path: str) -> str:
    from faster_whisper import WhisperModel
    model = WhisperModel(os.getenv("YT_WHISPER_MODEL", "small"), device="cpu", compute_type="int8")
    segments, _ = model.transcribe(path, language="ko", vad_filter=True)
    return " ".join(s.text for s in segments)


def build_prompt(text: str, today: str) -> str:
    return (
        f"오늘은 {today}이야. 아래는 MTN 머니투데이방송 아침 라이브(06:55~07:40, 전일 국내장·간밤 미국장 정리)를 "
        "음성인식으로 받아적은 글이야. 음성인식이라 종목명·숫자가 틀리게 적혔을 수 있어.\n\n"
        f"[방송 받아쓰기]\n{text}\n\n"
        "[작성 지침]\n"
        "1. 한국어로, 디스코드 메시지 하나(1500자 이내)로 정리해.\n"
        "2. ★ 종목 추천이 아니라 '내용'이 중요해 — 시장이 왜 그렇게 움직였는지(원인·해석·자금 흐름)와 "
        "방송이 짚은 오늘의 관전 포인트를 담아. 종목명은 섹터 흐름을 설명하는 예시로만 짧게.\n"
        "3. 순서: 🇺🇸 간밤 미장(지수 + 왜 움직였나) → 🇰🇷 전일 국장(흐름·수급 + 왜) → 🧭 방송의 시각(오늘 장 해석·섹터 흐름) "
        "→ 🔦 오늘 주목(방송 끝부분에 매일 돌아가며 소개하는 섹터 — 어떤 섹터를 왜 골랐는지, 업황·근거 위주) → ⚠️ 오늘 일정·변수.\n"
        "4. 각 항목은 짧은 글머리표로. 방송에서 말한 숫자만 쓰고, 어색한 숫자·종목명은 빼거나 '(확인 필요)'를 붙여.\n"
        "5. 방송에 없는 내용은 절대 지어내지 마. 광고·시청자 안내 같은 잡담은 빼.\n"
        "6. 받아쓰기가 너무 짧거나 방송 내용이 아니면 '오늘 방송 내용을 제대로 못 받았어'라고만 해."
    )


def _ask(prompt: str) -> str:
    import anthropic
    from common_utils import extract_claude_text
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    res = client.messages.create(
        model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5"),
        max_tokens=6000,     # 소넷5 thinking 몫까지 넉넉히 — 11-03 3000에서 본문 잘림 실측
        messages=[{"role": "user", "content": prompt}],
    )
    return extract_claude_text(res)


def summarize(text: str, today: str) -> str:
    return _ask(build_prompt(text, today))


def pick_event_theme(summary: str, today: str, ask=None, text: str = "") -> str:
    """★ 11-04 대장 — 방송에서 오늘 '이벤트로 조명받을' 테마 → event_sector에 저장(daybot 이벤트 섹터 엔진).
    ★ 11-05 대장 — 하나가 아니라 언급 1·2·3위(받아쓰기에서 자연스럽게 많이 나온 것만, 많이 나온 순). → 알림 한 줄(없으면 "")."""
    import event_sector
    import theme_open_obs
    all_themes = theme_open_obs.load_themes()
    themes = sorted(all_themes)
    picked = event_sector.parse_choice((ask or _ask)(event_sector.choose_prompt(summary, themes, today)), themes)
    ok, why = [], []
    for th, w in picked:
        n, parts = event_sector.mention_stats(text, th, all_themes.get(th, []))
        if event_sector.natural(text, th, all_themes.get(th, [])):
            ok.append((n, th, w))
        else:
            print(f"🎯 이벤트 섹터 후보 '{th}' 탈락 — 받아쓰기 언급 {n}번·{parts}구간(억지로 고르지 않음)")
    ok.sort(key=lambda x: -x[0])
    names = [th for _, th, _ in ok][:event_sector.MAX_THEMES]
    reason = " / ".join(f"{th}: {w}" for _, th, w in ok[:event_sector.MAX_THEMES])
    event_sector.save_theme(today, names, reason)
    if not names:
        return ""
    return (f"🎯 오늘 이벤트 섹터(언급순): **{' · '.join(names)}** — {reason}\n"
            f"   (daybot: 지수 장중 -{event_sector.INDEX_DROP:g}%~-{event_sector.PANIC_DROP:g}% 사이면 1슬롯 확보 → 지수 바닥 확인 뒤 "
            f"이 테마들 대장주 중 강반등 1종목)")


DB_PATH = os.path.join(_here, "mtn_morning.db")


def featured_section(summary: str) -> str:
    """요약에서 '🔦 오늘 주목' 칸만 떼어냄(없으면 "")."""
    m = re.search(r"🔦[^\n]*\n(.*?)(?=\n[^\n]*⚠️|\Z)", summary, re.S)
    return m.group(1).strip() if m else ""


def save_day(date: str, summary: str, db: str = DB_PATH):
    """★ 11-03 대장: '오늘 주목'은 전일 강했던 섹터를 돌아가며 소개 — 쌓아두면 섹터 대장 재선정(SUB 등) 때 참고."""
    import sqlite3
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE IF NOT EXISTS mtn_morning (date TEXT PRIMARY KEY, featured TEXT, summary TEXT)")
    c.execute("INSERT OR REPLACE INTO mtn_morning VALUES (?,?,?)", (date, featured_section(summary), summary))
    c.commit()
    c.close()


def split_msg(msg: str, limit: int = 1800) -> list:
    """디스코드 한 통 한도(1900자)를 넘으면 섹션(빈 줄) 단위로 나눔 — 넘치면 Notifier가 뒤를 잘라버림."""
    parts, cur = [], ""
    for block in msg.split("\n\n"):
        if cur and len(cur) + 2 + len(block) > limit:
            parts.append(cur)
            cur = block
        else:
            cur = f"{cur}\n\n{block}" if cur else block
    if cur:
        parts.append(cur)
    return [p[i:i + limit] for p in parts for i in range(0, len(p), limit)]


def _notify(msg: str):
    from notifier import Notifier
    n = Notifier(name="MTN아침", route="scan")
    for part in split_msg(msg):
        n.send(part)


def main():
    today = datetime.date.today().isoformat()
    vid = live_video_id()
    while not vid and datetime.datetime.now().strftime("%H%M") < WAIT_UNTIL:
        time.sleep(60)
        vid = live_video_id()
    if not vid:
        print(f"{today} MTN 라이브 없음 — 쉼")
        if datetime.date.today().weekday() < 5:
            _notify(f"[MTN 아침] {WAIT_UNTIL[:2]}:{WAIT_UNTIL[2:]}까지 라이브를 못 찾아 오늘 요약 없음")
        return
    sec = seconds_until(END_HHMM)
    if sec < 60:
        print("방송 끝날 시각이 지남 — 쉼")
        return
    path = os.path.join(tempfile.gettempdir(), f"mtn_morning_{int(time.time())}.m4a")
    print(f"🔴 MTN 라이브 {vid} — {sec // 60}분 녹음")
    try:
        if not capture(vid, sec, path):
            _notify("[MTN 아침] 방송 녹음 실패 — 오늘 요약 없음")
            return
        text = transcribe(path)
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    print(f"📝 전사 {len(text)}자")
    os.makedirs(SAVE_DIR, exist_ok=True)
    with open(os.path.join(SAVE_DIR, f"{today}.txt"), "w", encoding="utf-8") as f:
        f.write(text)
    if len(text) < 300:
        _notify(f"[MTN 아침] 받아쓰기가 {len(text)}자뿐이라 요약 생략")
        return
    summary = summarize(text, today)
    try:
        save_day(today, summary)
    except Exception as e:
        print(f"⚠️ 요약 저장 실패: {e}")
    try:
        evt = pick_event_theme(summary, today, text=text)
    except Exception as e:
        print(f"⚠️ 이벤트 섹터 고르기 실패: {e}")
        evt = ""
    _notify(f"📺 [MTN 아침 정리 {today[5:]}]\n{summary}" + (f"\n\n{evt}" if evt else ""))


if __name__ == "__main__":
    main()
