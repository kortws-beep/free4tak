"""
account_sync.py — 실계좌 ↔ DB 정합성 자동 체크/정리
================================================================
[하는 일]
봇 시작 시 실계좌와 DB 미매도 레코드를 비교해서:
1. 실계좌에 없는 DB 레코드 → 자동 삭제 (이미 매도된 것)
2. DB에 없는 실계좌 종목   → 경고 알림 (수동 매수된 것)
3. 수량 불일치             → 수량 보정

[지원 봇]
- sbot       : sync_positions()       — KIS API 기준 (한국투자증권)
- cbot       : sync_positions_upbit() — 업비트 API 기준
- sbo2       : 미사용 (자체 _sync_real_positions() 로직 보유)

[사용법]
from account_sync import sync_positions
synced = sync_positions(kis_api, "sbot_trade_history.db", notify_fn, bot_type="sbot")

from account_sync import sync_positions_upbit
synced = sync_positions_upbit(cbot_instance, "cbot_trade_history.db", notify_fn)
"""
import sqlite3
import os
from typing import Callable


def sync_positions(
    api,
    db_path: str,
    notify_fn: Callable,
    bot_type: str = "sbot",
) -> dict:
    """
    실계좌 ↔ DB 정합성 체크 및 자동 정리. (KIS API 전용 — sbot)
    반환: 정리된 실계좌 포지션 dict
    """
    print(f"🔍 [{bot_type}] 실계좌 ↔ DB 정합성 체크 중...")

    # 1. 실계좌 조회 (토큰 빈값이면 재발급 후 재시도)
    import time as _time
    if not api.token:
        print("⚠️ 토큰 없음 - 재발급 시도")
        for _i in range(3):
            api.token = api._issue_token()
            if api.token:
                break
            print(f"   재발급 대기 ({_i+1}/3)...")
            _time.sleep(65)
        if not api.token:
            print("❌ 토큰 발급 실패 - account_sync 스킵")
            return {}
    try:
        real_pos = api.get_current_positions()
    except Exception as e:
        print(f"⚠️ 실계좌 조회 실패: {e}")
        return {}

    print(f"   실계좌: {len(real_pos)}종목 — {list(real_pos.keys())}")

    # 2. DB 미매도 레코드 조회
    db_pos = {}
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        rows = conn.execute("""
            SELECT id, code, buy_price, qty
            FROM trades
            WHERE sell_price IS NULL
            ORDER BY buy_time
        """).fetchall()
        conn.close()
        for row_id, code, buy_price, qty in rows:
            if code not in db_pos:
                db_pos[code] = []
            db_pos[code].append((row_id, buy_price, qty))
    except Exception as e:
        print(f"⚠️ DB 조회 실패: {e}")
        return real_pos

    print(f"   DB 미매도: {len(db_pos)}종목 — {list(db_pos.keys())}")

    # 3. 실계좌에 없는 DB 레코드 삭제
    ghost_codes = [c for c in db_pos if c not in real_pos]
    if ghost_codes:
        try:
            conn = sqlite3.connect(db_path, timeout=10)
            for code in ghost_codes:
                ids = [str(r[0]) for r in db_pos[code]]
                conn.execute(
                    f"DELETE FROM trades WHERE id IN ({','.join(ids)})"
                )
                print(f"   🗑️ DB 정리: {code} ({len(ids)}건) — 실계좌에 없음")
            conn.commit()
            conn.close()
            notify_fn(
                f"🔧 [{bot_type}] DB 정합성 정리\n"
                f"실계좌에 없는 미매도 레코드 삭제: {', '.join(ghost_codes)}",
                critical=False,
            )
        except Exception as e:
            print(f"⚠️ DB 정리 실패: {e}")

    # 4. 실계좌에 있는데 DB에 없는 종목 → 경고 + 자동 추가
    missing_codes = [c for c in real_pos if c not in db_pos]
    if missing_codes:
        print(f"   ⚠️ DB 누락 종목: {missing_codes} (수동매수 또는 기록 누락)")
        notify_fn(
            f"⚠️ [{bot_type}] DB 누락 종목 발견\n"
            f"{', '.join(missing_codes)} — 수동매수 또는 기록 누락\n"
            f"매도 체크는 정상 작동하나 복기/성과에서 누락될 수 있어요",
            critical=False,
        )
        # ★ DB에 자동 추가 (손절 체크 정상화)
        try:
            conn = sqlite3.connect(db_path, timeout=10)
            for _c in missing_codes:
                _entry = real_pos[_c].get("entry_price", 0)
                _qty   = real_pos[_c].get("qty", 0)
                if _entry > 0 and _qty > 0:
                    conn.execute(
                        "INSERT INTO trades (code, buy_price, qty, buy_time) "
                        "VALUES (?,?,?,datetime('now','localtime'))",
                        (_c, _entry, _qty)
                    )
                    print(f"   ✅ DB 자동 추가: {_c} ({_entry:,.0f}원 × {_qty}주)")
            conn.commit()
            conn.close()
        except Exception as _e:
            print(f"⚠️ DB 자동 추가 오류: {_e}")

    # 5. 수량 불일치 체크
    for code in real_pos:
        if code not in db_pos:
            continue
        real_qty = real_pos[code].get("qty", 0)
        db_qty   = sum(r[2] for r in db_pos[code])
        if real_qty != db_qty:
            print(f"   ⚠️ 수량 불일치 {code}: 실계좌={real_qty} DB={db_qty}")
            # 수량 보정 (가장 최근 레코드 수량을 실계좌 기준으로)
            try:
                conn = sqlite3.connect(db_path, timeout=10)
                last_id = db_pos[code][-1][0]
                # 오래된 레코드 삭제 후 최신 레코드 수량 보정
                if len(db_pos[code]) > 1:
                    old_ids = [str(r[0]) for r in db_pos[code][:-1]]
                    conn.execute(
                        f"DELETE FROM trades WHERE id IN ({','.join(old_ids)})"
                    )
                conn.execute(
                    "UPDATE trades SET qty=? WHERE id=?",
                    (real_qty, last_id)
                )
                conn.commit()
                conn.close()
                print(f"   ✅ 수량 보정: {code} {db_qty}주 → {real_qty}주")
            except Exception as e:
                print(f"⚠️ 수량 보정 실패: {e}")

    print(f"✅ [{bot_type}] DB 정합성 체크 완료")

    # ★ buy_date 복원 — DB trades 테이블의 buy_time 기준
    import datetime as _dt_sync
    _today = _dt_sync.date.today()
    MAX_HOLD_DAYS = 30  # 30일 이상 된 레코드는 오늘 날짜로 리셋 (오래된 DB 오염 방어)
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        # ★ trades 테이블 없으면 스킵
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        if "trades" not in tables:
            conn.close()
            return real_pos
        for code in real_pos:
            row = conn.execute("""
                SELECT buy_time FROM trades
                WHERE code=? AND sell_price IS NULL
                ORDER BY buy_time ASC LIMIT 1
            """, (code,)).fetchone()
            if row and row[0]:
                buy_date_str = str(row[0])[:10]
                try:
                    buy_dt = _dt_sync.date.fromisoformat(buy_date_str)
                    days_held = (_today - buy_dt).days
                    if days_held > MAX_HOLD_DAYS:
                        # 너무 오래된 레코드 → 오늘 날짜로 리셋
                        real_pos[code]["buy_date"] = _today.isoformat()
                        print(f"   ⚠️ buy_date 오염 감지: {code} ({buy_date_str}, {days_held}일) → 오늘로 리셋")
                    else:
                        real_pos[code]["buy_date"] = buy_date_str
                        print(f"   📅 buy_date 복원: {code} → {buy_date_str} ({days_held}일 보유)")
                except Exception:
                    real_pos[code]["buy_date"] = _today.isoformat()
                    print(f"   ⚠️ buy_date 파싱 실패: {code} → 오늘로 리셋")
            else:
                # DB에 레코드 없으면 오늘 날짜
                real_pos[code]["buy_date"] = _today.isoformat()
                print(f"   📅 buy_date 없음: {code} → 오늘로 설정")
        conn.close()
    except Exception as e:
        print(f"⚠️ buy_date 복원 오류: {e}")

    return real_pos


