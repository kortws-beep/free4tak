#!/usr/bin/env python3
"""
patch_master_db_v2.py — master_db v2 연동 자동 패치
================================================================
실행 방법:
    cd /home/free4tak/k-bot/stock_bot
    source venv/bin/activate
    python3 patch_master_db_v2.py

패치 내용:
    [1] core/master_db.py 교체 (v2 신규)
    [2] bots/nbot_order.py
        - 매수 시 upsert_position() 호출 추가
        - 분할매도 시 record_trade(is_partial=True) + upsert_position() 호출
        - 전량매도 시 record_trade(is_partial=False) + remove_position() 호출
    [3] bots/sbot.py
        - 동일 패턴 적용
    [4] bots/cbot.py
        - 매수/매도 시 upsert_position / remove_position 호출

백업: 각 파일 .bak2 으로 자동 백업
================================================================
"""
import os
import shutil
import sys

BASE          = "/home/free4tak/k-bot/stock_bot"
MASTER_DB_DST = os.path.join(BASE, "core",  "master_db.py")
MASTER_DB_SRC = os.path.join(os.path.dirname(__file__), "master_db.py")
NBOT_ORDER    = os.path.join(BASE, "bots",  "nbot_order.py")
SBOT_FILE     = os.path.join(BASE, "bots",  "sbot.py")
CBOT_FILE     = os.path.join(BASE, "bots",  "cbot.py")

def backup(filepath):
    bak = filepath + ".bak2"
    shutil.copy2(filepath, bak)
    print(f"  💾 백업: {bak}")

def read_file(fp):
    with open(fp, "r", encoding="utf-8") as f:
        return f.read()

def write_file(fp, content):
    with open(fp, "w", encoding="utf-8") as f:
        f.write(content)

def check_files():
    ok = True
    for f in [MASTER_DB_SRC, NBOT_ORDER, SBOT_FILE, CBOT_FILE]:
        if os.path.exists(f):
            print(f"  ✅ 발견: {f}")
        else:
            print(f"  ❌ 없음: {f}")
            ok = False
    return ok


# ============================================================
# [1] master_db.py 교체
# ============================================================
def patch_master_db():
    print(f"\n🔧 [1/4] master_db.py 교체...")
    backup(MASTER_DB_DST)
    shutil.copy2(MASTER_DB_SRC, MASTER_DB_DST)
    print(f"  ✅ {MASTER_DB_DST} 교체 완료")


