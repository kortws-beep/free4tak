"""
mtn_morning_brief.py — MTN 아침 방송(전일장·미장 정리) 요약 (2026-11-03, 대장)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
대장: "매일 06시55분부터 07시35분정도까지 MTN에서 전일장·미장 정리를 하고 있거든 꽤 깔끔해..
      라이브로 하니까.. 우리 유튜브캐치를 그대로 쓰면 되."
youtube_live_monitor(07~20시, 60분 단위 종목 캐치)와 같은 방식 — 라이브 오디오를 yt-dlp로 받아
로컬 Whisper(CPU)로 글로 바꾼 뒤, 소넷이 핵심만 요약해서 리나 이름으로 보냄.
  06:54 크론 → MTN이 라이브면 07:36까지 녹음(아직이면 07:05까지 1분마다 다시 봄) → 전사 → 요약 → 발송
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
END_HHMM = "0736"           # 방송 끝(07:35쯤) + 1분 여유
WAIT_UNTIL = "0705"         # 이때까지 라이브 안 잡히면 그날은 쉼
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
    try:
        r = requests.get(f"https://www.youtube.com/@{handle}/live", headers=HEADERS, timeout=15)
        if '"isLiveNow":true' not in r.text:
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
        f"오늘은 {today}이야. 아래는 MTN 머니투데이방송 아침 라이브(06:55~07:35, 전일 국내장·간밤 미국장 정리)를 "
        "음성인식으로 받아적은 글이야. 음성인식이라 종목명·숫자가 틀리게 적혔을 수 있어.\n\n"
        f"[방송 받아쓰기]\n{text}\n\n"
        "[작성 지침]\n"
        "1. 한국어로, 디스코드 메시지 하나(1500자 이내)로 정리해.\n"
        "2. ★ 종목 추천이 아니라 '내용'이 중요해 — 시장이 왜 그렇게 움직였는지(원인·해석·자금 흐름)와 "
        "방송이 짚은 오늘의 관전 포인트를 담아. 종목명은 섹터 흐름을 설명하는 예시로만 짧게.\n"
        "3. 순서: 🇺🇸 간밤 미장(지수 + 왜 움직였나) → 🇰🇷 전일 국장(흐름·수급 + 왜) → 🧭 방송의 시각(오늘 장 해석·섹터 흐름) "
        "→ ⚠️ 오늘 일정·변수.\n"
        "4. 각 항목은 짧은 글머리표로. 방송에서 말한 숫자만 쓰고, 어색한 숫자·종목명은 빼거나 '(확인 필요)'를 붙여.\n"
        "5. 방송에 없는 내용은 절대 지어내지 마. 광고·시청자 안내 같은 잡담은 빼.\n"
        "6. 받아쓰기가 너무 짧거나 방송 내용이 아니면 '오늘 방송 내용을 제대로 못 받았어'라고만 해."
    )


def summarize(text: str, today: str) -> str:
    import anthropic
    from common_utils import extract_claude_text
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    res = client.messages.create(
        model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5"),
        max_tokens=6000,     # 소넷5 thinking 몫까지 넉넉히 — 11-03 3000에서 본문 잘림 실측
        messages=[{"role": "user", "content": build_prompt(text, today)}],
    )
    return extract_claude_text(res)


def _notify(msg: str):
    from notifier import Notifier
    Notifier(name="MTN아침", route="scan").send(msg)


def main():
    today = datetime.date.today().isoformat()
    vid = live_video_id()
    while not vid and datetime.datetime.now().strftime("%H%M") < WAIT_UNTIL:
        time.sleep(60)
        vid = live_video_id()
    if not vid:
        print(f"{today} MTN 라이브 없음 — 쉼")
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
    _notify(f"📺 [MTN 아침 정리 {today[5:]}]\n{summarize(text, today)}")


if __name__ == "__main__":
    main()
