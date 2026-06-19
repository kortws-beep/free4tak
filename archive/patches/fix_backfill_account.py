import sys

path = sys.argv[1] if len(sys.argv) > 1 else "lina_bot/backfill_investor.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old = '''    appkey = os.getenv("KIS_APPKEY")
    secret = os.getenv("KIS_SECRET")
    print("\\n🚀 [과거 수급 데이터 빈칸 채우기 시작]")
    api = KisAPI(appkey=appkey, secret=secret)'''

new = '''    appkey = os.getenv("KIS_APPKEY")
    secret = os.getenv("KIS_SECRET")
    cano   = os.getenv("KIS_CANO")
    acnt   = os.getenv("KIS_ACNT_PRDT_CD")
    print("\\n🚀 [과거 수급 데이터 빈칸 채우기 시작]")
    api = KisAPI(appkey=appkey, secret=secret, cano=cano, acnt=acnt)'''

if old in content:
    content = content.replace(old, new, 1)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("✅ 수정 완료")
else:
    print("❌ 패턴 미일치")