# ============================================================
# [2] nbot_order.py 패치
# ============================================================
def patch_nbot_order(content):
    errors = []

    # ── import 추가 ────────────────────────────────────────────
    OLD_IMPORT = "try:\n    from master_db import record_trade as _master_record\nexcept Exception:\n    _master_record = None"
    NEW_IMPORT = (
        "try:\n"
        "    from master_db import (\n"
        "        record_trade    as _master_record,\n"
        "        upsert_position as _master_upsert,\n"
        "        remove_position as _master_remove,\n"
        "    )\nexcept Exception:\n"
        "    _master_record = None\n"
        "    _master_upsert = None\n"
        "    _master_remove = None"
    )
    if OLD_IMPORT in content:
        content = content.replace(OLD_IMPORT, NEW_IMPORT, 1)
        print("  ✅ [2-1] nbot_order import 추가")
    elif "_master_upsert" in content:
        print("  ⏭️  [2-1] 이미 패치됨 — 스킵")
    else:
        errors.append("nbot_order import 패치 대상 못 찾음")

    # ── do_buy 후 upsert_position 추가 ─────────────────────────
    OLD_BUY_END = (
        "        # ★ peak_tracker 즉시 초기화\n"
        "        self.peak_tracker[code] = {\n"
        "            \"peak_rate\":       0.0,\n"
        "            \"stage\":           0,\n"
        "            \"remain_qty\":      qty,\n"
        "            \"buy2_done\":       False,\n"
        "            \"buy1_price\":      price,\n"
        "            \"effective_entry\": price,\n"
        "        }"
    )
    NEW_BUY_END = (
        "        # ★ peak_tracker 즉시 초기화\n"
        "        self.peak_tracker[code] = {\n"
        "            \"peak_rate\":       0.0,\n"
        "            \"stage\":           0,\n"
        "            \"remain_qty\":      qty,\n"
        "            \"buy2_done\":       False,\n"
        "            \"buy1_price\":      price,\n"
        "            \"effective_entry\": price,\n"
        "        }\n\n"
        "        # ★ master_positions 등록 (대시보드/리스크매니저)\n"
        "        if _master_upsert:\n"
        "            ctx = self.buy_context.get(code, {})\n"
        "            _master_upsert(\n"
        "                bot_type    = \"nbot\",\n"
        "                code        = code,\n"
        "                stock_name  = self._name(code),\n"
        "                entry_price = price,\n"
        "                current_price = price,\n"
        "                qty         = qty,\n"
        "                buy_time    = ctx.get(\"buy_time\", \"\"),\n"
        "                buy_tag     = self.buy_tags.get(code, \"\"),\n"
        "                ai_score    = ctx.get(\"ai_score\", 0),\n"
        "            )"
    )
    if OLD_BUY_END in content:
        content = content.replace(OLD_BUY_END, NEW_BUY_END, 1)
        print("  ✅ [2-2] nbot do_buy 후 upsert_position 추가")
    elif "_master_upsert(\n                bot_type    = \"nbot\"" in content:
        print("  ⏭️  [2-2] 이미 패치됨 — 스킵")
    else:
        errors.append("nbot do_buy peak_tracker 블록 못 찾음")

    # ── do_sell 전량매도 시 remove_position / 분할매도 시 upsert ─
    OLD_SELL_FULL = (
        "        # ★ 전량 매도 시만 컨텍스트 정리\n"
        "        if is_full_sell:\n"
        "            self.buy_tags.pop(code, None)\n"
        "            self.buy_context.pop(code, None)\n"
        "            self.positions.pop(code, None)"
    )
    NEW_SELL_FULL = (
        "        # ★ 전량 매도 시만 컨텍스트 정리\n"
        "        if is_full_sell:\n"
        "            self.buy_tags.pop(code, None)\n"
        "            self.buy_context.pop(code, None)\n"
        "            self.positions.pop(code, None)\n"
        "            # ★ master_positions 삭제\n"
        "            if _master_remove:\n"
        "                _master_remove(\"nbot\", code)\n"
        "        else:\n"
        "            # ★ 분할매도: 잔량 갱신\n"
        "            remain = self.positions.get(code, {}).get(\"qty\", 0)\n"
        "            if _master_upsert and remain > 0:\n"
        "                _master_upsert(\n"
        "                    bot_type=\"nbot\", code=code,\n"
        "                    qty=remain,\n"
        "                    stage=self.peak_tracker.get(code, {}).get(\"stage\", 0),\n"
        "                )"
    )
    # 기존 else 절 제거 후 새 로직으로 교체
    OLD_SELL_ELSE = (
        "        else:\n"
        "            # 부분 매도: 잔량만 갱신\n"
        "            self.positions[code] = {\n"
        "                \"entry_price\": current_pos.get(\"entry_price\", sell_price),\n"
        "                \"qty\":         held_qty - qty,\n"
        "            }"
    )
    NEW_SELL_ELSE = (
        "        else:\n"
        "            # 부분 매도: 잔량만 갱신\n"
        "            self.positions[code] = {\n"
        "                \"entry_price\": current_pos.get(\"entry_price\", sell_price),\n"
        "                \"qty\":         held_qty - qty,\n"
        "            }\n"
        "            # ★ master_positions 잔량 갱신\n"
        "            if _master_upsert:\n"
        "                _master_upsert(\n"
        "                    bot_type=\"nbot\", code=code,\n"
        "                    qty=held_qty - qty,\n"
        "                    stage=self.peak_tracker.get(code, {}).get(\"stage\", 0),\n"
        "                )"
    )

    if OLD_SELL_FULL in content:
        content = content.replace(OLD_SELL_FULL, NEW_SELL_FULL, 1)
        print("  ✅ [2-3] nbot do_sell 전량/분할 remove/upsert 추가")
    elif "_master_remove(\"nbot\", code)" in content:
        print("  ⏭️  [2-3] 이미 패치됨 — 스킵")
    else:
        # 대안: else 절에 추가
        if OLD_SELL_ELSE in content:
            content = content.replace(OLD_SELL_ELSE, NEW_SELL_ELSE, 1)
            print("  ✅ [2-3] nbot do_sell else 분기에 upsert 추가")
        else:
            errors.append("nbot do_sell 전량/부분 분기 못 찾음")

    # ── record_trade 호출에 is_partial 추가 ───────────────────
    OLD_RECORD = (
        "        # ★ master_trades 기록 (전량 매도 시만)\n"
        "        if _master_record and is_full_sell:"
    )
    NEW_RECORD = (
        "        # ★ master_trades 기록 (전량 + 분할매도 모두)\n"
        "        if _master_record:"
    )
    if OLD_RECORD in content:
        content = content.replace(OLD_RECORD, NEW_RECORD, 1)
        # is_partial 인자 추가
        content = content.replace(
            "                    buy_tag=self.buy_tags.get(code, \"\"),\n                )\n            except Exception as _e:\n                print(f\"⚠️ master_db 기록 오류: {_e}\")",
            "                    buy_tag=self.buy_tags.get(code, \"\"),\n                    is_partial=not is_full_sell,\n                )\n            except Exception as _e:\n                print(f\"⚠️ master_db 기록 오류: {_e}\")",
            1
        )
        print("  ✅ [2-4] nbot record_trade 분할매도 포함으로 변경")
    elif "is_partial=not is_full_sell" in content:
        print("  ⏭️  [2-4] 이미 패치됨 — 스킵")
    else:
        errors.append("nbot record_trade 호출부 못 찾음")

    return content, errors


