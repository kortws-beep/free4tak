import sys

path = sys.argv[1] if len(sys.argv) > 1 else "lina_bot/backfill_investor.py"

with open(path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

start_idx = None
end_idx = None
for i, line in enumerate(lines):
    if 'appkey = os.getenv("KIS_APPKEY")' in line:
        start_idx = i
    if 'api = KisAPI(appkey=appkey, secret=secret)' in line:
        end_idx = i
        break

if start_idx is None or end_idx is None:
    print(f"❌ 범위를 찾을 수 없음 (start={start_idx}, end={end_idx})")
    sys.exit(1)

print(f"교체 범위: {start_idx+1} ~ {end_idx+1} 라인")
for i in range(start_idx, end_idx+1):
    print(f"  [{i+1}] {lines[i].rstrip()}")

new_block = '''    appkey = os.getenv("KIS_APPKEY")
    secret = os.getenv("KIS_SECRET")
    cano   = os.getenv("KIS_CANO")
    acnt   = os.getenv("KIS_ACNT_PRDT_CD")
    print("\\n🚀 [과거 수급 데이터 빈칸 채우기 시작]")
    api = KisAPI(appkey=appkey, secret=secret, cano=cano, acnt=acnt)
'''

new_lines = lines[:start_idx] + [new_block] + lines[end_idx+1:]

with open(path, 'w', encoding='utf-8') as f:
    f.writelines(new_lines)

print("✅ 교체 완료")
