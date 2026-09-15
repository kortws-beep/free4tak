"""
youtube_stock_monitor.py — 유튜브 경제방송 추천종목 모니터링
================================================================
[하는 일]
MTN(머니투데이방송)/매일경제TV 유튜브 채널을 모니터링해서 새 영상이
올라오면 자막(자동생성 한글 캡션)을 가져와 로컬 AI(놀고있는 GPU 활용)로
"추천/주목 종목"을 추출, (날짜, 종목명)만 DB에 저장한다.

[동작 방식]
1. 채널의 /videos 탭 HTML에 박혀있는 ytInitialData JSON을 파싱해 최신
   영상 목록을 가져온다 (유튜브 공식 RSS(feeds/videos.xml)는 2026-09
   기준 완전히 404 — 이 프로젝트에서 새로 확인된 사실, API 키 없이
   쓰려면 페이지 스크래핑이 유일한 방법).
2. 채널별로 마지막으로 처리한 video_id를 상태파일에 저장해두고, 그
   이후에 올라온 영상만 신규 처리 (최초 실행 시엔 과거 전체를 백필하지
   않도록 최근 N개로 캡).
3. youtube-transcript-api로 한국어 자동자막 텍스트를 가져온다.
4. 로컬 ollama(llama3.1:8b, ~5GB — 카드가 8GB라 여유있게 안전권 모델
   선택, 하루 1회/영상당 잠깐만 뜨므로 GPU 상시 점유 문제는 없음)로
   "실제로 추천/주목한 개별 종목명"만 뽑아 JSON 배열로 받는다.
5. AI가 뽑은 이름은 kr_theme_finance.db 실제 상장 종목명과 대조해서
   존재하지 않는(할루시네이션) 이름은 걸러낸다.
6. (날짜, 종목명, 채널, 영상제목, video_id) 로 저장 — 요청대로 실사용
   조회 대상은 날짜+종목명 두 가지뿐이고 나머지는 중복방지/추적용.

크론: 하루 1회 (대장 요청 "24시간.. 하하하")
================================================================
"""
import os
import sys
import re
import json
import sqlite3
import datetime
import requests

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

try:
    import openai as _openai
except ImportError:
    _openai = None

from youtube_transcript_api import YouTubeTranscriptApi

# ── 경로 ──────────────────────────────────────────────────────
_BASE       = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH     = os.path.join(_BASE, "intelligence", "youtube_picks.db")
STATE_PATH  = os.path.join(_BASE, "intelligence", "youtube_monitor_state.json")
THEME_DB    = os.path.join(_BASE, "lina_bot", "kr_theme_finance.db")

# ── 모니터링 대상 채널 (핸들 기준) ─────────────────────────────
CHANNELS = {
    "mtn":          "MTN 머니투데이방송",
    "MKeconomy_TV": "매일경제TV",
}

# 채널당 한 번에 처리할 최대 영상 수 (전체 백필 방지).
# ★ 두 채널 다 하루에도 수십 건씩 올라오는 편이라(체크 주기가 24시간이라
#   더더욱) 너무 낮게 잡으면 하루치를 다 못 따라잡음 — 넉넉하게 잡음.
MAX_BACKLOG_PER_CHANNEL = 40

# ── 로컬 AI 설정 (8GB GPU에서 안전한 크기 우선) ─────────────────
OLLAMA_URL   = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("YT_OLLAMA_MODEL", "llama3.1:8b")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
}


# ============================================================
# DB
# ============================================================
def init_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS youtube_picks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            pick_date   TEXT NOT NULL,
            stock_name  TEXT NOT NULL,
            channel     TEXT DEFAULT '',
            video_id    TEXT DEFAULT '',
            video_title TEXT DEFAULT '',
            created_at  TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(video_id, stock_name)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_yp_date ON youtube_picks(pick_date)")
    conn.commit()
    conn.close()


def save_pick(pick_date: str, stock_name: str, channel: str,
              video_id: str, video_title: str) -> bool:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    cur = conn.execute("""
        INSERT OR IGNORE INTO youtube_picks
            (pick_date, stock_name, channel, video_id, video_title)
        VALUES (?, ?, ?, ?, ?)
    """, (pick_date, stock_name, channel, video_id, video_title))
    conn.commit()
    saved = cur.rowcount > 0
    conn.close()
    return saved


def get_recent_picks(days: int = 1) -> list:
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.execute("PRAGMA query_only = ON")
    rows = conn.execute("""
        SELECT pick_date, stock_name, channel, video_title
        FROM youtube_picks
        WHERE pick_date >= date('now', 'localtime', ? || ' days')
        ORDER BY pick_date DESC
    """, (f"-{days-1}",)).fetchall()
    conn.close()
    return [{"date": r[0], "name": r[1], "channel": r[2], "title": r[3]} for r in rows]


