"""
make_review_packs.py — 외부 AI(제미나이 등) 코드 리뷰용 분야별 묶음 파일 만들기 (2026-11-13, 대장)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
대장: "클로드(누리)가 메인으로 짜고, 제미나이는 분석·제안만 — 서버 전면 오픈은 찜찜하니 분야별 리뷰용으로".
→ 7개 분야(dbot·sbot·cbot·유튜브·섹터·리나·키키)마다 시작 파일에서 import를 따라가며 우리 코드 모듈을
  자동으로 모아 텍스트 파일 하나로 묶는다. 제미나이에 그 파일만 올리면 전체 그림을 보고 리뷰할 수 있음.
비밀 보호(구조적으로):
  · 파이썬 소스(.py)와 설계 문서만 넣음 — .env·토큰·DB·로그·state json·세션 파일은 아예 안 읽음.
  · 그래도 혹시 소스에 박혀 있을 수 있는 값(.env의 키·계좌번호·토큰·웹훅 등)은 전부 [가림]으로 바꾸고,
    키처럼 생긴 긴 문자열·디스코드 웹훅 주소도 한 번 더 가림. 마지막에 .env 값이 남았는지 검사해서 있으면 실패.
  · 제미나이는 이 사본만 보므로 서버 코드를 고칠 수 없음(분석·제안 전용).
결과: ~/k-bot/review_packs/<날짜>/<분야>.txt (+ 목록 README)
실행: venv/bin/python scripts/make_review_packs.py [분야 ...]   (없으면 7개 전부)
"""
import ast
import datetime
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_ROOT = os.path.join(os.path.dirname(BASE), "review_packs")
SEARCH_DIRS = ["core", "bots", "intelligence", "interface", "lina_bot", "backtest", ""]
SKIP_PARTS = ("venv", ".claude", "archive", "__pycache__", ".git")

AREAS = {
    "dbot": ("단타봇(daybot) — 한투 실행·키움 스크리닝, 하락장 1슬롯, 음봉·이벤트 섹터·자사주 엔진",
             ["bots/daybot.py"], ["daybot"]),
    "sbot": ("스윙봇(sbot) — 후보 풀(모멘텀·추세·완화·유튜브·S7), AI 점수, ATR 손절·트레일링, 하락장 1슬롯",
             ["bots/sbot.py"], ["sbot"]),
    "cbot": ("코인봇(cbot) — 업비트 추세추종, 급락 감지, 눌림목 주머니(신규매수 중단)",
             ["bots/cbot.py"], ["cbot"]),
    "youtube": ("유튜브·방송 — 라이브 녹음→Whisper(GPU)→Haiku 종목 추출, 텔레그램 코너, MTN 아침 요약",
                ["intelligence/youtube_live_monitor.py", "intelligence/youtube_stock_monitor.py",
                 "intelligence/youtube_daily_digest.py", "intelligence/mtn_morning_brief.py",
                 "core/whisper_loader.py"], ["리나"]),
    "sector": ("섹터 — 장중 섹터 모니터, 주도 섹터 DB, 하락장 섹터 게이트",
               ["intelligence/sector_monitor.py", "intelligence/sector_watch.py", "backtest/build_sector_db.py",
                "core/sector_gate.py"], ["분석", "daybot"]),
    "lina": ("리나 — 디스코드 봇, 브리핑·스케줄러, 리나등록 자동매도(키움 신용 포함), 수급·공시 수집",
             ["lina_bot/lina_bot.py", "lina_bot/manual_autosell.py", "lina_bot/collect_daily_data.py",
              "lina_bot/backfill_investor.py", "intelligence/market_flow.py"], ["리나"]),
    "kiki": ("키키 — AI 비서 디스코드 봇, 봇 명령 IPC(pending_cmd), 브리핑·모니터·대시보드",
             ["interface/kiki.py", "interface/kiki_cmd.py", "interface/kiki_briefing.py", "interface/kiki_monitor.py",
              "interface/kiki_data.py", "interface/dashboard.py"], ["키키", "대시보드"]),
}
MAX_DEPTH = 2        # 시작 파일 → 직접 import → 그 import 까지(공용 모듈 포함, 너무 멀리 번지지 않게)

REVIEW_PROMPT = """너는 이 자동매매 시스템의 '제3자 코드 리뷰어'야. 코드를 고치지 말고 분석·제안만 해줘.
관점: ① 실돈이 나가는 경로의 버그(주문·체결 확인·수량·중복 주문·손절 누락) ② 상태 파일·IPC 경쟁 상태
③ 장 시간·휴장·지연 데이터 처리 ④ 예외가 조용히 삼켜지는 곳 ⑤ 설계상 더 나은 방법(근거와 함께).
출력 형식(표): 번호 | 파일:줄 | 문제(무엇이 어떻게 틀리나) | 근거(코드 인용) | 심각도(치명/높음/보통/낮음) | 제안.
확실하지 않으면 '추정'이라고 표시하고, 이미 주석에 '의도적'이라고 적힌 결정은 반대 근거가 있을 때만 지적해줘."""


