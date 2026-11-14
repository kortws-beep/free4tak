"""
youtube_live_monitor.py — 유튜브 경제방송 "실시간 라이브" 추천종목 모니터링
================================================================
[하는 일]
MTN/매일경제TV가 라이브 방송 중일 때, 그 오디오를 끊김없이 이어서
캡처(청크 단위) → 로컬 Whisper로 텍스트화 → youtube_stock_monitor.py의
검증된 추출/저장 파이프라인 재사용. 대장 요청: "실시간 라이브를
모니터링해서 종목추천이 나오면 저장하는 방식" — 업로드된 VOD 기준
스캔(youtube_stock_monitor.py)은 애초에 원하던 방향이 아니었고, 마침
그쪽은 유튜브 자막API IP차단으로도 막혀있어서 이 라이브 방식으로 대체.

[동작 방식 — 채널별로 별도 스레드, 채널당]
1. `/live` 페이지로 라이브 여부 확인(isLiveNow).
2. 라이브면 yt-dlp+ffmpeg로 오디오만 CHUNK_SEC(기본 3600초=1시간) 단위로
   캡처. 캡처가 끝나면 곧바로 다음 구간 캡처 시작 — 라이브가 이어지는
   동안 끊김없이 순차 처리(정해진 시각에 스팟체크하는 게 아님).
3. faster-whisper(small, 로컬GPU)로 그 구간 오디오를 한국어 텍스트로.
4. youtube_stock_monitor의 extract_stock_picks/validate_stock_name/
   generate_comment/save_pick으로 종목 추출·검증·저장(공유 DB).
5. 라이브가 아니면 POLL_INTERVAL_SEC(기본 5분) 대기 후 재확인.

★ ffmpeg 시스템 패키지 필요(`sudo apt install ffmpeg`) — yt-dlp가 라이브
  HLS 스트림을 구간 제한 다운로드하는 데 사용.
★ Whisper는 CPU로 돌림 — GPU(cuda) 시도했더니 libcublas.so.12 없어서
  실패(ctranslate2가 요구하는 CUDA 라이브러리 별도설치 필요), CPU로
  테스트해보니 30초 오디오를 4.6초에 처리(실시간의 6.5배 속도)라 60분
  구간도 CPU로 9분 내외면 충분 — 오히려 로컬LLM(ollama)과 GPU를 아예
  안 나눠써도 돼서 VRAM 경합 이슈 자체가 사라짐(이 프로젝트 반복된
  GPU경합 교훈에 완전히 부합).
★ 구간 크기 60분은 대장 확인 — "바로 종목 살 게 아니고 후처리 과정을
  거치니까" 지연에 크게 민감하지 않음.

systemd 서비스로 상시 실행(다른 봇들과 동일 패턴) — cron 아님, 라이브
지속 시간 내내 청크를 이어서 처리해야 하는 상시 프로세스라서.
================================================================
"""
import os
import sys
import re
import gc
import json
import ctypes
import time
import shutil
import tempfile
import threading
import subprocess
import datetime

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

from youtube_stock_monitor import (
    CHANNELS, init_db, save_pick, validate_stock_name,
    extract_stock_picks, generate_comment, notify_report, _get_llm_client,
    DB_PATH, _ask,
)
import sqlite3

# ── 캡처 설정 ────────────────────────────────────────────────
CHUNK_SEC          = int(os.getenv("YT_LIVE_CHUNK_SEC", "3600"))   # 60분 (대장 확인)
POLL_INTERVAL_SEC  = 300   # 라이브 아닐 때 재확인 주기 (5분)
WHISPER_MODEL_SIZE = os.getenv("YT_WHISPER_MODEL", "small")        # 대장 확인 — 안전우선
AUDIO_FORMAT       = "233"  # yt-dlp 포맷ID: 오디오 전용 저비트레이트 (음성인식엔 충분)
YTDLP_BIN          = os.path.join(os.path.dirname(sys.executable), "yt-dlp")  # venv 안 실행파일 절대경로 사용(PATH 의존 X)
TRANSCRIPT_SUB_CHUNK = 4000  # 60분 전사(~2만자)를 이 크기로 쪼개서 전체를 다 훑음(num_ctx=8192 여유 감안)

