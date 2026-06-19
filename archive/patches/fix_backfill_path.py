import sys

path = sys.argv[1] if len(sys.argv) > 1 else "lina_bot/backfill_investor.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old = '''import os
import re
import time
import sqlite3
import requests
from datetime import datetime
from dotenv import load_dotenv
from kis_api import KisAPI'''

new = '''import os
import re
import sys
import time
import sqlite3
import requests
from datetime import datetime
from dotenv import load_dotenv

# ── sys.path 설정 (core/kis_api.py 사용) ───────────────────────
_STOCK_BOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ["core", "interface", "bots", ""]:
    _p = os.path.join(_STOCK_BOT, _d)
    if os.path.exists(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

from kis_api import KisAPI'''

if old in content:
    content = content.replace(old, new, 1)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("✅ 수정 완료")
else:
    print("❌ 패턴 미일치")
