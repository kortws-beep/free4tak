"""
coin_dip_sleeve.py — cbot 안의 "고정 4종목 눌림목 매수" 별도 주머니 (2026-10-08)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

cbot에서 노는 현금 중 100만원(25만 × 4)을 BTC·ETH·XRP·SOL에 아래 규칙으로 굴린다.
백테스트(backtest/dip_buy_backtest.py, 2023-10~2026-10) 최고 조합 그대로:
  · 진입: 지금가 ≤ 직전 7일(완성된 일봉) 고가 × 0.90
          + 어제 종가가 200일 이평선 위(상승 추세 중 눌림만 — 떨어지는 칼 회피)
  · 청산: +8% 익절 / 손절 없음 / 20일 보유하면 그날 종가 무렵(21일째 09시) 정리
  · 판 뒤에는 새 7일 고가가 한 번 나와야 다시 산다(같은 하락에 반복 매수 방지)
  · 일봉 날짜는 업비트 기준(매일 09:00 KST 시작)

cbot 본체와 섞이지 않게:
  · 이 주머니가 들고 있는 코인은 cbot 포지션 동기화·손절·트레일링에서 빠진다
    (cbot.get_current_positions가 held() 코인을 제외)
  · cbot이 이미 들고 있는 코인은 이 주머니가 사지 않고, 반대로 이 주머니가 든
    코인은 cbot이 사지 않는다(같은 코인 잔고가 섞이면 평균단가가 엉킴)
  · 손익은 cbot 당일손익·일손실 한도와 별개(trades가 아닌 dip_trades 테이블)
  · 아직 안 산 슬롯 몫(25만×빈 슬롯)은 cbot이 쓰지 못하게 남겨 둔다(reserved_krw)
끄기: .env에 DIP_SLEEVE_ENABLED=0
"""
import datetime
import sqlite3
import time

DIP_COINS = ["KRW-BTC", "KRW-ETH", "KRW-XRP", "KRW-SOL"]
SLOT_KRW = 250_000
LOOKBACK = 7
DIP = 0.10
TP = 0.08
MAX_HOLD = 20
TREND_MA = 200
FEE = 0.0005
CANDLE_TTL = 600            # 일봉 10분 캐시
MIN_ORDER = 5_000
BASE_URL = "https://api.upbit.com/v1"


# ── 순수 판단 (테스트 대상) ─────────────────────────────────
def daily_context(rows: list):
    """rows = [(date, open, high, low, close)] 오래된→최신, 마지막이 오늘(진행중) 봉.
    → {day, ref, trig, trend_ok, ma, today_high} / 자료 부족이면 None."""
    if len(rows) < max(LOOKBACK, TREND_MA) + 1:
        return None
    done, today = rows[:-1], rows[-1]
    ref = max(r[2] for r in done[-LOOKBACK:])
    ma = sum(r[4] for r in done[-TREND_MA:]) / TREND_MA
    return {"day": today[0], "ref": ref, "trig": ref * (1 - DIP), "ma": ma,
            "trend_ok": done[-1][4] > ma, "today_high": today[2]}


def _days(a: str, b: str) -> int:
    return (datetime.date.fromisoformat(b) - datetime.date.fromisoformat(a)).days


def decide(st: dict, ctx: dict, price: float) -> str:
    """코인 하나의 상태 st(제자리 갱신)와 오늘 일봉 맥락·현재가 → 할 일.
    반환: "buy" / "tp" / "expire" / "" (아무것도 안 함)."""
    if st.get("held"):
        entry = st.get("entry") or 0
        if entry > 0 and price >= entry * (1 + TP):
            return "tp"
        if _days(st["entry_day"], ctx["day"]) > MAX_HOLD:
            return "expire"
        return ""
    if st.get("blocked"):
        # 판 다음 날부터, 그날 고가가 새 7일 고가를 넘으면 해제 — 해제한 날은 안 산다
        if ctx["day"] > st.get("exit_day", "") and max(ctx["today_high"], price) >= ctx["ref"]:
            st["blocked"] = False
            st["no_buy_day"] = ctx["day"]
        return ""
    if st.get("no_buy_day") == ctx["day"]:
        return ""
    if ctx["trend_ok"] and price <= ctx["trig"]:
        return "buy"
    return ""