# ============================================================
# [3] sbot.py 패치
# ============================================================
def patch_sbot(content):
    errors = []

    # ── import 추가 ────────────────────────────────────────────
    OLD_IMPORT = "try:\n    from master_db import record_trade as _master_record\nexcept ImportError:\n    _master_record = None"
    NEW_IMPORT = (
        "try:\n"
        "    from master_db import (\n"
        "        record_trade    as _master_record,\n"
        "        upsert_position as _master_upsert,\n"
        "        remove_position as _master_remove,\n"
        "    )\nexcept ImportError:\n"
        "    _master_record = None\n"
        "    _master_upsert = None\n"
        "    _master_remove = None"
    )
    if OLD_IMPORT in content:
        content = content.replace(OLD_IMPORT, NEW_IMPORT, 1)
        print("  ✅ [3-1] sbot import 추가")
    elif "_master_upsert" in content:
        print("  ⏭️  [3-1] 이미 패치됨 — 스킵")
    else:
        errors.append("sbot import 패치 대상 못 찾음")

    # ── _do_buy 후 upsert_position 추가 ──────────────────────
    OLD_BUY = (
        "        self.peak_tracker[code] = {\n"
        "            \"peak_rate\":       0.0,\n"
        "            \"stage\":           0,\n"
        "            \"remain_qty\":      qty,\n"
        "            \"buy2_done\":       False,\n"
        "            \"buy1_price\":      price,\n"
        "            \"effective_entry\": price,\n"
        "        }\n"
        "        self.sold_today[code] = \"buying\""
    )
    NEW_BUY = (
        "        self.peak_tracker[code] = {\n"
        "            \"peak_rate\":       0.0,\n"
        "            \"stage\":           0,\n"
        "            \"remain_qty\":      qty,\n"
        "            \"buy2_done\":       False,\n"
        "            \"buy1_price\":      price,\n"
        "            \"effective_entry\": price,\n"
        "        }\n"
        "        self.sold_today[code] = \"buying\"\n\n"
        "        # ★ master_positions 등록\n"
        "        if _master_upsert:\n"
        "            ctx = self.buy_context.get(code, {})\n"
        "            _master_upsert(\n"
        "                bot_type=\"sbot\", code=code,\n"
        "                stock_name=self._name(code),\n"
        "                entry_price=price, current_price=price,\n"
        "                qty=qty, buy_time=ctx.get(\"buy_time\", \"\"),\n"
        "                buy_tag=ctx.get(\"buy_tag\", \"\"),\n"
        "                ai_score=ctx.get(\"ai_score\", 0),\n"
        "            )"
    )
    if OLD_BUY in content:
        content = content.replace(OLD_BUY, NEW_BUY, 1)
        print("  ✅ [3-2] sbot _do_buy 후 upsert_position 추가")
    elif "_master_upsert(\n                bot_type=\"sbot\"" in content:
        print("  ⏭️  [3-2] 이미 패치됨 — 스킵")
    else:
        errors.append("sbot _do_buy peak_tracker 블록 못 찾음")

    # ── _do_sell 전량/부분 분기에 remove/upsert 추가 ─────────
    OLD_SELL = (
        "        # ★ 핵심: 전량 매도일 때만 컨텍스트 정리\n"
        "        if is_full_sell:\n"
        "            self.buy_context.pop(code, None)\n"
        "            self.positions.pop(code, None)\n"
        "        else:\n"
        "            # 부분 매도: 잔량만 갱신 (entry_price 유지)\n"
        "            remain = held_qty - qty\n"
        "            self.positions[code] = {\n"
        "                \"entry_price\": current_pos.get(\"entry_price\", sell_price),\n"
        "                \"qty\":         remain,\n"
        "            }\n"
        "            # ★ peak_tracker 잔량 동기화\n"
        "            if code in self.peak_tracker:\n"
        "                self.peak_tracker[code][\"remain_qty\"] = remain\n"
        "                print(f\"🔄 peak_tracker 잔량 동기화: {code} → {remain}주\")"
    )
    NEW_SELL = (
        "        # ★ 핵심: 전량 매도일 때만 컨텍스트 정리\n"
        "        if is_full_sell:\n"
        "            self.buy_context.pop(code, None)\n"
        "            self.positions.pop(code, None)\n"
        "            # ★ master_positions 삭제\n"
        "            if _master_remove:\n"
        "                _master_remove(\"sbot\", code)\n"
        "        else:\n"
        "            # 부분 매도: 잔량만 갱신 (entry_price 유지)\n"
        "            remain = held_qty - qty\n"
        "            self.positions[code] = {\n"
        "                \"entry_price\": current_pos.get(\"entry_price\", sell_price),\n"
        "                \"qty\":         remain,\n"
        "            }\n"
        "            # ★ peak_tracker 잔량 동기화\n"
        "            if code in self.peak_tracker:\n"
        "                self.peak_tracker[code][\"remain_qty\"] = remain\n"
        "                print(f\"🔄 peak_tracker 잔량 동기화: {code} → {remain}주\")\n"
        "            # ★ master_positions 잔량 갱신\n"
        "            if _master_upsert:\n"
        "                _master_upsert(\n"
        "                    bot_type=\"sbot\", code=code,\n"
        "                    qty=remain,\n"
        "                    stage=self.peak_tracker.get(code, {}).get(\"stage\", 0),\n"
        "                )"
    )
    if OLD_SELL in content:
        content = content.replace(OLD_SELL, NEW_SELL, 1)
        print("  ✅ [3-3] sbot _do_sell remove/upsert 추가")
    elif "_master_remove(\"sbot\", code)" in content:
        print("  ⏭️  [3-3] 이미 패치됨 — 스킵")
    else:
        errors.append("sbot _do_sell 전량/부분 분기 못 찾음")

    # ── record_trade 분할매도 포함 ────────────────────────────
    OLD_RECORD = "        if _master_record and is_full_sell:"
    NEW_RECORD = "        if _master_record:  # 전량 + 분할매도 모두 기록"
    if OLD_RECORD in content:
        content = content.replace(OLD_RECORD, NEW_RECORD, 1)
        # is_partial 추가
        content = content.replace(
            "                    market_status=self.market_status,\n                    hold_days=hold_d,\n                )\n            except Exception as _e:\n                print(f\"⚠️ master_db 기록 오류: {_e}\")",
            "                    market_status=self.market_status,\n                    hold_days=hold_d,\n                    is_partial=not is_full_sell,\n                )\n            except Exception as _e:\n                print(f\"⚠️ master_db 기록 오류: {_e}\")",
            1
        )
        print("  ✅ [3-4] sbot record_trade 분할매도 포함으로 변경")
    elif "is_partial=not is_full_sell" in content:
        print("  ⏭️  [3-4] 이미 패치됨 — 스킵")
    else:
        errors.append("sbot record_trade 호출부 못 찾음")

    return content, errors