def _env_values() -> list:
    vals = []
    try:
        for line in open(os.path.join(BASE, ".env"), encoding="utf-8"):
            if "=" in line and not line.lstrip().startswith("#"):
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
                if len(v) >= 6 and v.lower() not in ("true", "false", "openai", "kiwoom", "python", "localhost"):
                    vals.append(v)
    except OSError:
        pass
    return sorted(set(vals), key=len, reverse=True)


SECRET_RES = [
    re.compile(r"https://discord(?:app)?\.com/api/webhooks/\S+"),
    re.compile(r"\b(?:sk-[A-Za-z0-9_\-]{16,}|AIza[0-9A-Za-z_\-]{20,}|xox[abp]-[0-9A-Za-z\-]{10,})"),
    re.compile(r"\b[A-Za-z0-9_\-]{24,}\.[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{20,}\b"),      # 디스코드 봇 토큰 모양
]


def scrub(text: str, env_vals: list) -> str:
    for v in env_vals:
        text = text.replace(v, "[가림]")
    for rx in SECRET_RES:
        text = rx.sub("[가림]", text)
    return text


def _module_index() -> dict:
    idx = {}
    for d in SEARCH_DIRS:
        root = os.path.join(BASE, d)
        if not os.path.isdir(root):
            continue
        for f in os.listdir(root):
            if f.endswith(".py") and not f.startswith("test_"):
                idx.setdefault(f[:-3], os.path.join(d, f) if d else f)
    return idx


def _imports(path: str) -> set:
    try:
        tree = ast.parse(open(os.path.join(BASE, path), encoding="utf-8").read())
    except Exception:
        return set()
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module and not n.level:
            out.add(n.module.split(".")[0])
    return out


def closure(starts: list, idx: dict) -> list:
    seen, order, frontier = set(), [], [(s, 0) for s in starts]
    while frontier:
        p, depth = frontier.pop(0)
        if p in seen or not os.path.exists(os.path.join(BASE, p)):
            continue
        seen.add(p)
        order.append(p)
        if depth >= MAX_DEPTH:
            continue
        for m in sorted(_imports(p)):
            q = idx.get(m)
            if q and q not in seen and not any(s in q for s in SKIP_PARTS):
                frontier.append((q, depth + 1))
    return order


def changelog_lines(targets: list, n: int = 60) -> str:
    try:
        lines = [ln for ln in open(os.path.join(BASE, "docs", "CHANGELOG_KO.md"), encoding="utf-8")
                 if ln.startswith("- 20") and any(f"| {t} |" in ln for t in targets)]
    except OSError:
        return ""
    return "".join(lines[:n])


def build(area: str, env_vals: list, idx: dict, out_dir: str) -> tuple:
    title, starts, targets = AREAS[area]
    files = closure(starts, idx)
    parts = [f"# 리뷰 묶음: {area} — {title}\n# 만든 시각: {datetime.datetime.now():%Y-%m-%d %H:%M} · 파일 {len(files)}개\n\n",
             "## 리뷰 요청\n", REVIEW_PROMPT, "\n\n## 포함 파일 (시작 파일 → import 따라 2단계)\n",
             "".join(f"- {f}\n" for f in files)]
    arch = os.path.join(BASE, "ARCHITECTURE.md")
    if os.path.exists(arch):
        parts += ["\n## 전체 구조 문서 (ARCHITECTURE.md, 일부 오래됐을 수 있음)\n", open(arch, encoding="utf-8").read()]
    cl = changelog_lines(targets)
    if cl:
        parts += [f"\n## 최근 변경 이력 ({', '.join(targets)})\n", cl]
    for f in files:
        src = open(os.path.join(BASE, f), encoding="utf-8").read()
        parts += [f"\n\n{'=' * 100}\n### FILE: {f}\n{'=' * 100}\n", src]
    text = scrub("".join(parts), env_vals)
    leaked = [v for v in env_vals if v in text]
    if leaked:
        raise RuntimeError(f"{area}: .env 값 {len(leaked)}개가 가림 후에도 남음 — 저장 안 함")
    path = os.path.join(out_dir, f"{area}.txt")
    with open(path, "w", encoding="utf-8") as fo:
        fo.write(text)
    return path, len(files), len(text)


def main():
    want = [a for a in sys.argv[1:] if a in AREAS] or list(AREAS)
    out_dir = os.path.join(OUT_ROOT, datetime.date.today().isoformat())
    os.makedirs(out_dir, exist_ok=True)
    env_vals, idx = _env_values(), _module_index()
    rows = []
    for a in want:
        path, nf, nc = build(a, env_vals, idx, out_dir)
        rows.append((a, nf, nc))
        print(f"✅ {a:8} 파일 {nf:>2}개 · {nc / 1000:,.0f}천 자 → {path}")
    with open(os.path.join(out_dir, "README.txt"), "w", encoding="utf-8") as fo:
        fo.write("분야별 리뷰 묶음 — 제미나이에 한 파일씩 올려 리뷰 요청(파일 맨 위 '리뷰 요청' 그대로 사용).\n"
                 "결과는 누리에게 가져오면 항목별로 실제 코드와 대조해 판정.\n\n"
                 + "".join(f"- {a}.txt : 파일 {nf}개, 약 {nc / 1000:,.0f}천 자 — {AREAS[a][0]}\n" for a, nf, nc in rows))
    print(f"📁 {out_dir}")


if __name__ == "__main__":
    main()
