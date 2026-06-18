import sys

path = sys.argv[1] if len(sys.argv) > 1 else "sbot_backtest_engine.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old = '''        for code in list(self.positions.keys()):
            if code not in self.positions:
                continue
            pos = self.positions[code]

            # ATR / MA20 — DB 컬럼 우선, 없으면 features에서 보완'''
new = '''        for code in list(self.positions.keys()):
            if code not in self.positions:
                continue
            pos = self.positions[code]

            # ★ 25일 기한 로직용 — 시뮬레이션 날짜를 tracker에 주입 (실전 영향 없음)
            if code in self.peak_tracker:
                self.peak_tracker[code]["_bt_today"] = date.date()

            # ATR / MA20 — DB 컬럼 우선, 없으면 features에서 보완'''

if old in content:
    content = content.replace(old, new, 1)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("✅ 수정 완료")
else:
    print("❌ 패턴 미일치")