# ============================================================
# [4] cbot.py 패치
# ============================================================
def patch_cbot(content):
    errors = []

    # ── import 추가 ────────────────────────────────────────────
    OLD_IMPORT = "from master_db import record_trade as _master_record"
    NEW_IMPORT = (
        "try:\n"
        "    from master_db import (\n"
        "        record_trade    as _master_record,\n"
        "        upsert_position as _master_upsert,\n"
        "        remove_position as _master_remove,\n"
        "    )\nexcept Exception:\n"
        "    _master_record = None\n"
        "    _master_upsert = None\n"
        "    _master_remove = None"
    )
    if OLD_IMPORT in content and "_master_upsert" not in content:
        content = content.replace(OLD_IMPORT, NEW_IMPORT, 1)
        print("  ✅ [4-1] cbot import 추가")
    elif "_master_upsert" in content:
        print("  ⏭️  [4-1] 이미 패치됨 — 스킵")
    else:
        errors.append("cbot import 패치 대상 못 찾음")

    # ── _do_buy 후 upsert_position 추가 ──────────────────────
    OLD_BUY = "        self.positions[market] = {\n            \"entry_price\": buy_price,\n            \"qty\":         qty,\n            \"buy_time\":    now,\n        }"
    NEW_BUY = (
        "        self.positions[market] = {\n"
        "            \"entry_price\": buy_price,\n"
        "            \"qty\":         qty,\n"
        "            \"buy_time\":    now,\n"
        "        }\n"
        "        # ★ master_positions 등록\n"
        "        if _master_upsert:\n"
        "            _master_upsert(\n"
        "                bot_type=\"cbot\", code=market,\n"
        "                stock_name=market.replace(\"KRW-\",\"\"),\n"
        "                entry_price=buy_price, current_price=buy_price,\n"
        "                qty=int(qty * 1000),\n"
        "                buy_time=now,\n"
        "            )"
    )
    if OLD_BUY in content:
        content = content.replace(OLD_BUY, NEW_BUY, 1)
        print("  ✅ [4-2] cbot _do_buy 후 upsert_position 추가")
    elif "_master_upsert(\n                bot_type=\"cbot\"" in content:
        print("  ⏭️  [4-2] 이미 패치됨 — 스킵")
    else:
        errors.append("cbot _do_buy positions 블록 못 찾음")

    # ── _do_sell 후 remove_position 추가 ─────────────────────
    OLD_SELL_END = "        self.positions.pop(market, None)\n        self.sold_today[market] = now_hms()"
    NEW_SELL_END = (
        "        self.positions.pop(market, None)\n"
        "        self.sold_today[market] = now_hms()\n"
        "        # ★ master_positions 삭제\n"
        "        if _master_remove:\n"
        "            _master_remove(\"cbot\", market)"
    )
    if OLD_SELL_END in content:
        content = content.replace(OLD_SELL_END, NEW_SELL_END, 1)
        print("  ✅ [4-3] cbot _do_sell 후 remove_position 추가")
    elif "_master_remove(\"cbot\", market)" in content:
        print("  ⏭️  [4-3] 이미 패치됨 — 스킵")
    else:
        errors.append("cbot _do_sell positions.pop 블록 못 찾음")

    return content, errors


