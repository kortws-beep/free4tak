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
# ★ 2026-09-15: 체크 주기 24시간→하루 3회(06/10/14시)로 단축돼서 한 번에
#   따라잡아야 할 분량이 줄었지만, 최초 실행/장기 다운타임 복구용으로
#   여유는 남겨둠.
MAX_BACKLOG_PER_CHANNEL = 20

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
            evaluation  TEXT DEFAULT '',
            created_at  TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(video_id, stock_name)
        )
    """)
    # ★ 2026-09-15: 기존 DB에는 evaluation 컬럼이 없어서 마이그레이션 필요
    cols = [r[1] for r in conn.execute("PRAGMA table_info(youtube_picks)").fetchall()]
    if "evaluation" not in cols:
        conn.execute("ALTER TABLE youtube_picks ADD COLUMN evaluation TEXT DEFAULT ''")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_yp_date ON youtube_picks(pick_date)")
    conn.commit()
    conn.close()


def save_pick(pick_date: str, stock_name: str, channel: str,
              video_id: str, video_title: str, evaluation: str = "") -> bool:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    cur = conn.execute("""
        INSERT OR IGNORE INTO youtube_picks
            (pick_date, stock_name, channel, video_id, video_title, evaluation)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (pick_date, stock_name, channel, video_id, video_title, evaluation))
    conn.commit()
    saved = cur.rowcount > 0
    conn.close()
    return saved


def get_recent_picks(days: int = 1) -> list:
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.execute("PRAGMA query_only = ON")
    rows = conn.execute("""
        SELECT pick_date, stock_name, channel, video_title, evaluation
        FROM youtube_picks
        WHERE pick_date >= date('now', 'localtime', ? || ' days')
        ORDER BY pick_date DESC
    """, (f"-{days-1}",)).fetchall()
    conn.close()
    return [{"date": r[0], "name": r[1], "channel": r[2], "title": r[3], "evaluation": r[4]} for r in rows]


def get_mention_dates(stock_name: str, days: int = 14) -> list:
    """최근 N일(오늘 포함) 동안 해당 종목이 언급된 날짜 목록(중복 포함, 발생순)."""
    conn = sqlite3.connect(DB_PATH, timeout=5)
    conn.execute("PRAGMA query_only = ON")
    rows = conn.execute("""
        SELECT pick_date FROM youtube_picks
        WHERE stock_name = ? AND pick_date >= date('now', 'localtime', ? || ' days')
        ORDER BY pick_date
    """, (stock_name, f"-{days-1}")).fetchall()
    conn.close()
    return [r[0] for r in rows]


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
    """추천 종목명만 뽑는다 (JSON 문자열 배열). 근거요약(comment)은 별도
    함수(generate_comment)로 분리 — name+comment를 한 번에 JSON 객체로
    요청하면 llama3.1:8b가 형식을 자주 못 지켜서(JSON 자체를 안 씀)
    실제 저장돼야 할 종목명까지 통째로 날아가는 걸 실측으로 확인.
    종목명만 뽑는 단순한 형태가 검증된 형태라 그대로 유지."""
    if not transcript or llm is None:
        return []

    prompt = f"""아래는 경제 유튜브 영상의 자막 텍스트입니다.
제목: {title}

자막:
{transcript}

"오늘의 추천종목", "탑픽", "일발장전" 같은 공식 추천/픽 코너에서
출연자가 명시적으로 지목한 종목명만 뽑아줘. 아주 엄격하게 판단할 것:
- 그냥 테마/섹터를 설명하다가 예시로 여러 종목을 나열한 경우(예: "이
  테마 관련 종목들은 A, B, C, D..." 식의 단순 소개)는 절대 포함하지
  마 — 이런 나열은 추천이 아니라 정보전달일 뿐임.
- "추천합니다", "탑픽입니다", "오늘의 픽" 처럼 명시적으로 추천/픽으로
  못박은 경우만 포함.
- 한 세그먼트에서 진짜 추천되는 종목은 보통 1~3개 이내임. 4개 넘게
  나온다면 그건 십중팔구 테마 나열이지 진짜 추천이 아니니 의심하고
  더 엄격히 재검토.
- 지수(코스피/코스닥), 업종/섹터 이름, 해외지수는 제외.
- 확실하지 않으면 포함하지 마.
- 결과는 다른 설명 없이 JSON 배열로만 응답. 예: ["삼성전자"]
- 추천 종목이 없으면 빈 배열 []."""

    # ★ 2026-09-16: 소형 로컬모델이 가끔 JSON을 깨뜨려서 파싱 실패 —
    #   진짜 추천을 놓치는 게 더 아까워서 실패시 1회 재시도.
    for attempt in range(2):
        try:
            res = llm.chat.completions.create(
                model=OLLAMA_MODEL,
                max_tokens=500,
                extra_body={"options": {"num_ctx": 8192}},
                messages=[{"role": "user", "content": prompt}],
            )
            text = res.choices[0].message.content.strip()
            m = re.search(r"\[.*\]", text, re.S)
            if not m:
                continue
            names = json.loads(m.group(0))
            names = [n.strip() for n in names if isinstance(n, str) and n.strip()]
            # ★ 2026-09-16 도입, 2026-09-17 제거: "3개 초과면 테마나열로
            #   간주해 통째로 버림" 규칙이, 여러 전문가가 연달아 각자
            #   종목을 추천하는 구간(4000자 안에 여러 실명이 들어감)까지
            #   같이 날려버리는 걸 실측으로 확인(대장이 매일경제TV를 직접
            #   보면서 "9개 정도 나오는데 우리는 놓친다"고 지적, 로그에서
            #   실제 종목명(한전기술/RF-HIC/한선엔지니어링 등)이 4개
            #   추출됐다는 이유만으로 통째 폐기된 사례 확인). 이제
            #   generate_comment()의 목표가/손절가 게이트가 훨씬 강한
            #   2차 필터라 진짜 테마나열(가격 없음)은 거기서 걸러지므로
            #   이 cap은 불필요 + 유해 판정, 제거.
            return names
        except Exception as e:
            print(f"   ⚠️ 로컬AI 추출 오류(시도 {attempt+1}/2): {e}")
    return []