# ★ 2026-09-18: 대장 요청 — "24시간 81건이네.. 넘 많다. 유튜브는 07시부터
# 20시까지만 하자." 새벽~아침 이른시간 방송은 캐치해봐야 의미 없다고 판단,
# 활성 시간대 밖에서는 캡처 자체를 안 함(가벼운 폴링만 하며 대기).
ACTIVE_HOUR_START = 7
ACTIVE_HOUR_END   = 20  # 07:00~19:59 활성, 20:00부터 다음날 07:00까지 휴식

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
}

_TMP_DIR = os.path.join(tempfile.gettempdir(), "youtube_live_monitor")

# ★ 2026-11-11 대장 — 구간별 전사문 보관(나중에 "그 코너가 몇 시였나" 찾기·재분석용). 하루 1MB 안팎, 60일 지나면 지움.
TRANSCRIPT_DIR  = os.path.join(_base, "logs", "youtube_live")
TRANSCRIPT_KEEP_DAYS = 60

# ★ 2026-11-11 대장 — 매일경제TV 오후(14시 전후, 1시간 안 됨) "텔레그램방에서 부각되는 종목" 코너(꼭지 3~4개).
#   시각이 정확하지 않아 시간 대신 내용으로 찾음: 전사문에 '텔레그램'이 나오면 그 근처를 AI에게 따로 물어봄.
#   목표가·손절가 게이트 없이 "텔레그램에서 부각된다고 소개된 종목"만 별도 표(telegram_buzz)에 저장 + 알림.
#   봇 매매(유튜브 슬롯 등)엔 연결 안 함 — 대장 수동 참고용.
TELEGRAM_RE = re.compile(r"텔레\s*그램|텔레방|텔방")
_tg_carry = {}   # ★ 11-12 코너가 60분 구간 끝에 걸치면(마지막 언급이 끝 8000자 안) 다음 구간 앞부분도 이어서 봄
os.makedirs(_TMP_DIR, exist_ok=True)

# ★ 2026-09-25: 프로세스가 오디오 캡처/전사 도중(주로 코드배포 재시작)에
#   죽으면 process_chunk()의 finally 블록이 못 돌아 .m4a/.m4a.part가
#   고아로 남는 문제 발견(대장 지적 — 174MB, 9일치 잔재). 이 디렉터리는
#   이 서비스 전용이라 프로세스 시작 시점에 남아있는 파일은 100% 전
#   실행분의 잔재(현재 실행 중인 캡처가 이 파일을 새로 만들었을 리 없음)
#   — 시작할 때마다 통째로 비움.
for _f in os.listdir(_TMP_DIR):
    try:
        os.remove(os.path.join(_TMP_DIR, _f))
    except OSError:
        pass

# 두 채널 스레드가 whisper 모델 인스턴스 하나를 공유 — 동시 추론 방지용 락
_whisper_lock = threading.Lock()


def is_channel_live(handle: str):
    """반환: (is_live: bool, video_id: str)"""
    try:
        resp = requests.get(f"https://www.youtube.com/@{handle}/live",
                             headers=HEADERS, timeout=15)
        if '"isLiveNow":true' in resp.text:
            m = re.search(r'"videoId":"([^"]+)"', resp.text)
            if m:
                return True, m.group(1)
    except Exception as e:
        print(f"⚠️ [라이브체크] {handle} 오류: {e}")
    # ★ 2026-11-04: 방송 중인데 페이지 확인이 07:00~07:25 내내 '라이브 아님'(세 채널 모두) — yt-dlp로 한 번 더 봄
    try:
        res = subprocess.run([YTDLP_BIN, "--skip-download", "--print", "%(id)s %(is_live)s",
                              f"https://www.youtube.com/@{handle}/live"],
                             capture_output=True, text=True, timeout=60)
        parts = res.stdout.split()
        if len(parts) >= 2 and parts[1] == "True":
            print(f"   🔁 [라이브체크] {handle} 페이지는 아님, yt-dlp로 라이브 확인 {parts[0]}")
            return True, parts[0]
    except Exception as e:
        print(f"⚠️ [라이브체크] {handle} yt-dlp 오류: {e}")
    return False, ""


