import sys

path = sys.argv[1] if len(sys.argv) > 1 else "lina_bot/collect_daily_data.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old = '''import os
import re
import time
import sqlite3
from datetime import datetime, timedelta
from dotenv import load_dotenv, find_dotenv  # 💡 find_dotenv 추가
# ── 환경변수 & 경로 (대장님 전용 stock_bot .env 자동 연동) ──────
load_dotenv(find_dotenv(), override=True)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE_DIR, "kr_theme_finance.db")
# ── 💡 한투 API 임포트 수정 (신형 클래스명 반영) ──────────────────
# sbot2.py와 동일하게 kis_api 파일에서 KoreaInvestmentAPI를 가져옵니다.
try:
    from kis_api import KoreaInvestmentAPI as KisAPI
except ImportError:
    # 혹시 모를 구형 명칭 백업용 방어 코드
    from kis_api import KisAPI'''

new = '''import os
import sys
import re
import time
import sqlite3
from datetime import datetime, timedelta
from dotenv import load_dotenv, find_dotenv  # 💡 find_dotenv 추가
# ── 환경변수 & 경로 (대장님 전용 stock_bot .env 자동 연동) ──────
load_dotenv(find_dotenv(), override=True)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE_DIR, "kr_theme_finance.db")
# ── ★ sys.path에 core/ 추가 (kis_api.py 위치) — 2026-06-19 추가
_PROJECT_ROOT = os.path.dirname(BASE_DIR)
for _p in (os.path.join(_PROJECT_ROOT, "core"),
           os.path.join(_PROJECT_ROOT, "interface"),
           os.path.join(_PROJECT_ROOT, "bots"),
           _PROJECT_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)
# ── 💡 한투 API 임포트 수정 (신형 클래스명 반영) ──────────────────
# sbot2.py와 동일하게 kis_api 파일에서 KoreaInvestmentAPI를 가져옵니다.
try:
    from kis_api import KoreaInvestmentAPI as KisAPI
except ImportError:
    # 혹시 모를 구형 명칭 백업용 방어 코드
    from kis_api import KisAPI'''

if old in content:
    content = content.replace(old, new, 1)
    print("✅ 수정 완료")
else:
    print("❌ 패턴 미일치")
    sys.exit(1)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
