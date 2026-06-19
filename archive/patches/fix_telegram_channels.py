import sys

path = sys.argv[1] if len(sys.argv) > 1 else "intelligence/telegram_monitor.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old = '''CHANNELS = [
    "hankyung_fin",   # 한국경제 금융
    "stocknewskorea", # 주식뉴스
    "kind_krx",       # 공시(KIND)
    "AllStockNews",   # 전체 주식 뉴스 (상한가/이슈)
]'''
new = '''CHANNELS = [
    "hankyung_fin",   # 한국경제 금융
    "stocknewskorea", # 주식뉴스
    "kind_krx",       # 공시(KIND)
    "AllStockNews",   # 전체 주식 뉴스 (상한가/이슈) — 여의도 주식 속보
    "FastStockNews",  # 주식급등일보
    "darthacking",    # 실시간 주식 공시
]'''

if old in content:
    content = content.replace(old, new, 1)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("✅ 수정 완료")
else:
    print("❌ 패턴 미일치")