SALVAGE_MIN_BYTES = 2_000_000   # ★ 11-12 약 2분 분량 — 이보다 작으면 버림


def _salvage_partial(out_path: str) -> bool:
    """★ 2026-11-12 — 라이브 HLS 조각 하나가 실패하면 yt-dlp/ffmpeg가 오류로 끝나 60분 구간이 통째로 버려졌음
    (하루 수십 번, 매경 텔레그램 코너 뒷부분 유실). 받은 데까지의 파일(또는 .part)이 있으면 그걸로 전사."""
    for cand in (out_path, out_path + ".part"):
        try:
            if os.path.exists(cand) and os.path.getsize(cand) >= SALVAGE_MIN_BYTES:
                if cand != out_path:
                    os.replace(cand, out_path)
                return True
        except OSError:
            pass
    for cand in (out_path, out_path + ".part"):
        try:
            os.remove(cand)
        except OSError:
            pass
    return False


def capture_audio_chunk(video_id: str, duration_sec: int, out_path: str) -> bool:
    """yt-dlp로 라이브 스트림 오디오를 duration_sec만큼만 캡처.
    ffmpeg 다운로더에 -t 옵션을 넘겨서 구간을 제한 — 라이브는 seek 개념이
    없어서 "지금 시점부터 N초"가 그대로 다음 구간의 시작점이 된다
    (호출 사이 처리시간만큼만 미세한 갭 발생, 60분 단위라 허용범위)."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    cmd = [
        YTDLP_BIN, "-f", AUDIO_FORMAT,
        "--downloader", "ffmpeg",
        "--downloader-args", f"ffmpeg_i:-t {duration_sec}",
        "--no-part",                       # ★ 11-12 끊겨도 받은 만큼 파일로 남게(.part 정리로 사라지지 않게)
        "-o", out_path,
        url,
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=duration_sec + 120)
        if res.returncode != 0 or not os.path.exists(out_path):
            if _salvage_partial(out_path):
                print(f"⚠️ [오디오캡처] 중간에 끊김 — 받은 부분({os.path.getsize(out_path) // 1_000_000}MB)은 살려서 전사: "
                      f"{res.stderr.strip().splitlines()[-1][-160:] if res.stderr.strip() else ''}")
                return True
            print(f"⚠️ [오디오캡처] 실패: {res.stderr[-500:]}")
            return False
        return True
    except subprocess.TimeoutExpired:
        if _salvage_partial(out_path):
            print("⚠️ [오디오캡처] 타임아웃 — 받은 부분은 살려서 전사")
            return True
        print("⚠️ [오디오캡처] 타임아웃")
        return False
    except Exception as e:
        print(f"⚠️ [오디오캡처] 오류: {e}")
        return False


# ★ 2026-10-07: 서비스가 3일 10시간 동안 재시작 없이 돌며 메모리 4.8GB까지 증가
#   (마스터 실측). 60분 오디오를 전사할 때마다 faster-whisper가 오디오 전체를
#   float 배열로 디코딩(약 230MB)하고 VAD·추론 버퍼를 잡는데, 여러 스레드가
#   번갈아 쓰는 장기 프로세스에선 glibc가 해제된 메모리를 OS에 돌려주지 않고
#   쌓아 두는(단편화) 경우가 흔함. 전사 직후 gc + malloc_trim으로 돌려주고,
#   전후 메모리를 로그로 남겨 실제로 어디서 늘어나는지 확인할 수 있게 함.
try:
    _libc = ctypes.CDLL("libc.so.6")
except OSError:
    _libc = None


def _rss_mb() -> int:
    """현재 프로세스 실사용 메모리(MB). 리눅스 외엔 0."""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    return 0


def _release_memory():
    gc.collect()
    if _libc is not None:
        try:
            _libc.malloc_trim(0)
        except Exception:
            pass


def transcribe_audio(model, path: str) -> str:
    before = _rss_mb()
    try:
        with _whisper_lock:
            segments, _info = model.transcribe(path, language="ko", vad_filter=True)
            text = " ".join(seg.text for seg in segments)
            del segments, _info
            return text
    except Exception as e:
        print(f"⚠️ [Whisper] 전사 오류: {e}")
        return ""
    finally:
        peak = _rss_mb()
        _release_memory()
        print(f"   🧠 [메모리] 전사 전 {before}MB → 직후 {peak}MB → 정리 후 {_rss_mb()}MB")


def _save_transcript(handle: str, start: datetime.datetime, text: str):
    try:
        d = os.path.join(TRANSCRIPT_DIR, start.strftime("%Y-%m-%d"))
        os.makedirs(d, exist_ok=True)
        end = datetime.datetime.now()
        with open(os.path.join(d, f"{handle}_{start:%H%M}-{end:%H%M}.txt"), "w", encoding="utf-8") as f:
            f.write(text)
        cutoff = (datetime.date.today() - datetime.timedelta(days=TRANSCRIPT_KEEP_DAYS)).isoformat()
        for old in os.listdir(TRANSCRIPT_DIR):
            if old < cutoff:
                shutil.rmtree(os.path.join(TRANSCRIPT_DIR, old), ignore_errors=True)
    except OSError as e:
        print(f"   ⚠️ 전사문 저장 오류: {e}")


def _init_telegram_buzz():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("""CREATE TABLE IF NOT EXISTS telegram_buzz (
        id INTEGER PRIMARY KEY AUTOINCREMENT, pick_date TEXT NOT NULL, stock_name TEXT NOT NULL,
        channel TEXT DEFAULT '', video_id TEXT DEFAULT '', reason TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now','localtime')), UNIQUE(pick_date, stock_name, channel))""")
    conn.commit()
    conn.close()


def extract_telegram_buzz(text: str, llm) -> list:
    """전사문에서 '텔레그램방·채널에서 부각되는 종목'으로 소개된 것만 [(이름, 이유)]."""
    hits = [m.start() for m in TELEGRAM_RE.finditer(text)]
    if not hits or llm is None:
        return []
    # 코너 첫 언급 앞 500자 ~ 마지막 언급 뒤 6000자(종목 소개가 뒤따름), 너무 길면 12000자까지
    seg = text[max(0, hits[0] - 500): min(len(text), hits[-1] + 6000)][:12000]
    prompt = f"""아래는 경제 방송 자막(음성인식이라 오타가 있을 수 있음)이야.
이 중에 "텔레그램(방·채널·자료)"을 근거로 종목을 소개하는 코너가 있으면 — 예: "텔레그램 자료로 같이 들여다보겠습니다",
텔레그램에서 화제·부각되는 종목, 텔레그램에 올라온 리포트·기사 소개 — 그 코너가 다룬 상장 종목을 골라줘. 규칙:
- 코너는 보통 꼭지 3~4개(첫 번째·두 번째·세 번째…)로 이어짐 — 꼭지마다 빠짐없이 훑어서, 소개·탑픽·수혜·주목으로 나온 상장 종목을 전부 넣어.
- 코너 밖에서 일반 시황으로 언급된 종목은 빼.
- 각 종목마다 방송이 말한 소개 이유(재료)를 20자 안으로.
- JSON 배열로만 답해: [{{"name": "종목명", "why": "이유"}}]. 해당 코너가 없으면 [].

자막:
{seg}"""
    try:
        out = _ask(llm, prompt, 4000, effort="medium")   # 꼭지 여러 개를 빠짐없이 — low에선 1~6개로 들쭉날쭉(11-12)
        m = re.search(r"\[.*\]", out, re.S)
        items = json.loads(m.group(0)) if m else []
        return [(str(i.get("name", "")).strip(), str(i.get("why", "")).strip()[:40])
                for i in items if isinstance(i, dict) and i.get("name")]
    except Exception as e:
        print(f"   ⚠️ 텔레그램 코너 추출 오류: {e}")
        return []


def _handle_telegram_buzz(today: str, text: str, channel_label: str, video_id: str, llm, handle: str = ""):
    hits = [m.start() for m in TELEGRAM_RE.finditer(text)]
    carry = _tg_carry.pop(handle, False)
    if hits and len(text) - hits[-1] < 8000:
        _tg_carry[handle] = True
    if carry and not (hits and hits[0] < 8000):
        text = "[앞 구간에서 이어지는 텔레그램 자료 코너] " + text[:8000] + " … " + text[8000:]
    found = extract_telegram_buzz(text, llm)
    if not found:
        return
    saved = []
    conn = sqlite3.connect(DB_PATH, timeout=10)
    for raw, why in found:
        name = validate_stock_name(raw)
        if not name:
            print(f"   ⏭️ [텔레그램] 검증 실패: {raw}")
            continue
        cur = conn.execute("INSERT OR IGNORE INTO telegram_buzz (pick_date, stock_name, channel, video_id, reason) "
                           "VALUES (?,?,?,?,?)", (today, name, channel_label, video_id, why))
        if cur.rowcount:
            saved.append((name, why))
            print(f"   📨 [텔레그램 부각] {name} — {why} ({channel_label})")
    conn.commit()
    conn.close()
    if saved:
        try:
            from notifier import Notifier
            Notifier(name="유튜브스카우트", route="scan").send(
                f"📨 [{channel_label}] 텔레그램방 부각 종목 ({len(saved)}건, 매매 연결 안 함)\n"
                + "\n".join(f"· {n} — {w}" for n, w in saved))
        except Exception as e:
            print(f"⚠️ 알림 전송 오류: {e}")


def process_chunk(handle: str, channel_label: str, llm, model):
    live, video_id = is_channel_live(handle)
    if not live:
        return False

    today = datetime.date.today().strftime("%Y-%m-%d")
    audio_path = os.path.join(_TMP_DIR, f"{handle}_{int(time.time())}.m4a")

    started = datetime.datetime.now()
    print(f"🔴 [{channel_label}] 라이브 감지 — {CHUNK_SEC//60}분 구간 캡처 시작 ({video_id})")
    ok = capture_audio_chunk(video_id, CHUNK_SEC, audio_path)
    if not ok:
        return True  # 라이브였으니 True(다음 구간 바로 재시도) — 실패는 별개

    try:
        text = transcribe_audio(model, audio_path)
    finally:
        try:
            os.remove(audio_path)
        except OSError:
            pass

    if not text.strip():
        print(f"   ⚠️ [{channel_label}] 전사 결과 없음 — 스킵")
        return True

    print(f"   📝 [{channel_label}] 전사 {len(text)}자")
    _save_transcript(handle, started, text)
    _handle_telegram_buzz(today, text, channel_label, video_id, llm, handle)
    # ★ 2026-09-16: 60분 구간 전사가 보통 2만자 안팎이라 한 번에
    #   text[:6000]으로 잘라 넣으면 앞쪽 16분 정도만 분석되고 나머지
    #   84%가 통째로 무시되던 버그를 실측(첫 라이브 사이클)으로 발견.
    #   TRANSCRIPT_SUB_CHUNK 단위로 쪼개서 전체를 다 훑도록 수정.
    saved = []
    for i in range(0, len(text), TRANSCRIPT_SUB_CHUNK):
        sub = text[i:i + TRANSCRIPT_SUB_CHUNK]
        names = extract_stock_picks(f"{channel_label} 라이브", sub, llm)
        for raw_name in names:
            valid_name = validate_stock_name(raw_name)
            if not valid_name:
                print(f"   ⏭️ 검증 실패(할루시네이션 추정): {raw_name}")
                continue
            # ★ 2026-09-16: 대장 요청 — "언급내용중 목표가/손절가만 있는
            #   종목으로 한정하자". comment(generate_comment)가 이제
            #   목표가/손절가 전용이라, 비어있으면(=가격 언급 없음)
            #   저장 자체를 스킵 — 일반 "추천합니다" 수준은 더 이상 저장 안 함.
            comment = generate_comment(valid_name, sub, llm)
            if not comment:
                print(f"   ⏭️ 목표가/손절가 없음 — 저장 스킵: {valid_name}")
                continue
            if save_pick(today, valid_name, channel_label, video_id, f"{channel_label} 라이브", comment):
                saved.append((today, valid_name, channel_label))
                print(f"   💾 {today} | {valid_name} ({channel_label}) — {comment}")

    if saved:
        notify_report(saved)
    return True


_TRADING_DAY = {"date": "", "open": True}


def _is_trading_day() -> bool:
    """★ 11-14 대장 "주말엔 재방송·재탕이라 필요 없어" — 토·일·휴장일엔 녹음 안 함(전엔 시간대만 막혀 있어서
    주말에도 3채널을 하루 종일 받아 회선을 잡아먹고 원격 접속이 끊겼음). 휴장일은 한투 API로 하루 한 번 확인,
    API 실패(None)면 캐시하지 않고 열린 날로 보고 다음에 다시 확인(lina_bot._is_trading_day와 같은 방식)."""
    now = datetime.datetime.now()
    if now.weekday() >= 5:
        return False
    today = now.date().isoformat()
    if _TRADING_DAY["date"] != today:
        try:
            from kis_api import KisAPI
            is_open = KisAPI().is_market_open()
        except Exception as e:
            print(f"⚠️ [유튜브] 휴장일 확인 오류: {e}")
            is_open = None
        if is_open is None:
            return True
        _TRADING_DAY.update(date=today, open=bool(is_open))
    return _TRADING_DAY["open"]


def _within_active_hours() -> bool:
    return ACTIVE_HOUR_START <= datetime.datetime.now().hour < ACTIVE_HOUR_END and _is_trading_day()


def channel_worker(handle: str, channel_label: str, llm, model):
    print(f"👀 [{channel_label}] 라이브 모니터링 스레드 시작")
    was_active = True
    while True:
        try:
            if not _within_active_hours():
                if was_active:
                    print(f"😴 [{channel_label}] 활성시간(거래일 {ACTIVE_HOUR_START}~{ACTIVE_HOUR_END}시) 아님 — 대기")
                    was_active = False
                time.sleep(POLL_INTERVAL_SEC)
                continue
            was_active = True
            was_live = process_chunk(handle, channel_label, llm, model)
        except Exception as e:
            print(f"⚠️ [{channel_label}] 워커 오류: {e}")
            was_live = False
        if not was_live:
            time.sleep(POLL_INTERVAL_SEC)
        # 라이브였으면 대기 없이 바로 다음 구간 캡처(끊김없이 이어서 처리)


def main():
    if shutil.which("ffmpeg") is None:
        print("❌ ffmpeg가 설치되어 있지 않습니다. `sudo apt install -y ffmpeg` 실행 후 재시작하세요.")
        sys.exit(1)

    init_db()
    _init_telegram_buzz()
    llm = _get_llm_client()
    if llm is None:
        print("⚠️ anthropic 패키지·ANTHROPIC_API_KEY 없음 — AI 호출 불가, 종료")
        sys.exit(1)

    # ★ 2026-11-09: GPU 있으면 GPU(cuda float16), 실패하면 CPU int8 — core/whisper_loader.py
    from whisper_loader import load_whisper
    print(f"🤖 Whisper({WHISPER_MODEL_SIZE}) 모델 로딩 중...")
    model, dev = load_whisper(WHISPER_MODEL_SIZE)
    print(f"✅ Whisper 모델 로드 완료 — {dev}")

    threads = []
    for handle, label in CHANNELS.items():
        t = threading.Thread(target=channel_worker, args=(handle, label, llm, model), daemon=True)
        t.start()
        threads.append(t)

    for t in threads:
        t.join()


if __name__ == "__main__":
    main()