# ── 실제 운용 ──────────────────────────────────────────────
class DipSleeve:
    def __init__(self, bot, db_path: str, load_state, save_state, enabled: bool = True):
        """bot: CBot(session·_get_headers·get_balances·get_current_price·notify 사용)
        load_state()/save_state(dict): cbot_state.json의 'dip' 키 읽기/쓰기."""
        self.bot, self.db_path, self._save = bot, db_path, save_state
        self.enabled = enabled
        self.state = {m: {} for m in DIP_COINS}
        try:
            saved = load_state() or {}
            for m in DIP_COINS:
                if isinstance(saved.get(m), dict):
                    self.state[m] = saved[m]
        except Exception as e:
            print(f"⚠️ 눌림목 상태 복구 실패: {e}")
        self._candles = {}      # {market: (ts, rows)}
        self._init_db()

    # 상태 질의 — cbot 본체가 쓰는 것
    def held(self) -> set:
        return {m for m, st in self.state.items() if st.get("held")}

    def holds(self, market: str) -> bool:
        return bool(self.state.get(market, {}).get("held"))

    def reserved_krw(self) -> int:
        """아직 안 산 슬롯 몫 — cbot이 이만큼은 남겨 둔다."""
        if not self.enabled:
            return 0
        return SLOT_KRW * sum(1 for m in DIP_COINS if not self.holds(m))

    # DB
    def _init_db(self):
        try:
            conn = sqlite3.connect(self.db_path, timeout=10)
            conn.execute("""CREATE TABLE IF NOT EXISTS dip_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT, market TEXT, buy_time TEXT, buy_price REAL,
                qty REAL, amount REAL, ref_high REAL, sell_time TEXT, sell_price REAL,
                profit_rate REAL, profit_krw REAL, hold_days INTEGER, sell_reason TEXT)""")
            conn.commit(); conn.close()
        except Exception as e:
            print(f"⚠️ 눌림목 DB 초기화 실패: {e}")

    def _db(self, sql: str, args: tuple):
        try:
            conn = sqlite3.connect(self.db_path, timeout=10)
            cur = conn.execute(sql, args)
            conn.commit(); rid = cur.lastrowid; conn.close()
            return rid
        except Exception as e:
            print(f"⚠️ 눌림목 DB 기록 실패: {e}")
            return None

    # 시세
    def _fetch_daily(self, market: str) -> list:
        out, to = [], None
        for _ in range(2):                         # 200 + 200 → 200일선 + 오늘 봉 충분
            params = {"market": market, "count": 200}
            if to:
                params["to"] = to
            data = self.bot.session.get(f"{BASE_URL}/candles/days", params=params, timeout=5).json()
            if not isinstance(data, list) or not data:
                break
            out += [(c["candle_date_time_kst"][:10], float(c["opening_price"]), float(c["high_price"]),
                     float(c["low_price"]), float(c["trade_price"])) for c in data]
            to = data[-1]["candle_date_time_utc"].replace("T", " ")
            time.sleep(0.12)
        return sorted({r[0]: r for r in out}.values())

    def _rows(self, market: str) -> list:
        ts, rows = self._candles.get(market, (0, []))
        if time.time() - ts > CANDLE_TTL or not rows:
            try:
                rows = self._fetch_daily(market) or rows
                self._candles[market] = (time.time(), rows)
            except Exception as e:
                print(f"⚠️ 눌림목 일봉 조회 실패 {market}: {e}")
        return rows

    def _day(self, m: str) -> str:
        """업비트 일봉 날짜(09:00 KST 시작) — 받아 둔 일봉의 마지막 날, 없으면 KST로 계산."""
        rows = self._candles.get(m, (0, []))[1]
        if rows:
            return rows[-1][0]
        return datetime.datetime.now(datetime.timezone.utc).date().isoformat()   # KST 09시 = UTC 0시

    # 주문
    def _order(self, params: dict) -> bool:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        hdrs = self.bot._get_headers(qs)
        hdrs["Content-Type"] = "application/json"
        try:
            res = self.bot.session.post(f"{BASE_URL}/orders", headers=hdrs, json=params, timeout=5).json()
            if res.get("uuid"):
                return True
            print(f"❌ 눌림목 주문 실패 {params.get('market')}: {res.get('error', {}).get('message', res)}")
        except Exception as e:
            print(f"❌ 눌림목 주문 예외 {params.get('market')}: {e}")
        return False

    # 한 루프
    def step(self, cbot_positions: dict, krw: float, allow_buy: bool = True) -> bool:
        """cbot 메인루프에서 매 루프 호출. 주문을 냈으면 True(호출부가 KRW 다시 조회)."""
        if not self.enabled:
            return False
        acted, line = False, []
        balances = self.bot.get_balances()
        prices = self.bot.get_current_price(DIP_COINS)
        for m in DIP_COINS:
            st, px = self.state[m], prices.get(m, 0)
            coin = m.replace("KRW-", "")
            if st.get("held") and balances is not None:
                bal = balances.get(coin, {})
                qty = bal.get("balance", 0)
                if qty * (px or st.get("entry") or 0) < MIN_ORDER and time.time() - st.get("buy_ts", 0) > 120:
                    self._close(m, px or st.get("entry", 0), "외부매도(잔고없음)", qty=0, ordered=False)
                    acted = True
                    continue
                if bal.get("avg_buy_price"):
                    st["entry"] = bal["avg_buy_price"]      # 실제 체결 평균가로 갱신
                st["qty"] = qty
            if not px:
                continue
            ctx = daily_context(self._rows(m))
            if not ctx:
                line.append(f"{coin} 일봉부족")
                continue
            act = decide(st, ctx, px)
            if act == "buy":
                cp = cbot_positions.get(m)
                if cp and cp.get("qty", 0) * px >= MIN_ORDER:
                    line.append(f"{coin} 신호(cbot 보유중→패스)")
                    continue
                if not allow_buy:
                    line.append(f"{coin} 신호(일시중단→패스)")
                    continue
                if krw < SLOT_KRW:
                    line.append(f"{coin} 신호(잔고부족)")
                    continue
                if self._buy(m, px, ctx):
                    krw -= SLOT_KRW
                    acted = True
            elif act in ("tp", "expire"):
                if self._close(m, px, "익절+8%" if act == "tp" else f"기한{MAX_HOLD}일",
                               qty=st.get("qty", 0)):
                    acted = True
            st = self.state[m]                         # 매수/매도로 새 dict가 됐을 수 있음
            if st.get("held"):
                e = st.get("entry") or px
                line.append(f"{coin} 보유 {(px / e - 1) * 100:+.1f}% D{_days(st['entry_day'], ctx['day'])}")
            elif st.get("blocked"):
                line.append(f"{coin} 새고점대기")
            else:
                gap = (px / ctx["trig"] - 1) * 100
                line.append(f"{coin} {'추세X' if not ctx['trend_ok'] else f'트리거까지 {gap:+.1f}%'}")
        print("🪤 눌림목 | " + " · ".join(line))
        if acted:
            self._save(self.state)
        return acted

    def _buy(self, m: str, px: float, ctx: dict) -> bool:
        if not self._order({"market": m, "side": "bid", "price": str(SLOT_KRW), "ord_type": "price"}):
            return False
        qty = SLOT_KRW * (1 - FEE) / px
        now = datetime.datetime.now().isoformat(timespec="seconds")
        rid = self._db("INSERT INTO dip_trades (market, buy_time, buy_price, qty, amount, ref_high) "
                       "VALUES (?,?,?,?,?,?)", (m, now, px, qty, SLOT_KRW, ctx["ref"]))
        self.state[m] = {"held": True, "entry": px, "qty": qty, "entry_day": ctx["day"],
                         "buy_ts": time.time(), "row": rid, "ref": ctx["ref"]}
        self._save(self.state)
        self.bot.notify(f"🪤 [눌림목 매수] {m} | {SLOT_KRW:,}원 @ {px:,.0f}\n"
                        f"7일고가 {ctx['ref']:,.0f} 대비 {(px / ctx['ref'] - 1) * 100:+.1f}% · 200일선 위 | "
                        f"익절 +{TP:.0%}({px * (1 + TP):,.0f}) · 손절없음 · 최대 {MAX_HOLD}일", critical=True)
        return True

    def _close(self, m: str, px: float, reason: str, qty: float, ordered: bool = True) -> bool:
        st = self.state[m]
        if ordered:
            if qty <= 0:
                bal = (self.bot.get_balances() or {}).get(m.replace("KRW-", ""), {})
                qty = bal.get("balance", 0)
            if qty <= 0 or not self._order({"market": m, "side": "ask", "volume": f"{qty:.8f}",
                                            "ord_type": "market"}):
                return False
        entry = st.get("entry") or px
        rate = (px / entry - 1) - 2 * FEE if entry else 0.0
        krw = (st.get("qty") or qty) * entry * rate
        exit_day = self._day(m)
        days = _days(st["entry_day"], exit_day) if st.get("entry_day") else 0
        if st.get("row"):
            self._db("UPDATE dip_trades SET sell_time=?, sell_price=?, profit_rate=?, profit_krw=?, "
                     "hold_days=?, sell_reason=? WHERE id=?",
                     (datetime.datetime.now().isoformat(timespec="seconds"), px, rate * 100, krw,
                      days, reason, st["row"]))
        self.state[m] = {"blocked": True, "exit_day": exit_day}
        self._save(self.state)
        self.bot.notify(f"{'💰' if rate > 0 else '💔'} [눌림목 매도] {m} | {reason} | "
                        f"{rate * 100:+.2f}% ({krw:+,.0f}원) · 보유 {days}일\n"
                        f"→ 새 7일 고가가 나오면 다시 대기", critical=True)
        return True

    def summary(self) -> dict:
        return {m: {k: st[k] for k in ("entry", "qty", "entry_day") if k in st}
                for m, st in self.state.items() if st.get("held")}


if __name__ == "__main__":
    # 점검용(주문 없음): python core/coin_dip_sleeve.py → 4종목 지금 상태
    import requests

    class _Bot:
        session = requests.Session()
    s = object.__new__(DipSleeve)
    s.bot, s._candles = _Bot(), {}
    for m in DIP_COINS:
        r = s._rows(m)
        c = daily_context(r) if r else None
        if not c:
            print(f"{m} 일봉 부족({len(r)})")
            continue
        px = r[-1][4]
        print(f"{m:<8} 현재 {px:>14,.1f} | 7일고가 {c['ref']:,.1f} → 매수선 {c['trig']:,.1f} "
              f"({(px / c['trig'] - 1) * 100:+.1f}%) | 200일선 {c['ma']:,.1f} "
              f"{'위 ✅' if c['trend_ok'] else '아래 ❌(매수 안 함)'}")
