#!/usr/bin/env python3
"""정확한 패턴으로 재패치"""
import os, shutil

BASE       = "/home/free4tak/k-bot/stock_bot"
NBOT_ORDER = os.path.join(BASE, "bots", "nbot_order.py")
CBOT_FILE  = os.path.join(BASE, "bots", "cbot.py")

def read_file(fp):
    with open(fp, "r", encoding="utf-8") as f:
        return f.read()

def write_file(fp, content):
    with open(fp, "w", encoding="utf-8") as f:
        f.write(content)

# ============================================================
# nbot_order.py 패치
# 문제: 기존에 else 절이 이미 있는데 또 else를 추가해서 중복
# 해결: else 절 건드리지 말고, is_full_sell 블록 안에만 추가
# ============================================================
def patch_nbot(content):
    # 전량매도 시 remove_position — if is_full_sell 블록 안에 추가
    OLD = (
        "        if is_full_sell:\n"
        "            self.buy_tags.pop(code, None)\n"
        "            self.buy_context.pop(code, None)\n"
        "            self.positions.pop(code, None)\n"
        "        else:\n"
        "            # 부분 매도: 잔량만 갱신\n"
        "            self.positions[code] = {\n"
        "                \"entry_price\": current_pos.get(\"entry_price\", sell_price),\n"
        "                \"qty\":         held_qty - qty,\n"
        "            }"
    )
    NEW = (
        "        if is_full_sell:\n"
        "            self.buy_tags.pop(code, None)\n"
        "            self.buy_context.pop(code, None)\n"
        "            self.positions.pop(code, None)\n"
        "            # ★ master_positions 삭제\n"
        "            if _master_remove:\n"
        "                try:\n"
        "                    _master_remove('nbot', code)\n"
        "                except Exception as _e:\n"
        "                    print(f'⚠️ master remove 오류: {_e}')\n"
        "        else:\n"
        "            # 부분 매도: 잔량만 갱신\n"
        "            self.positions[code] = {\n"
        "                \"entry_price\": current_pos.get(\"entry_price\", sell_price),\n"
        "                \"qty\":         held_qty - qty,\n"
        "            }\n"
        "            # ★ master_positions 잔량 갱신\n"
        "            if _master_upsert:\n"
        "                try:\n"
        "                    _master_upsert(\n"
        "                        bot_type='nbot', code=code,\n"
        "                        qty=held_qty - qty,\n"
        "                        stage=self.peak_tracker.get(code, {}).get('stage', 0),\n"
        "                    )\n"
        "                except Exception as _e:\n"
        "                    print(f'⚠️ master upsert 오류: {_e}')"
    )
    if OLD in content:
        content = content.replace(OLD, NEW, 1)
        print("  ✅ nbot_order do_sell remove/upsert 추가")
    elif "_master_remove('nbot', code)" in content:
        print("  ⏭️  nbot_order 이미 패치됨")
    else:
        print("  ❌ nbot_order 패턴 못 찾음")

    # do_buy 후 upsert — sold_today 직전에 추가
    OLD2 = (
        "        # 1차 매수일 때만 sold_today 등록 (재매수 금지용)\n"
        "        if not is_second:\n"
        "            self.sold_today[code] = now_hms()"
    )
    NEW2 = (
        "        # ★ master_positions 등록\n"
        "        if _master_upsert:\n"
        "            try:\n"
        "                _master_upsert(\n"
        "                    bot_type='nbot', code=code,\n"
        "                    stock_name=self._name(code),\n"
        "                    entry_price=price, current_price=price,\n"
        "                    qty=qty,\n"
        "                    buy_time=ctx.get('buy_time', ''),\n"
        "                    buy_tag=buy_tag or ctx.get('buy_tag', ''),\n"
        "                    ai_score=ctx.get('ai_score', 0),\n"
        "                )\n"
        "            except Exception as _e:\n"
        "                print(f'⚠️ master upsert 오류: {_e}')\n"
        "        # 1차 매수일 때만 sold_today 등록 (재매수 금지용)\n"
        "        if not is_second:\n"
        "            self.sold_today[code] = now_hms()"
    )
    if OLD2 in content:
        content = content.replace(OLD2, NEW2, 1)
        print("  ✅ nbot_order do_buy upsert 추가")
    elif "bot_type='nbot', code=code," in content:
        print("  ⏭️  nbot_order do_buy 이미 패치됨")
    else:
        print("  ❌ nbot_order do_buy 패턴 못 찾음")

    return content

