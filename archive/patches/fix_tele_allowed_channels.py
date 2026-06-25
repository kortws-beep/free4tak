import sys

path = sys.argv[1] if len(sys.argv) > 1 else "lina_bot/tele_swing_analyzer.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old = '''    # 종목 추천 성격 채널만 허용 (뉴스 피드 채널 제외)
    ALLOWED_CHANNELS = (
        "stocknewskorea",  # 주식뉴스
        "stock0",          # 주식 정보
        "darthacking",     # 다크해킹
        "korea_news11",    # 한국 뉴스
    )'''

new = '''    # ★ 실제 정보량이 많은 3채널 기준으로 변경 (2026-06-19)
    #   기존 stocknewskorea/stock0/korea_news11은 telegram_events에 데이터 0건 확인됨
    ALLOWED_CHANNELS = (
        "AllStockNews",       # 여의도 주식 속보 — 상한가/공시/특징주
        "FastStockNews",      # 주식급등일보
        "darthacking",        # 실시간 주식 공시
        "-1001208429502",     # FastStockNews 추정 내부 채널ID (username 미매칭분 흡수)
    )'''

if old in content:
    content = content.replace(old, new, 1)
    print("✅ 수정 완료")
else:
    print("❌ 패턴 미일치")
    sys.exit(1)

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)
