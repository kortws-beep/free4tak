import sys

path = sys.argv[1] if len(sys.argv) > 1 else "lina_bot/backfill_investor.py"

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

old = '''load_dotenv(dotenv_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"), override=True)'''

new = '''# .env 우선순위: lina_bot/.env → stock_bot/.env
_BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
_STOCK_BOT_ENV = os.path.dirname(_BASE_DIR)
_env1 = os.path.join(_BASE_DIR, ".env")
_env2 = os.path.join(_STOCK_BOT_ENV, ".env")
if os.path.exists(_env1):
    load_dotenv(dotenv_path=_env1, override=True)
elif os.path.exists(_env2):
    load_dotenv(dotenv_path=_env2, override=True)'''

if old in content:
    content = content.replace(old, new, 1)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(content)
    print("✅ 수정 완료")
else:
    print("❌ 패턴 미일치")