# ============================================================
# cbot.py 패치
# 문제: try: 안에 또 try: from 이 들어가서 IndentationError
# 해결: 기존 try/except ImportError 블록 밖에 별도로 추가
# ============================================================
def patch_cbot(content):
    # import 추가 — load_dotenv 한 줄 뒤에 깔끔하게
    OLD_IMPORT = "load_dotenv('/home/free4tak/k-bot/stock_bot/.env')\n"
    NEW_IMPORT = (
        "load_dotenv('/home/free4tak/k-bot/stock_bot/.env')\n"
        "try:\n"
        "    from master_db import (\n"
        "        upsert_position as _master_upsert,\n"
        "        remove_position as _master_remove,\n"
        "    )\n"
        "except Exception:\n"
        "    _master_upsert = None\n"
        "    _master_remove = None\n"
    )
    if "_master_upsert" in content:
        print("  ⏭️  cbot import 이미 있음")
    elif OLD_IMPORT in content:
        content = content.replace(OLD_IMPORT, NEW_IMPORT, 1)
        print("  ✅ cbot import 추가")
    else:
        print("  ❌ cbot load_dotenv 패턴 못 찾음")

    # _do_buy: sold_today[market] = None 직후
    OLD_BUY = (
        "                    # 1차 매수 — sold_today에 등록 (재매수 금지)\n"
        "                    self.sold_today[market] = None"
    )
    NEW_BUY = (
        "                    # 1차 매수 — sold_today에 등록 (재매수 금지)\n"
        "                    self.sold_today[market] = None\n"
        "                    if _master_upsert:\n"
        "                        try:\n"
        "                            _master_upsert(\n"
        "                                bot_type='cbot', code=market,\n"
        "                                stock_name=market.replace('KRW-', ''),\n"
        "                                entry_price=buy_price,\n"
        "                                current_price=buy_price,\n"
        "                                qty=int(buy_qty * 10000),\n"
        "                            )\n"
        "                        except Exception as _e:\n"
        "                            print(f'⚠️ master upsert 오류: {_e}')"
    )
    if "bot_type='cbot'" in content:
        print("  ⏭️  cbot _do_buy 이미 패치됨")
    elif OLD_BUY in content:
        content = content.replace(OLD_BUY, NEW_BUY, 1)
        print("  ✅ cbot _do_buy upsert 추가")
    else:
        print("  ❌ cbot _do_buy 패턴 못 찾음")

    # _do_sell: sold_today[market] = now_hms() 직후 remove
    OLD_SELL = (
        "                self.sold_today[market] = now_hms()\n"
        "                print(f"
    )
    NEW_SELL = (
        "                self.sold_today[market] = now_hms()\n"
        "                if _master_remove:\n"
        "                    try:\n"
        "                        _master_remove('cbot', market)\n"
        "                    except Exception as _e:\n"
        "                        print(f'⚠️ master remove 오류: {_e}')\n"
        "                print(f"
    )
    if "_master_remove('cbot', market)" in content:
        print("  ⏭️  cbot _do_sell 이미 패치됨")
    elif OLD_SELL in content:
        content = content.replace(OLD_SELL, NEW_SELL, 1)
        print("  ✅ cbot _do_sell remove 추가")
    else:
        print("  ❌ cbot _do_sell 패턴 못 찾음")

    return content

# ============================================================
# 실행
# ============================================================
import ast

print("🔧 nbot_order.py 패치...")
shutil.copy2(NBOT_ORDER, NBOT_ORDER + ".bak4")
c = patch_nbot(read_file(NBOT_ORDER))
try:
    ast.parse(c)
    write_file(NBOT_ORDER, c)
    print("  ✅ 문법 OK — 저장 완료")
except SyntaxError as e:
    print(f"  ❌ 문법 오류: {e} — 저장 안 함")

print("\n🔧 cbot.py 패치...")
shutil.copy2(CBOT_FILE, CBOT_FILE + ".bak4")
c = patch_cbot(read_file(CBOT_FILE))
try:
    ast.parse(c)
    write_file(CBOT_FILE, c)
    print("  ✅ 문법 OK — 저장 완료")
except SyntaxError as e:
    print(f"  ❌ 문법 오류: {e} — 저장 안 함")

print("\n완료!")