# ============================================================
# 메인
# ============================================================
def main():
    print("=" * 60)
    print("  master_db v2 연동 패치 시작")
    print("=" * 60)

    print("\n📂 파일 확인...")
    if not check_files():
        print("\n❌ 파일 없음. 경로 확인 후 재실행하세요.")
        sys.exit(1)

    all_errors = []

    # [1] master_db.py 교체
    patch_master_db()

    # [2] nbot_order.py
    print(f"\n🔧 [2/4] nbot_order.py 패치 중...")
    backup(NBOT_ORDER)
    content, errs = patch_nbot_order(read_file(NBOT_ORDER))
    all_errors += errs
    write_file(NBOT_ORDER, content)
    print("  💾 저장 완료")

    # [3] sbot.py
    print(f"\n🔧 [3/4] sbot.py 패치 중...")
    backup(SBOT_FILE)
    content, errs = patch_sbot(read_file(SBOT_FILE))
    all_errors += errs
    write_file(SBOT_FILE, content)
    print("  💾 저장 완료")

    # [4] cbot.py
    print(f"\n🔧 [4/4] cbot.py 패치 중...")
    backup(CBOT_FILE)
    content, errs = patch_cbot(read_file(CBOT_FILE))
    all_errors += errs
    write_file(CBOT_FILE, content)
    print("  💾 저장 완료")

    # 결과
    print("\n" + "=" * 60)
    if all_errors:
        print("⚠️  일부 패치 실패 (수동 확인 필요):")
        for e in all_errors:
            print(f"  - {e}")
    else:
        print("✅ 패치 완료!")
        print("\n다음 명령으로 전체 재시작:")
        print("  ./bot.sh restart")
        print("\n또는 개별 재시작:")
        print("  ./bot.sh restart nbot")
        print("  ./bot.sh restart sbot")
        print("  ./bot.sh restart cbot")
    print("=" * 60)


if __name__ == "__main__":
    main()