def generate_comment(stock_name: str, transcript: str, llm) -> str:
    """검증 통과한 종목에 한해서만 호출 — 자막에서 이 종목의 목표가/손절가가
    구체적 숫자로 언급됐는지만 찾는다. ★ 2026-09-16: 대장 요청 —
    "언급내용중 목표가/손절가만 있는 종목으로 한정하자". 일반적인
    "추천합니다" 수준 언급은 더 이상 충분하지 않고, 구체적 가격이 나온
    경우만 진짜 픽으로 취급. 호출부(process_chunk/main)에서 이 반환값이
    비어있으면 아예 저장하지 않는 게이트로 씀 — 독자적 투자판단이 아니라
    "말한 내용에 숫자가 있었는지"만 보는 거라 할루시네이션 리스크도 낮음."""
    if llm is None:
        return ""
    prompt = f"""아래 자막에서 "{stock_name}"에 대해 언급된 목표가(target
price) 또는 손절가(stop-loss price)를 찾아줘. 규칙:
- 목표가나 손절가가 구체적인 숫자(원/만원 단위)로 명확히 언급된 경우만
  "목표가:X원" 또는 "손절가:X원" 형식으로 답해(둘 다 있으면 둘 다).
- 그냥 "추천합니다", "주목할 만합니다" 같은 가격 없는 일반 발언은
  해당 안 됨.
- 목표가/손절가 둘 다 숫자로 안 나왔으면 다른 말 없이 정확히
  "가격정보없음"이라고만 답해.
- 다른 설명 없이 위 형식만 답해.

자막:
{transcript}"""
    try:
        res = llm.chat.completions.create(
            model=OLLAMA_MODEL,
            max_tokens=100,
            extra_body={"options": {"num_ctx": 8192}},
            messages=[{"role": "user", "content": prompt}],
        )
        comment = res.choices[0].message.content.strip().strip('"').strip()
        # ★ "가격정보없음" in comment로 부정매칭했더니, 목표가만 있고
        #   손절가는 없는 경우("목표가:150,000원 / 손절가:가격정보없음")
        #   처럼 일부만 없어도 전체가 무효 처리되던 버그 발견 — 목표가/
        #   손절가 뒤에 실제 숫자가 있는지 긍정매칭으로 변경.
        if len(comment) > 100 or not re.search(r"(목표가|손절가)\s*[:：]?\s*[\d,]+", comment):
            return ""
        return comment
    except Exception as e:
        print(f"   ⚠️ 코멘트 생성 오류 ({stock_name}): {e}")
        return ""


_ALL_STOCK_NAMES_CACHE = None


def _get_all_stock_names() -> list:
    """kr_theme_finance.db의 전체 종목명(마켓/코드 제거된 순수명) 캐시.
    프로세스 생애주기 동안 DB가 안 바뀌니 한 번만 로드."""
    global _ALL_STOCK_NAMES_CACHE
    if _ALL_STOCK_NAMES_CACHE is not None:
        return _ALL_STOCK_NAMES_CACHE
    names = []
    try:
        conn = sqlite3.connect(THEME_DB, timeout=5)
        conn.execute("PRAGMA query_only = ON")
        rows = conn.execute("SELECT DISTINCT stock_name FROM kr_theme_stocks").fetchall()
        conn.close()
        for (raw,) in rows:
            m = re.match(r"^(.*?)(KOSPI|KOSDAQ)\s", raw)
            if m:
                names.append(m.group(1))
    except Exception as e:
        print(f"   ⚠️ 종목명 캐시 로드 오류: {e}")
    _ALL_STOCK_NAMES_CACHE = names
    return names


