#!/usr/bin/env python3
"""
patch_master_db_v2_fix.py — master_db v2 누락 패치 보완
================================================================
실행 방법:
    cd /home/free4tak/k-bot/stock_bot
    source venv/bin/activate
    python3 patch_master_db_v2_fix.py

누락된 패치:
    [1] nbot_order.py — do_buy 후 upsert_position 추가
    [2] sbot.py       — master_db import + _do_buy 후 upsert_position 추가
    [3] cbot.py       — _do_buy 후 upsert + _do_sell 후 remove_position
================================================================
"""
import os, shutil, sys

BASE       = "/home/free4tak/k-bot/stock_bot"
NBOT_ORDER = os.path.join(BASE, "bots", "nbot_order.py")
SBOT_FILE  = os.path.join(BASE, "bots", "sbot.py")
CBOT_FILE  = os.path.join(BASE, "bots", "cbot.py")

def backup(fp):
    bak = fp + ".bak3"
    shutil.copy2(fp, bak)
    print(f"  💾 백업: {bak}")

def read_file(fp):
    with open(fp, "r", encoding="utf-8") as f:
        return f.read()

def write_file(fp, content):
    with open(fp, "w", encoding="utf-8") as f:
        f.write(content)


# ============================================================
# [1] nbot_order.py — do_buy 후 upsert_position
# ============================================================
def patch_nbot_order(content):
    errors = []

    OLD = (
        "        # 1차 매수일 때만 sold_today 등록 (재매수 금지용)\n"
        "        if not is_second:\n"
        "            self.sold_today[code] = now_hms()"
    )
    NEW = (
        "        # ★ master_positions 등록 (대시보드/리스크매니저)\n"
        "        if _master_upsert:\n"
        "            try:\n"
        "                _master_upsert(\n"
        "                    bot_type      = \"nbot\",\n"
        "                    code          = code,\n"
        "                    stock_name    = self._name(code),\n"
        "                    entry_price   = price,\n"
        "                    current_price = price,\n"
        "                    qty           = qty,\n"
        "                    buy_time      = ctx.get(\"buy_time\", \"\"),\n"
        "                    buy_tag       = buy_tag or ctx.get(\"buy_tag\", \"\"),\n"
        "                    ai_score      = ctx.get(\"ai_score\", 0),\n"
        "                )\n"
        "            except Exception as _e:\n"
        "                print(f\"\\u26a0\\ufe0f master_positions upsert \\uc624\\ub958: {_e}\")\n\n"
        "        # 1차 매수일 때만 sold_today 등록 (재매수 금지용)\n"
        "        if not is_second:\n"
        "            self.sold_today[code] = now_hms()"
    )
    if OLD in content:
        content = content.replace(OLD, NEW, 1)
        print("  ✅ [1] nbot_order do_buy → upsert_position 추가")
    elif "bot_type      = \"nbot\"" in content:
        print("  ⏭️  [1] 이미 패치됨 — 스킵")
    else:
        errors.append("nbot_order: sold_today 블록 못 찾음")

    return content, errors


# ============================================================
# [2] sbot.py — import + _do_buy 후 upsert_position
# ============================================================
def patch_sbot(content):
    errors = []

    # import 추가 (load_dotenv 뒤)
    OLD_IMPORT = "load_dotenv('/home/free4tak/k-bot/stock_bot/.env')\n"
    NEW_IMPORT = (
        "load_dotenv('/home/free4tak/k-bot/stock_bot/.env')\n\n"
        "try:\n"
        "    from master_db import (\n"
        "        record_trade    as _master_record,\n"
        "        upsert_position as _master_upsert,\n"
        "        remove_position as _master_remove,\n"
        "    )\n"
        "except Exception:\n"
        "    _master_record = None\n"
        "    _master_upsert = None\n"
        "    _master_remove = None\n"
    )
    if "_master_upsert" in content:
        print("  ⏭️  [2-1] sbot import 이미 있음 — 스킵")
    elif OLD_IMPORT in content:
        content = content.replace(OLD_IMPORT, NEW_IMPORT, 1)
        print("  ✅ [2-1] sbot import 추가")
    else:
        errors.append("sbot: load_dotenv 라인 못 찾음")

    # _do_buy: sold_today[code] = "buying" 직후 upsert
    OLD_BUY = "        self.sold_today[code] = \"buying\""
    NEW_BUY = (
        "        self.sold_today[code] = \"buying\"\n\n"
        "        # ★ master_positions 등록\n"
        "        if _master_upsert:\n"
        "            try:\n"
        "                ctx2 = self.buy_context.get(code, {})\n"
        "                _master_upsert(\n"
        "                    bot_type      = \"sbot\",\n"
        "                    code          = code,\n"
        "                    stock_name    = self._name(code),\n"
        "                    entry_price   = price,\n"
        "                    current_price = price,\n"
        "                    qty           = qty,\n"
        "                    buy_time      = ctx2.get(\"buy_time\", \"\"),\n"
        "                    buy_tag       = ctx2.get(\"buy_tag\", \"\"),\n"
        "                    ai_score      = ctx2.get(\"ai_score\", 0),\n"
        "                )\n"
        "            except Exception as _e:\n"
        "                print(f\"\\u26a0\\ufe0f master_positions upsert \\uc624\\ub958: {_e}\")"
    )
    if "bot_type      = \"sbot\"" in content:
        print("  ⏭️  [2-2] sbot _do_buy upsert 이미 있음 — 스킵")
    elif OLD_BUY in content:
        content = content.replace(OLD_BUY, NEW_BUY, 1)
        print("  ✅ [2-2] sbot _do_buy → upsert_position 추가")
    else:
        errors.append("sbot: sold_today[code]='buying' 라인 못 찾음")

    return content, errors


