"""
reset_sbo2_state.py — sbo2 상태파일 초기화
실계좌 동기화로 포지션을 새로 잡도록 포지션은 비우고
가비아 재매수 방지 + 오늘 스캔 완료 처리
"""
import json
import os
from datetime import datetime, timezone, timedelta

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "sbo2_state.json")

KST = timezone(timedelta(hours=9))
today = datetime.now(KST).strftime("%Y-%m-%d")

state = {
    "positions":       {},          # 실계좌 동기화로 새로 채울 것
    "sold_today":      {
        "079940": "14:00:00"        # 가비아 재매수 방지
    },
    "sold_today_date": today,
    "cand_date":       today,       # 오늘 스캔 완료로 처리
}

with open(STATE_FILE, 'w', encoding='utf-8') as f:
    json.dump(state, f, ensure_ascii=False, indent=2)

print(f"✅ 상태파일 초기화 완료: {STATE_FILE}")
print(f"   cand_date: {today} (재스캔 방지)")
print(f"   sold_today: 가비아(079940) 재매수 방지")
print(f"   positions: 비움 (실계좌 동기화로 자동 채워짐)")
