#!/usr/bin/env python3
"""
patch_vol_ratio.py — vol_ratio 2차 매수 실제 연동 자동 패치
================================================================
실행 방법:
    cd /home/free4tak/k-bot/stock_bot
    source venv/bin/activate
    python3 patch_vol_ratio.py

패치 내용:
    [1] core/sbot_strategy.py
        - check_sell() 시그니처에 vol_ratio 파라미터 추가
        - vol_ok = True 고정 → 실제 조건 (전일 대비 150% 이상) 으로 교체
        - 조건미달 로그에 거래량 정보 추가

    [2] bots/sbot.py
        - _get_vol_ratio() 메서드 신규 추가
          (sector_monitor.db → KIS vol_inrt → 0.0 순서 fallback)
        - _check_all_sells() 에서 vol_ratio 조회 후 check_sell 에 전달

백업: 각 파일 .bak 으로 자동 백업
================================================================
"""
import os
import sys
import shutil
import re

# ============================================================
# 경로 설정
# ============================================================
BASE = "/home/free4tak/k-bot/stock_bot"
STRATEGY_FILE = os.path.join(BASE, "core",  "sbot_strategy.py")
SBOT_FILE     = os.path.join(BASE, "bots",  "sbot.py")

def check_files():
    ok = True
    for f in [STRATEGY_FILE, SBOT_FILE]:
        if os.path.exists(f):
            print(f"  ✅ 발견: {f}")
        else:
            print(f"  ❌ 없음: {f}")
            ok = False
    return ok

def backup(filepath):
    bak = filepath + ".bak"
    shutil.copy2(filepath, bak)
    print(f"  💾 백업: {bak}")