# ============================================================
# [3] cbot.py — _do_buy 후 upsert + _do_sell 후 remove
# ============================================================
def patch_cbot(content):
    errors = []

    # _do_buy: sold_today[market] = None 직후 upsert
    OLD_BUY = "                    self.sold_today[market] = None"
    NEW_BUY = (
        "                    self.sold_today[market] = None\n"
        "                    # ★ master_positions 등록\n"
        "                    if _master_upsert:\n"
        "                        try:\n"
        "                            _master_upsert(\n"
        "                                bot_type      = \"cbot\",\n"
        "                                code          = market,\n"
        "                                stock_name    = market.replace(\"KRW-\", \"\"),\n"
        "                                entry_price   = buy_price,\n"
        "                                current_price = buy_price,\n"
        "                                qty           = int(buy_qty * 10000),\n"
        "                            )\n"
        "                        except Exception as _e:\n"
        "                            print(f\"\\u26a0\\ufe0f master_positions upsert \\uc624\\ub958: {_e}\")"
    )
    if "bot_type      = \"cbot\"" in content:
        print("  ⏭️  [3-1] cbot _do_buy upsert 이미 있음 — 스킵")
    elif OLD_BUY in content:
        content = content.replace(OLD_BUY, NEW_BUY, 1)
        print("  ✅ [3-1] cbot _do_buy → upsert_position 추가")
    else:
        errors.append("cbot: sold_today[market]=None 라인 못 찾음")

    # _do_sell: sold_today[market] = now_hms() 직후 remove
    OLD_SELL = (
        "                self.daily_pnl += profit_krw\n"
        "                self.sold_today[market] = now_hms()\n"
        "                print(f\"\\ud83d\\udcca \\ub2f9\\uc77cPNL: {self.daily_pnl:+,.0f}\\uc6d0 / \"\n"
        "                      f\"\\ud55c\\ub3c4:{DAILY_LOSS_LIMIT:,}\\uc6d0\")\n"
        "                return True"
    )
    NEW_SELL = (
        "                self.daily_pnl += profit_krw\n"
        "                self.sold_today[market] = now_hms()\n"
        "                # ★ master_positions 삭제\n"
        "                if _master_remove:\n"
        "                    try:\n"
        "                        _master_remove(\"cbot\", market)\n"
        "                    except Exception as _e:\n"
        "                        print(f\"\\u26a0\\ufe0f master_positions remove \\uc624\\ub958: {_e}\")\n"
        "                print(f\"\\ud83d\\udcca \\ub2f9\\uc77cPNL: {self.daily_pnl:+,.0f}\\uc6d0 / \"\n"
        "                      f\"\\ud55c\\ub3c4:{DAILY_LOSS_LIMIT:,}\\uc6d0\")\n"
        "                return True"
    )

    # 유니코드 이스케이프 없이 직접 비교
    SELL_MARKER = "                self.sold_today[market] = now_hms()"
    REMOVE_MARKER = "_master_remove(\"cbot\", market)"

    if REMOVE_MARKER in content:
        print("  ⏭️  [3-2] cbot _do_sell remove 이미 있음 — 스킵")
    elif SELL_MARKER in content:
        # 간단하게 sold_today 다음 줄 패턴으로 찾기
        content = content.replace(
            "                self.sold_today[market] = now_hms()\n"
            "                print(f",
            "                self.sold_today[market] = now_hms()\n"
            "                # ★ master_positions 삭제\n"
            "                if _master_remove:\n"
            "                    try:\n"
            "                        _master_remove(\"cbot\", market)\n"
            "                    except Exception as _e:\n"
            "                        print(f\"⚠️ master_positions remove 오류: {_e}\")\n"
            "                print(f",
            1
        )
        print("  ✅ [3-2] cbot _do_sell → remove_position 추가")
    else:
        errors.append("cbot: sold_today[market]=now_hms() 블록 못 찾음")

    return content, errors


# ============================================================
# 메인
# ============================================================
def main():
    print("=" * 60)
    print("  master_db v2 누락 패치 보완")
    print("=" * 60)

    all_errors = []

    print(f"\n🔧 [1/3] nbot_order.py 패치 중...")
    backup(NBOT_ORDER)
    content, errs = patch_nbot_order(read_file(NBOT_ORDER))
    all_errors += errs
    write_file(NBOT_ORDER, content)
    print("  💾 저장 완료")

    print(f"\n🔧 [2/3] sbot.py 패치 중...")
    backup(SBOT_FILE)
    content, errs = patch_sbot(read_file(SBOT_FILE))
    all_errors += errs
    write_file(SBOT_FILE, content)
    print("  💾 저장 완료")

    print(f"\n🔧 [3/3] cbot.py 패치 중...")
    backup(CBOT_FILE)
    content, errs = patch_cbot(read_file(CBOT_FILE))
    all_errors += errs
    write_file(CBOT_FILE, content)
    print("  💾 저장 완료")

    print("\n" + "=" * 60)
    if all_errors:
        print("⚠️  일부 패치 실패:")
        for e in all_errors:
            print(f"  - {e}")
        print("\n아래 명령으로 실제 코드 확인:")
        print("  grep -n 'sold_today' /home/free4tak/k-bot/stock_bot/bots/cbot.py | head -10")
    else:
        print("✅ 누락 패치 완료!")
        print("\n전체 재시작:")
        print("  ./bot.sh restart")
    print("=" * 60)


if __name__ == "__main__":
    main()