# ============================================================
# 상태 (채널별 마지막 처리 video_id)
# ============================================================
def _load_state() -> dict:
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_state(state: dict):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ============================================================
# 채널 영상 목록 스크래핑
# ============================================================
def fetch_channel_videos(handle: str) -> list:
    """
    채널 /videos 탭에서 최신 영상 목록을 최신순으로 반환.
    반환: [{"video_id":..., "title":..., "time_text":...}, ...]
    """
    try:
        resp = requests.get(f"https://www.youtube.com/@{handle}/videos",
                             headers=HEADERS, timeout=15)
        m = re.search(r'var ytInitialData = (\{.*?\});</script>', resp.text, re.S)
        if not m:
            print(f"⚠️ [유튜브] {handle} ytInitialData 파싱 실패")
            return []
        data = json.loads(m.group(1))
    except Exception as e:
        print(f"⚠️ [유튜브] {handle} 채널 조회 오류: {e}")
        return []

    results = []

    def walk(node):
        if isinstance(node, dict):
            lvm = node.get("lockupViewModel")
            if isinstance(lvm, dict) and lvm.get("contentType") == "LOCKUP_CONTENT_TYPE_VIDEO":
                vid = lvm.get("contentId")
                meta = lvm.get("metadata", {}).get("lockupMetadataViewModel", {})
                title = meta.get("title", {}).get("content", "")
                rows = meta.get("metadata", {}).get("contentMetadataViewModel", {}).get("metadataRows", [])
                time_text = ""
                if rows:
                    parts = rows[0].get("metadataParts", [])
                    if len(parts) > 1:
                        time_text = parts[1].get("text", {}).get("content", "")
                if vid:
                    results.append({"video_id": vid, "title": title, "time_text": time_text})
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return results


def parse_relative_time(text: str) -> str:
    """"N분 전"/"N시간 전"/"N일 전" 등 → YYYY-MM-DD (KST 기준, 현재 시각 근사)."""
    now = datetime.datetime.now()
    m = re.match(r"(\d+)\s*(분|시간|일|주|개월|년)\s*전", text or "")
    if not m:
        return now.strftime("%Y-%m-%d")
    n, unit = int(m.group(1)), m.group(2)
    delta = {
        "분": datetime.timedelta(minutes=n),
        "시간": datetime.timedelta(hours=n),
        "일": datetime.timedelta(days=n),
        "주": datetime.timedelta(weeks=n),
        "개월": datetime.timedelta(days=n * 30),
        "년": datetime.timedelta(days=n * 365),
    }.get(unit, datetime.timedelta(0))
    return (now - delta).strftime("%Y-%m-%d")


def get_new_videos(handle: str, state: dict) -> tuple:
    """
    신규 영상만 오래된순으로 반환 + 갱신할 last_video_id.
    반환: ([{video_id,title,time_text}, ...], new_last_video_id)
    """
    videos = fetch_channel_videos(handle)
    if not videos:
        return [], state.get(handle, {}).get("last_video_id")

    last_id = state.get(handle, {}).get("last_video_id")
    if last_id:
        idx = next((i for i, v in enumerate(videos) if v["video_id"] == last_id), None)
        if idx is not None:
            new_videos = videos[:idx]
        else:
            # 너무 오래 안 돌아서 목록에 없음 — 백로그 캡
            new_videos = videos[:MAX_BACKLOG_PER_CHANNEL]
    else:
        # 최초 실행 — 과거 전체 백필 방지, 최근 것만
        new_videos = videos[:MAX_BACKLOG_PER_CHANNEL]

    new_videos.reverse()  # 오래된 것부터 처리
    new_last_id = videos[0]["video_id"]
    return new_videos, new_last_id


# ============================================================
# 자막 추출
# ============================================================
def fetch_transcript(video_id: str, max_chars: int = 6000) -> str:
    try:
        api = YouTubeTranscriptApi()
        result = api.fetch(video_id, languages=["ko"])
        text = " ".join(s.text for s in result.snippets)
        return text[:max_chars]
    except Exception as e:
        print(f"   ⚠️ 자막 조회 실패 ({video_id}): {e}")
        return ""


# ============================================================
# 로컬 AI 추출 + 실종목 검증
# ============================================================
def _get_llm_client():
    if not _openai:
        return None
    return _openai.OpenAI(base_url=f"{OLLAMA_URL}/v1", api_key="ollama")


