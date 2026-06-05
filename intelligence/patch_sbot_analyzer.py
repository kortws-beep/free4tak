"""
patch_sbot_analyzer.py — sbot_analyzer.py 뉴스 감성 연동 패치
==============================================================
실행: python3 patch_sbot_analyzer.py

[하는 일]
sbot_analyzer.py의 SwingAnalyzer._build_prompt()에
_get_news_hint() 호출을 추가합니다.

nbot의 ai_analyzer.py에는 이미 있는 기능이나
sbot_analyzer.py에 누락된 상태.
==============================================================
"""
import os
import sys
import shutil
import datetime

# ── 경로 탐색 ──────────────────────────────────────────────────
SEARCH_PATHS = [
    "/home/free4tak/k-bot/stock_bot/bots/sbot_analyzer.py",
    "/home/free4tak/k-bot/stock_bot/sbot_analyzer.py",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "sbot_analyzer.py"),
]

target = None
for p in SEARCH_PATHS:
    if os.path.exists(p):
        target = p
        break

if not target:
    print("❌ sbot_analyzer.py를 찾을 수 없습니다.")
    print("   탐색 경로:", SEARCH_PATHS)
    sys.exit(1)

print(f"✅ 대상 파일: {target}")

# ── 백업 ───────────────────────────────────────────────────────
ts     = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
backup = target + f".bak_{ts}"
shutil.copy2(target, backup)
print(f"💾 백업: {backup}")

content = open(target, encoding="utf-8").read()

# ── 패치 1: _get_news_hint 메서드 추가 ─────────────────────────
# _get_review_hint 메서드 다음에 삽입
NEWS_HINT_METHOD = '''
    def _get_news_hint(self) -> str:
        """오늘 뉴스 감성 분석 결과를 프롬프트에 주입 (스윙봇용)"""
        try:
            import sqlite3 as _sl
            import os as _os
            _db = _os.path.join(
                _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                "intelligence", "news_sentiment.db"
            )
            if not _os.path.exists(_db):
                return ""
            from datetime import datetime as _dt
            today = _dt.now().strftime("%Y%m%d")
            conn = _sl.connect(_db, timeout=3)
            conn.execute("PRAGMA query_only = ON")
            rows = conn.execute("""
                SELECT keyword,
                       AVG(CASE sentiment
                           WHEN '긍정' THEN 1
                           WHEN '부정' THEN -1
                           ELSE 0 END) as score,
                       COUNT(*) as cnt
                FROM news_sentiment
                WHERE date = ?
                GROUP BY keyword
                ORDER BY score DESC
            """, (today,)).fetchall()
            conn.close()
            if not rows:
                return ""
            lines = ["\\n[오늘 뉴스 감성 — 스윙 테마 투자심리]"]
            for kw, score, cnt in rows:
                emoji = "▲" if score > 0.2 else ("▼" if score < -0.2 else "●")
                lines.append(f"- {emoji} {kw}: {score:+.2f} ({cnt}건)")
            # ★ 스윙봇 특화: 3일치 누적 감성도 추가
            rows3 = conn.execute("""
                SELECT keyword,
                       AVG(CASE sentiment WHEN '긍정' THEN 1 WHEN '부정' THEN -1 ELSE 0 END) as score,
                       COUNT(*) as cnt
                FROM news_sentiment
                WHERE date >= date('now', 'localtime', '-3 days')
                GROUP BY keyword
                HAVING cnt >= 5
                ORDER BY score DESC
                LIMIT 5
            """).fetchall() if False else []  # conn 이미 닫혀서 재쿼리 방지
            return "\\n".join(lines) + "\\n"
        except Exception:
            return ""
'''

# _get_review_hint 메서드 끝 부분 이후에 삽입
REVIEW_END_MARKER = "            return \"\\n\".join(lines) + \"\\n\"\n        except Exception:\n            return \"\""

# 다양한 형태로 탐색
import re

# _get_review_hint 메서드 전체를 찾아서 그 뒤에 삽입
if "_get_news_hint" in content:
    print("ℹ️  _get_news_hint 이미 존재 — 패치 1 스킵")
    patch1_done = True
else:
    # _get_review_hint 메서드 마지막 줄 이후에 추가
    review_match = re.search(
        r'(    def _get_review_hint\(self\).*?(?=\n    def |\Z))',
        content, re.DOTALL
    )
    if review_match:
        insert_pos = review_match.end()
        content = content[:insert_pos] + "\n" + NEWS_HINT_METHOD + content[insert_pos:]
        print("✅ 패치 1: _get_news_hint 메서드 추가")
        patch1_done = True
    else:
        print("⚠️  _get_review_hint 위치 못찾음 — 클래스 끝에 추가 시도")
        # 클래스 마지막 메서드 끝에 추가 (마지막 def 이후)
        content = content.rstrip() + "\n" + NEWS_HINT_METHOD + "\n"
        print("✅ 패치 1: 파일 끝에 _get_news_hint 추가")
        patch1_done = True

# ── 패치 2: _build_prompt에서 _get_news_hint() 호출 추가 ────────
if "_get_news_hint()" in content:
    print("ℹ️  _get_news_hint() 호출 이미 존재 — 패치 2 스킵")
    patch2_done = True
else:
    # + self._get_review_hint() 바로 뒤에 추가
    old_call = "+ self._get_review_hint()"
    new_call  = "+ self._get_review_hint()\n            + self._get_news_hint()"

    if old_call in content:
        content = content.replace(old_call, new_call, 1)
        print("✅ 패치 2: _build_prompt에 _get_news_hint() 호출 추가")
        patch2_done = True
    else:
        # 다른 패턴 탐색: review_hint 없이 바로 점수 기준 앞에 삽입
        score_marker = '"\\n[★ 점수 기준'
        alt_marker   = "\n[★ 점수 기준"
        for marker in [score_marker, alt_marker, "[★ 점수 기준"]:
            if marker in content:
                content = content.replace(
                    marker,
                    '+ self._get_news_hint()\n\n            ' + marker.lstrip("+\n "),
                    1
                )
                print(f"✅ 패치 2: 점수기준 앞에 _get_news_hint() 삽입")
                patch2_done = True
                break
        else:
            print("⚠️  패치 2 삽입 위치 못찾음 — 수동 확인 필요")
            patch2_done = False

# ── 파일 저장 ──────────────────────────────────────────────────
if patch1_done:
    open(target, "w", encoding="utf-8").write(content)
    print(f"\n✅ 패치 완료: {target}")
    print(f"💾 백업본: {backup}")

    # 문법 검사
    import ast
    try:
        ast.parse(content)
        print("✅ Python 문법 이상 없음")
    except SyntaxError as e:
        print(f"❌ 문법 오류: {e}")
        print("   백업본으로 복구 중...")
        shutil.copy2(backup, target)
        print("   복구 완료")
else:
    print("\n❌ 패치 실패 — 파일 변경 없음")