# ============================================================
# cbot(업비트) 전용 동기화
# ============================================================
def sync_positions_upbit(
    cbot,
    db_path: str,
    notify_fn: Callable,
    bot_type: str = "cbot",
) -> dict:
    """
    실계좌(업비트) ↔ DB 정합성 체크 및 자동 정리. (cbot 전용)

    ★ sync_positions()(KIS 전용)와 분리한 이유:
    - cbot은 KisAPI 인스턴스가 없음 — 업비트 REST API + JWT 인증 사용
      (api.token / api._issue_token() 같은 KIS 전용 속성이 존재하지 않음)
    - cbot의 trades 테이블은 종목 컬럼명이 code가 아니라 market
      (예: "KRW-BTC") — 동일 SQL을 그대로 재사용할 수 없음
    - 과거엔 cbot.run()이 sync_positions(self, ...)로 KIS용 함수를
      그대로 호출해 api.token 접근 시 AttributeError가 발생, 매번
      try/except로 조용히 무시되어 사실상 한 번도 동작하지 않았음

    cbot: CoinBot 인스턴스. get_current_positions()를 제공해야 함
          (반환 형식: {"KRW-BTC": {"entry_price", "qty", ...}, ...})
    반환: 정리된 실계좌 포지션 dict
    """
    print(f"🔍 [{bot_type}] 실계좌(업비트) ↔ DB 정합성 체크 중...")

    # 1. 실계좌(업비트) 조회 — 업비트는 JWT를 매 요청마다 새로 만들어
    #    쓰므로 KIS처럼 토큰 캐시/재발급 로직이 필요 없음
    try:
        real_pos = cbot.get_current_positions()
    except Exception as e:
        print(f"⚠️ 실계좌 조회 실패: {e}")
        return {}

    print(f"   실계좌: {len(real_pos)}종목 — {list(real_pos.keys())}")

    # 2. DB 미매도 레코드 조회 (★ market 컬럼 — code 아님)
    db_pos = {}
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        rows = conn.execute("""
            SELECT id, market, buy_price, qty
            FROM trades
            WHERE sell_price IS NULL
            ORDER BY buy_time
        """).fetchall()
        conn.close()
        for row_id, market, buy_price, qty in rows:
            if market not in db_pos:
                db_pos[market] = []
            db_pos[market].append((row_id, buy_price, qty))
    except Exception as e:
        print(f"⚠️ DB 조회 실패: {e}")
        return real_pos

    print(f"   DB 미매도: {len(db_pos)}종목 — {list(db_pos.keys())}")

    # 3. 실계좌에 없는 DB 레코드 삭제
    ghost_markets = [m for m in db_pos if m not in real_pos]
    if ghost_markets:
        try:
            conn = sqlite3.connect(db_path, timeout=10)
            for market in ghost_markets:
                ids = [str(r[0]) for r in db_pos[market]]
                conn.execute(
                    f"DELETE FROM trades WHERE id IN ({','.join(ids)})"
                )
                print(f"   🗑️ DB 정리: {market} ({len(ids)}건) — 실계좌에 없음")
            conn.commit()
            conn.close()
            notify_fn(
                f"🔧 [{bot_type}] DB 정합성 정리\n"
                f"실계좌에 없는 미매도 레코드 삭제: {', '.join(ghost_markets)}",
                critical=False,
            )
        except Exception as e:
            print(f"⚠️ DB 정리 실패: {e}")

    # 4. 실계좌에 있는데 DB에 없는 코인 → 경고 + 자동 추가
    missing_markets = [m for m in real_pos if m not in db_pos]
    if missing_markets:
        print(f"   ⚠️ DB 누락 종목: {missing_markets} (수동매수 또는 기록 누락)")
        notify_fn(
            f"⚠️ [{bot_type}] DB 누락 종목 발견\n"
            f"{', '.join(missing_markets)} — 수동매수 또는 기록 누락\n"
            f"매도 체크는 정상 작동하나 복기/성과에서 누락될 수 있어요",
            critical=False,
        )
        try:
            conn = sqlite3.connect(db_path, timeout=10)
            for _m in missing_markets:
                _entry = real_pos[_m].get("entry_price", 0)
                _qty   = real_pos[_m].get("qty", 0)
                if _entry > 0 and _qty > 0:
                    conn.execute(
                        "INSERT INTO trades (market, buy_price, qty, buy_time) "
                        "VALUES (?,?,?,datetime('now','localtime'))",
                        (_m, _entry, _qty)
                    )
                    print(f"   ✅ DB 자동 추가: {_m} ({_entry:,.0f}원 × {_qty})")
            conn.commit()
            conn.close()
        except Exception as _e:
            print(f"⚠️ DB 자동 추가 오류: {_e}")

    # 5. 수량 불일치 체크 (코인은 소수점 보유 가능 — 부동소수 오차 허용)
    QTY_TOLERANCE = 1e-6
    for market in real_pos:
        if market not in db_pos:
            continue
        real_qty = real_pos[market].get("qty", 0)
        db_qty   = sum(r[2] for r in db_pos[market])
        if abs(real_qty - db_qty) > QTY_TOLERANCE:
            print(f"   ⚠️ 수량 불일치 {market}: 실계좌={real_qty} DB={db_qty}")
            try:
                conn = sqlite3.connect(db_path, timeout=10)
                last_id = db_pos[market][-1][0]
                if len(db_pos[market]) > 1:
                    old_ids = [str(r[0]) for r in db_pos[market][:-1]]
                    conn.execute(
                        f"DELETE FROM trades WHERE id IN ({','.join(old_ids)})"
                    )
                conn.execute(
                    "UPDATE trades SET qty=? WHERE id=?",
                    (real_qty, last_id)
                )
                conn.commit()
                conn.close()
                print(f"   ✅ 수량 보정: {market} {db_qty} → {real_qty}")
            except Exception as e:
                print(f"⚠️ 수량 보정 실패: {e}")

    print(f"✅ [{bot_type}] DB 정합성 체크 완료")

    # ★ buy_date 복원 — DB trades 테이블의 buy_time 기준
    import datetime as _dt_sync
    _today = _dt_sync.date.today()
    MAX_HOLD_DAYS = 30
    try:
        conn = sqlite3.connect(db_path, timeout=10)
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        if "trades" not in tables:
            conn.close()
            return real_pos
        for market in real_pos:
            row = conn.execute("""
                SELECT buy_time FROM trades
                WHERE market=? AND sell_price IS NULL
                ORDER BY buy_time ASC LIMIT 1
            """, (market,)).fetchone()
            if row and row[0]:
                buy_date_str = str(row[0])[:10]
                try:
                    buy_dt = _dt_sync.date.fromisoformat(buy_date_str)
                    days_held = (_today - buy_dt).days
                    if days_held > MAX_HOLD_DAYS:
                        real_pos[market]["buy_date"] = _today.isoformat()
                        print(f"   ⚠️ buy_date 오염 감지: {market} ({buy_date_str}, {days_held}일) → 오늘로 리셋")
                    else:
                        real_pos[market]["buy_date"] = buy_date_str
                        print(f"   📅 buy_date 복원: {market} → {buy_date_str} ({days_held}일 보유)")
                except Exception:
                    real_pos[market]["buy_date"] = _today.isoformat()
                    print(f"   ⚠️ buy_date 파싱 실패: {market} → 오늘로 리셋")
            else:
                real_pos[market]["buy_date"] = _today.isoformat()
                print(f"   📅 buy_date 없음: {market} → 오늘로 설정")
        conn.close()
    except Exception as e:
        print(f"⚠️ buy_date 복원 오류: {e}")

    return real_pos
