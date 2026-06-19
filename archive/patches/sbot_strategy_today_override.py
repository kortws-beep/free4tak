import sys

path = sys.argv[1] if len(sys.argv) > 1 else "core/sbot_strategy.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old = '''        if stage == 0:
            try:
                import datetime as _dt
                buy_date_str = tracker.get("buy_date", "")
                if buy_date_str:
                    buy_date = _dt.date.fromisoformat(buy_date_str)
                    held_days = (_dt.date.today() - buy_date).days'''
new = '''        if stage == 0:
            try:
                import datetime as _dt
                buy_date_str = tracker.get("buy_date", "")
                if buy_date_str:
                    buy_date = _dt.date.fromisoformat(buy_date_str)
                    # ★ 백테스트용: tracker에 "_bt_today"가 있으면 그 날짜를 기준으로 사용
                    #   (실전에서는 이 키가 없으므로 항상 date.today() 그대로 사용 — 영향 없음)
                    _today = tracker.get("_bt_today") or _dt.date.today()
                    held_days = (_today - buy_date).days'''

if old in content:
    content = content.replace(old, new, 1)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("✅ 수정 완료")
else:
    print("❌ 패턴 미일치")