def _edit_distance_1(a: str, b: str) -> bool:
    """편집거리(삽입/삭제/치환) 1 이하인지 — 길이 차 2 이상이면 즉시 False."""
    if abs(len(a) - len(b)) > 1:
        return False
    if a == b:
        return True
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) <= 1
    # 길이가 1 다르면 짧은 쪽을 긴 쪽에 한 글자 삽입/삭제로 맞출 수 있는지
    short, long_ = (a, b) if len(a) < len(b) else (b, a)
    for i in range(len(long_)):
        if short == long_[:i] + long_[i + 1:]:
            return True
    return False


# ★ 2026-09-17: 로컬 LLM이 국문 종목명을 영문 약칭/브랜드명으로 바꿔
# 뽑는 경우 — DB엔 "에스피지"/"케이엠더블유"로만 있는데 "SPG"/"KMW"로
# 나와서 편집거리 보정으로도 못 잡음(스크립트 자체가 다름). 실측으로
# 발견된 것부터 수동 등록, 발견되는 대로 추가.
NAME_ALIASES = {
    "SPG": "에스피지",
    "KMW": "케이엠더블유",
}


def validate_stock_name(name: str) -> str:
    """kr_theme_finance.db 대조 — 실제 상장종목명이면 정규화된 이름, 아니면 빈문자열.
    로컬 AI(소형모델)가 합성어 중간에 공백을 끼워넣는 경우가 잦아서
    (예: "삼성 전기" → "삼성전기") 공백 제거본으로도 한 번 더 대조한다.
    ★ 2026-09-17: "비나텍"이 매번 "비나택"으로, "포스코퓨처엠"이 "포스코
    퓨처앰"으로 — Whisper가 같은 종목명을 반복적으로 한 글자씩 다르게
    잘못 알아듣는 패턴을 실측(5회 연속 동일오류)으로 확인. 정확매칭
    실패시 편집거리 1(한 글자 치환/삽입/삭제) 이내 종목명이 있으면
    그걸로 보정 — 짧은 이름(2글자 이하)은 편집거리 1도 위험해서 제외."""
    name = re.sub(r"^\(?주\)?\s*", "", name).strip()
    if not name:
        return ""
    if name in NAME_ALIASES:
        print(f"   🔧 영문약칭 보정: {name} → {NAME_ALIASES[name]}")
        name = NAME_ALIASES[name]
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

    if len(stripped) > 2:
        for real_name in _get_all_stock_names():
            if _edit_distance_1(stripped, real_name):
                print(f"   🔧 오인식 보정: {name} → {real_name}")
                return real_name
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
                # ★ 2026-09-16: 목표가/손절가 없으면 저장 스킵(generate_comment
                #   변경사항 참고 — youtube_live_monitor.py와 동일 게이트)
                comment = generate_comment(valid_name, transcript, llm)
                if not comment:
                    print(f"   ⏭️ 목표가/손절가 없음 — 저장 스킵: {valid_name}")
                    continue
                if save_pick(pick_date, valid_name, channel_label, vid, title, comment):
                    total_saved.append((pick_date, valid_name, channel_label))
                    print(f"   💾 {pick_date} | {valid_name} ({channel_label}) — {comment}")

            # 영상 단위로 상태 저장 — 중간에 죽어도 재처리 안 되게
            state.setdefault(handle, {})["last_video_id"] = vid
            _save_state(state)

        state.setdefault(handle, {})["last_video_id"] = new_last_id
        _save_state(state)

    report_count = notify_report(total_saved)
    print(f"\n✅ [유튜브] 완료 — 신규 저장 {len(total_saved)}건, 리포팅 {report_count}건")


def notify_report(total_saved: list) -> int:
    """★ 2026-09-15: 개별 신규 저장 건 나열 대신, 14일 기준(그 이전 언급은
    카운트에서 자동 제외) 2번 이상 언급된 종목만 "종목명(최초일자, N회)"
    형태로 간단히 리포팅 (대장 요청 — 날짜 나열은 헷갈려서 최초언급일+
    횟수로 축약). VOD 스캔(youtube_stock_monitor)과 라이브 모니터
    (youtube_live_monitor)가 이 함수를 공유해서 알림 포맷을 통일한다.
    total_saved: [(pick_date, stock_name, channel_label), ...]
    반환: 리포팅된 종목 수."""
    report_lines = []
    for name in sorted({n for _, n, _ in total_saved}):
        dates = get_mention_dates(name, days=14)
        if len(dates) >= 2:
            first_date = dates[0][5:].replace("-", "/")  # 09/15
            report_lines.append(f"{name}({first_date}, {len(dates)}회)")

    if report_lines:
        try:
            from notifier import Notifier
            Notifier(name="유튜브스카우트").send(
                "[유튜브] 14일내 2회+ 언급 종목\n" + ", ".join(report_lines)
            )
        except Exception as e:
            print(f"⚠️ 알림 전송 오류: {e}")

    return len(report_lines)


if __name__ == "__main__":
    main()
