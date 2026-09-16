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
import json
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
)

# ── 캡처 설정 ────────────────────────────────────────────────
CHUNK_SEC          = int(os.getenv("YT_LIVE_CHUNK_SEC", "3600"))   # 60분 (대장 확인)
POLL_INTERVAL_SEC  = 300   # 라이브 아닐 때 재확인 주기 (5분)
WHISPER_MODEL_SIZE = os.getenv("YT_WHISPER_MODEL", "small")        # 대장 확인 — 안전우선
AUDIO_FORMAT       = "233"  # yt-dlp 포맷ID: 오디오 전용 저비트레이트 (음성인식엔 충분)
YTDLP_BIN          = os.path.join(os.path.dirname(sys.executable), "yt-dlp")  # venv 안 실행파일 절대경로 사용(PATH 의존 X)
TRANSCRIPT_SUB_CHUNK = 4000  # 60분 전사(~2만자)를 이 크기로 쪼개서 전체를 다 훑음(num_ctx=8192 여유 감안)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
}

_TMP_DIR = os.path.join(tempfile.gettempdir(), "youtube_live_monitor")
os.makedirs(_TMP_DIR, exist_ok=True)

# 두 채널 스레드가 whisper 모델 인스턴스 하나를 공유 — 동시 추론 방지용 락
_whisper_lock = threading.Lock()


def is_channel_live(handle: str):
    """반환: (is_live: bool, video_id: str)"""
    try:
        resp = requests.get(f"https://www.youtube.com/@{handle}/live",
                             headers=HEADERS, timeout=15)
        if '"isLiveNow":true' not in resp.text:
            return False, ""
        m = re.search(r'"videoId":"([^"]+)"', resp.text)
        return (True, m.group(1)) if m else (False, "")
    except Exception as e:
        print(f"⚠️ [라이브체크] {handle} 오류: {e}")
        return False, ""


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
        "-o", out_path,
        url,
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=duration_sec + 120)
        if res.returncode != 0 or not os.path.exists(out_path):
            print(f"⚠️ [오디오캡처] 실패: {res.stderr[-500:]}")
            return False
        return True
    except subprocess.TimeoutExpired:
        print("⚠️ [오디오캡처] 타임아웃")
        return False
    except Exception as e:
        print(f"⚠️ [오디오캡처] 오류: {e}")
        return False


def transcribe_audio(model, path: str) -> str:
    try:
        with _whisper_lock:
            segments, _info = model.transcribe(path, language="ko", vad_filter=True)
            return " ".join(seg.text for seg in segments)
    except Exception as e:
        print(f"⚠️ [Whisper] 전사 오류: {e}")
        return ""


def process_chunk(handle: str, channel_label: str, llm, model):
    live, video_id = is_channel_live(handle)
    if not live:
        return False

    today = datetime.date.today().strftime("%Y-%m-%d")
    audio_path = os.path.join(_TMP_DIR, f"{handle}_{int(time.time())}.m4a")

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
    # ★ 2026-09-16: 60분 구간 전사가 보통 2만자 안팎이라 한 번에
    #   text[:6000]으로 잘라 넣으면 앞쪽 16분 정도만 분석되고 나머지
    #   84%가 통째로 무시되던 버그를 실측(첫 라이브 사이클)으로 발견.
    #   TRANSCRIPT_SUB_CHUNK 단위로 쪼개서 전체를 다 훑도록 수정 —
    #   MAX_PICKS_PER_SEGMENT 필터도 조각마다 독립 적용되니 테마나열
    #   차단 효과는 그대로 유지됨.
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


def channel_worker(handle: str, channel_label: str, llm, model):
    print(f"👀 [{channel_label}] 라이브 모니터링 스레드 시작")
    while True:
        try:
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
    llm = _get_llm_client()
    if llm is None:
        print("⚠️ openai 패키지 없음 — 로컬 AI 호출 불가, 종료")
        sys.exit(1)

    from faster_whisper import WhisperModel
    print(f"🤖 Whisper({WHISPER_MODEL_SIZE}) 모델 로딩 중... (CPU)")
    model = WhisperModel(WHISPER_MODEL_SIZE, device="cpu", compute_type="int8")
    print("✅ Whisper 모델 로드 완료")

    threads = []
    for handle, label in CHANNELS.items():
        t = threading.Thread(target=channel_worker, args=(handle, label, llm, model), daemon=True)
        t.start()
        threads.append(t)

    for t in threads:
        t.join()


if __name__ == "__main__":
    main()