def extract_stock_picks(title: str, transcript: str, llm) -> list:
    if not transcript or llm is None:
        return []

    prompt = f"""아래는 경제 유튜브 영상의 자막 텍스트입니다.
제목: {title}

자막:
{transcript}

위 내용에서 출연자가 명확하게 "추천" 또는 "주목/관심 있게 볼 종목"으로
언급한 개별 상장 종목명만 뽑아줘. 규칙:
- 지수(코스피/코스닥), 업종/섹터 이름, 해외지수는 제외하고 개별 종목명만.
- 확실하지 않으면 포함하지 마.
- 결과는 다른 설명 없이 JSON 배열로만 응답. 예: ["삼성전자", "SK하이닉스"]
- 추천 종목이 없으면 빈 배열 []."""

    try:
        res = llm.chat.completions.create(
            model=OLLAMA_MODEL,
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        text = res.choices[0].message.content.strip()
        m = re.search(r"\[.*\]", text, re.S)
        if not m:
            return []
        names = json.loads(m.group(0))
        return [n.strip() for n in names if isinstance(n, str) and n.strip()]
    except Exception as e:
        print(f"   ⚠️ 로컬AI 추출 오류: {e}")
        return []


def validate_stock_name(name: str) -> str:
    """kr_theme_finance.db 대조 — 실제 상장종목명이면 정규화된 이름, 아니면 빈문자열.
    로컬 AI(소형모델)가 합성어 중간에 공백을 끼워넣는 경우가 잦아서
    (예: "삼성 전기" → "삼성전기") 공백 제거본으로도 한 번 더 대조한다."""
    name = re.sub(r"^\(?주\)?\s*", "", name).strip()
    if not name:
        return ""
    candidates = [name]
    stripped = name.replace(" ", "")
    if stripped != name:
        candidates.append(stripped)

    try:
        conn = sqlite3.connect(THEME_DB, timeout=5)
        conn.execute("PRAGMA query_only = ON")
        for cand in candidates:
            row = conn.execute("""
                SELECT DISTINCT stock_name FROM kr_theme_stocks
                WHERE stock_name LIKE ? OR stock_name LIKE ?
                LIMIT 1
            """, (f"{cand}KOSPI %", f"{cand}KOSDAQ %")).fetchone()
            if row:
                conn.close()
                return cand
        conn.close()
    except Exception as e:
        print(f"   ⚠️ 종목명 검증 오류: {e}")
    return ""


# ============================================================
# 메인
# ============================================================
def main():
    init_db()
    state = _load_state()
    llm = _get_llm_client()
    if llm is None:
        print("⚠️ [유튜브] openai 패키지 없음 — 로컬 AI 호출 불가, 종료")
        return

    total_saved = []

    for handle, channel_label in CHANNELS.items():
        print(f"\n📺 [유튜브] {channel_label}(@{handle}) 확인 중...")
        new_videos, new_last_id = get_new_videos(handle, state)

        if not new_videos:
            print(f"   신규 영상 없음")
            continue

        print(f"   신규 영상 {len(new_videos)}건 처리")
        for v in new_videos:
            vid, title = v["video_id"], v["title"]
            pick_date = parse_relative_time(v["time_text"])
            transcript = fetch_transcript(vid)
            if not transcript:
                state.setdefault(handle, {})["last_video_id"] = vid
                _save_state(state)
                continue

            picks = extract_stock_picks(title, transcript, llm)
            for raw_name in picks:
                valid_name = validate_stock_name(raw_name)
                if not valid_name:
                    print(f"   ⏭️ 검증 실패(할루시네이션 추정): {raw_name}")
                    continue
                if save_pick(pick_date, valid_name, channel_label, vid, title):
                    total_saved.append((pick_date, valid_name, channel_label))
                    print(f"   💾 {pick_date} | {valid_name} ({channel_label})")

            # 영상 단위로 상태 저장 — 중간에 죽어도 재처리 안 되게
            state.setdefault(handle, {})["last_video_id"] = vid
            _save_state(state)

        state.setdefault(handle, {})["last_video_id"] = new_last_id
        _save_state(state)

    if total_saved:
        try:
            from notifier import Notifier
            lines = [f"- {d} {n} ({c})" for d, n, c in total_saved]
            Notifier(name="유튜브스카우트").send(
                f"[유튜브] 새 추천종목 {len(total_saved)}건\n" + "\n".join(lines)
            )
        except Exception as e:
            print(f"⚠️ 알림 전송 오류: {e}")

    print(f"\n✅ [유튜브] 완료 — 신규 저장 {len(total_saved)}건")


if __name__ == "__main__":
    main()
