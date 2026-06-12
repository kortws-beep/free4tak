"""
fix_sbo2_state.py — sbo2 상태파일 종목명 수정
"""
import json
import os

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "sbo2_state.json")

# 실제 종목명 맵
NAME_MAP = {
    "042660": "티엘비",
    "047040": "대우건설",
    "356860": "한화오션",
    "079940": "가비아",
}

# 가비아 실제 매수 정보 수정
CORRECT_DATA = {
    "079940": {
        "entry_price": 31872.0,
        "qty":         58,
        "stop_price":  round(31872.0 * 0.90, 0),   # -10%
        "tgt_price":   round(31872.0 * 1.15, 0),   # +15%
    }
}

with open(STATE_FILE, 'r', encoding='utf-8') as f:
    state = json.load(f)

positions = state.get("positions", {})

for code, pos in positions.items():
    # 종목명 수정
    if code in NAME_MAP:
        pos["name"] = NAME_MAP[code]

    # 가비아 매수 정보 수정
    if code in CORRECT_DATA:
        for k, v in CORRECT_DATA[code].items():
            pos[k] = v

# cand_date 추가 (오늘로 설정해서 재스캔 방지)
from datetime import datetime, timezone, timedelta
KST = timezone(timedelta(hours=9))
state["cand_date"] = datetime.now(KST).strftime("%Y-%m-%d")

# sold_today에 가비아 추가 (중복 매수 방지)
state.setdefault("sold_today", {})
# 079940은 이미 보유중이니 sold_today에 넣지 않음 (매도 후 재매수 금지용)

with open(STATE_FILE, 'w', encoding='utf-8') as f:
    json.dump(state, f, ensure_ascii=False, indent=2)

print("✅ 상태파일 수정 완료!")
print("\n[수정된 포지션]")
for code, pos in state["positions"].items():
    print(f"  {pos['name']}({code}) | {pos['entry_price']:,}원 × {pos['qty']}주 "
          f"| 손절:{pos['stop_price']:,} 목표:{pos['tgt_price']:,}")
print(f"\ncand_date: {state['cand_date']}")