def read_file(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        return f.read()

def write_file(filepath, content):
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

def patch_strategy(content):
    """sbot_strategy.py 패치"""
    errors = []

    # ── 패치 1: 시그니처에 vol_ratio 파라미터 추가 ──────────
    OLD_SIG = (
        "                   ma20: float = 0,\n"
        "                   atr_rate: float = 0) -> Optional[str]:"
    )
    NEW_SIG = (
        "                   ma20: float = 0,\n"
        "                   atr_rate: float = 0,\n"
        "                   vol_ratio: float = 0.0) -> Optional[str]:"
    )
    if OLD_SIG in content:
        content = content.replace(OLD_SIG, NEW_SIG, 1)
        print("  ✅ [1/4] 시그니처 vol_ratio 파라미터 추가")
    elif NEW_SIG in content:
        print("  ⏭️  [1/4] 시그니처 이미 패치됨 — 스킵")
    else:
        errors.append("시그니처 패치 대상을 찾지 못함")

    # ── 패치 2: vol_ratio 임시 로직 → 실제 조건 교체 ────────
    OLD_VOL = (
        "        # ★ 강화 조건: MA20 위 + 시장 normal + 거래량 1.5배↑\n"
        "        ma20_ok   = (ma20 > 0 and current >= ma20)\n"
        "        mkt_ok    = (market_status == \"normal\")\n"
        "        vol_ratio = float(atr_rate * 100) if atr_rate > 0 else 0  # 임시\n"
        "        vol_ok    = True  # vol_ratio는 market_data에 없으므로 일단 통과"
    )
    NEW_VOL = (
        "        # ★ 강화 조건: MA20 위 + 시장 normal + 거래량 1.5배↑\n"
        "        ma20_ok   = (ma20 > 0 and current >= ma20)\n"
        "        mkt_ok    = (market_status == \"normal\")\n"
        "        # ★ vol_ratio 실제 연동 (기존 True 고정 → 실제 조건)\n"
        "        # vol_ratio=0 이면 데이터 없음 → 조건 통과 (보수적 허용)\n"
        "        VOL_RATIO_MIN = 150.0   # 전일 대비 1.5배 이상 (150%)\n"
        "        vol_ok = (vol_ratio <= 0) or (vol_ratio >= VOL_RATIO_MIN)"
    )
    if OLD_VOL in content:
        content = content.replace(OLD_VOL, NEW_VOL, 1)
        print("  ✅ [2/4] vol_ok 실제 조건으로 교체")
    elif NEW_VOL in content:
        print("  ⏭️  [2/4] vol_ok 이미 패치됨 — 스킵")
    else:
        errors.append("vol_ok 패치 대상을 찾지 못함")

    # ── 패치 3: 2차 매수 조건 if 문에 vol_ok 추가 ───────────
    OLD_IF = (
        "                and not is_paused and not is_weak\n"
        "                and ma20_ok and mkt_ok):\n"
        "            print(f\"➕ 2차 매수(물타기) {code} | {buy2_rate:+.2%} | MA20:{ma20:,.0f}\")"
    )
    NEW_IF = (
        "                and not is_paused and not is_weak\n"
        "                and ma20_ok and mkt_ok and vol_ok):\n"
        "            print(f\"➕ 2차 매수(물타기) {code} | {buy2_rate:+.2%} | \"\n"
        "                  f\"MA20:{ma20:,.0f} | 거래량:{vol_ratio:.0f}%\")"
    )
    if OLD_IF in content:
        content = content.replace(OLD_IF, NEW_IF, 1)
        print("  ✅ [3/4] 2차 매수 조건 if문에 vol_ok 추가")
    elif NEW_IF in content:
        print("  ⏭️  [3/4] 이미 패치됨 — 스킵")
    else:
        errors.append("2차 매수 if 조건 패치 대상을 찾지 못함")

    # ── 패치 4: elif 조건미달 로그에 거래량 추가 ────────────
    OLD_ELIF = (
        "            reasons = []\n"
        "            if not ma20_ok: reasons.append(f\"MA20이탈({current:,.0f}<{ma20:,.0f})\")\n"
        "            if not mkt_ok:  reasons.append(f\"시장{market_status}\")\n"
        "            print(f\"⛔ 2차매수 조건미달 {code}: {', '.join(reasons)}\")"
    )
    NEW_ELIF = (
        "            reasons = []\n"
        "            if not ma20_ok: reasons.append(f\"MA20이탈({current:,.0f}<{ma20:,.0f})\")\n"
        "            if not mkt_ok:  reasons.append(f\"시장{market_status}\")\n"
        "            if not vol_ok:  reasons.append(f\"거래량부족({vol_ratio:.0f}%<{VOL_RATIO_MIN:.0f}%)\")\n"
        "            print(f\"⛔ 2차매수 조건미달 {code}: {', '.join(reasons)}\")"
    )
    if OLD_ELIF in content:
        content = content.replace(OLD_ELIF, NEW_ELIF, 1)
        print("  ✅ [4/4] 조건미달 로그에 거래량 추가")
    elif NEW_ELIF in content:
        print("  ⏭️  [4/4] 이미 패치됨 — 스킵")
    else:
        errors.append("elif 로그 패치 대상을 찾지 못함")

    return content, errors


def patch_sbot(content):
    """sbot.py 패치"""
    errors = []

    # ── 패치 5: _get_vol_ratio 메서드 추가 (이미 있으면 스킵) ──
    GET_VOL_METHOD = '''    def _get_vol_ratio(self, code: str, mdata: dict) -> float:
        """
        거래량 전일 대비 비율(%) 조회.

        우선순위:
          1. sector_monitor.db stock_momentum.vol_ratio (30초 실시간)
          2. KIS API mdata["vol_inrt"] (거래량 전일비 %)
          3. 0.0 반환 (데이터 없음 → check_sell 에서 조건 통과)

        캐시: 30초
        """
        now_ts = time.time()
        if not hasattr(self, "_vol_ratio_cache"):
            self._vol_ratio_cache = {}
        cached = self._vol_ratio_cache.get(code)
        if cached and now_ts - cached[1] < 30:
            return cached[0]

        # ── 우선순위 1: sector_monitor.db ─────────────────
        try:
            import sqlite3 as _sl
            _sm_db = "/home/free4tak/k-bot/stock_bot/intelligence/sector_monitor.db"
            if not os.path.exists(_sm_db):
                _sm_db = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)),
                    "..", "intelligence", "sector_monitor.db"
                )
            if os.path.exists(_sm_db):
                _conn = _sl.connect(_sm_db, timeout=3)
                _conn.execute("PRAGMA query_only = ON")
                row = _conn.execute("""
                    SELECT vol_ratio FROM stock_momentum
                    WHERE code = ?
                    ORDER BY ts DESC LIMIT 1
                """, (code,)).fetchone()
                _conn.close()
                if row and row[0] and float(row[0]) > 0:
                    vr = float(row[0])
                    self._vol_ratio_cache[code] = (vr, now_ts)
                    return vr
        except Exception as _e:
            print(f"⚠️ sector_monitor vol_ratio 조회 오류 {code}: {_e}")

        # ── 우선순위 2: KIS API mdata vol_inrt ────────────
        # vol_inrt: 거래량 전일 대비 증감율(%)
        # 증감율 50% → vol_ratio 150% (전일 대비 1.5배)
        try:
            vi = float(mdata.get("vol_inrt", 0) or 0)
            if vi != 0:
                vr = 100.0 + vi
                self._vol_ratio_cache[code] = (vr, now_ts)
                return vr
        except Exception:
            pass

        # ── 우선순위 3: 데이터 없음 ───────────────────────
        self._vol_ratio_cache[code] = (0.0, now_ts)
        return 0.0

'''

    if "_get_vol_ratio" in content:
        print("  ⏭️  [5/6] _get_vol_ratio 이미 존재 — 스킵")
    else:
        # _check_all_sells 바로 앞에 삽입
        TARGET = "    def _check_all_sells(self, pos_mkt_cache: dict):"
        if TARGET in content:
            content = content.replace(TARGET, GET_VOL_METHOD + TARGET, 1)
            print("  ✅ [5/6] _get_vol_ratio 메서드 추가")
        else:
            errors.append("_check_all_sells 메서드를 찾지 못함 — _get_vol_ratio 삽입 실패")

    # ── 패치 6: _check_all_sells 에서 vol_ratio 조회 + 전달 ──
    OLD_CALL = (
        "            # ★ 스윙봇 — market_status \"normal\" 고정\n"
        "            # 약세/stop 모드 손절선 축소(-3%) 방지 → 원래 손절선(-7%) 유지\n"
        "            self.strategy.check_sell(\n"
        "                code, pos, mdata, \"normal\",\n"
        "                self.peak_tracker, self._is_paused,\n"
        "                lambda c, p, a: self._do_buy(c, p, a, is_second=True),\n"
        "                lambda c, q, r, sp: self._do_sell(c, q, r, sp),\n"
        "                self._do_loss,\n"
        "                ma20=ma20, atr_rate=atr_rate,\n"
        "            )"
    )
    NEW_CALL = (
        "            # ★ vol_ratio 실제 조회 (sector_monitor.db → KIS API 순서)\n"
        "            vol_ratio = self._get_vol_ratio(code, mdata)\n"
        "            # ★ 스윙봇 — market_status \"normal\" 고정\n"
        "            # 약세/stop 모드 손절선 축소(-3%) 방지 → 원래 손절선(-7%) 유지\n"
        "            self.strategy.check_sell(\n"
        "                code, pos, mdata, \"normal\",\n"
        "                self.peak_tracker, self._is_paused,\n"
        "                lambda c, p, a: self._do_buy(c, p, a, is_second=True),\n"
        "                lambda c, q, r, sp: self._do_sell(c, q, r, sp),\n"
        "                self._do_loss,\n"
        "                ma20=ma20, atr_rate=atr_rate,\n"
        "                vol_ratio=vol_ratio,\n"
        "            )"
    )
    if OLD_CALL in content:
        content = content.replace(OLD_CALL, NEW_CALL, 1)
        print("  ✅ [6/6] _check_all_sells 에 vol_ratio 조회 + 전달 추가")
    elif NEW_CALL in content:
        print("  ⏭️  [6/6] 이미 패치됨 — 스킵")
    else:
        errors.append("_check_all_sells check_sell 호출부를 찾지 못함")

    return content, errors


# ============================================================
# 메인 실행
# ============================================================
def main():
    print("=" * 60)
    print("  vol_ratio 2차 매수 실제 연동 패치 시작")
    print("=" * 60)

    # 파일 존재 확인
    print("\n📂 파일 확인...")
    if not check_files():
        print("\n❌ 파일을 찾지 못했습니다. 경로를 확인하세요.")
        sys.exit(1)

    all_errors = []

    # ── sbot_strategy.py 패치 ────────────────────────────────
    print(f"\n🔧 [1/2] sbot_strategy.py 패치 중...")
    backup(STRATEGY_FILE)
    content = read_file(STRATEGY_FILE)
    content, errs = patch_strategy(content)
    all_errors += errs
    if not errs or all(("이미" in e or "스킵" in e) for e in errs):
        write_file(STRATEGY_FILE, content)
        print("  💾 저장 완료")

    # ── sbot.py 패치 ─────────────────────────────────────────
    print(f"\n🔧 [2/2] sbot.py 패치 중...")
    backup(SBOT_FILE)
    content = read_file(SBOT_FILE)
    content, errs = patch_sbot(content)
    all_errors += errs
    if not errs or all(("이미" in e or "스킵" in e) for e in errs):
        write_file(SBOT_FILE, content)
        print("  💾 저장 완료")

    # ── 결과 출력 ────────────────────────────────────────────
    print("\n" + "=" * 60)
    if all_errors:
        print("⚠️  일부 패치 실패:")
        for e in all_errors:
            print(f"  - {e}")
        print("\n수동으로 확인이 필요합니다.")
        print("백업 파일(.bak)으로 복원 가능합니다.")
    else:
        print("✅ 패치 완료!")
        print("\n다음 명령으로 sbot 재시작:")
        print("  ./bot.sh restart sbot")
        print("\n로그 확인:")
        print("  ./bot.sh log sbot")
    print("=" * 60)


if __name__ == "__main__":
    main()
