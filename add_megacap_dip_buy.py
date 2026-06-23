import sys

path = sys.argv[1] if len(sys.argv) > 1 else "bots/sbot.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

results = []

# 1. 상수 추가 — MAX_POSITIONS 근처에 추가 (파일 상단 상수 영역)
old1 = '''MAX_POSITIONS'''
import re
m = re.search(r'^MAX_POSITIONS\s*=\s*\d+.*\n', content, re.MULTILINE)
if m:
    insert = (
        "\n"
        "# ★ 5대장주 전용 슬롯 (2026-06-23 추가) — 기존 MAX_POSITIONS와 별개로 운영\n"
        "#   최근 10일 최고가 대비 -15% 하락 시 매수, ATR 추세추종 로직에 편입\n"
        "MEGA_CAP_CODES = {\n"
        '    "005930": "삼성전자",\n'
        '    "000660": "SK하이닉스",\n'
        '    "009150": "삼성전기",\n'
        '    "402340": "SK스퀘어",\n'
        '    "005380": "현대차",\n'
        "}\n"
        "MEGA_CAP_DROP_THRESHOLD = -0.15   # 10일 최고가 대비 -15%\n"
        "MEGA_CAP_LOOKBACK_DAYS  = 10\n"
        "MEGA_CAP_BUY_AMT        = 1_000_000\n"
        "MEGA_CAP_CHECK_INTERVAL = 1800    # 30분마다 체크\n"
    )
    content = content[:m.end()] + insert + content[m.end():]
    results.append("✅ MEGA_CAP 상수 추가")
else:
    results.append("❌ MAX_POSITIONS 정의 위치 못찾음")

# 2. __init__에 타이머 변수 추가 (self._last_market_check = 0 근처)
old2 = "self._last_market_check = 0"
new2 = "self._last_market_check = 0\n        self._last_megacap_check = 0"
if old2 in content:
    content = content.replace(old2, new2, 1)
    results.append("✅ _last_megacap_check 타이머 추가")
else:
    results.append("❌ _last_market_check 위치 못찾음")

print("\n".join(results))
with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
