"""
backtest.py — 영암9 단타봇 백테스터
================================================================
[이 파일이 하는 일]

과거 주가 데이터를 가져와서 단타봇 전략을 시뮬레이션합니다.
실제 매매 없이 "과거에 이렇게 했으면 어땠을까?"를 계산합니다.

[사용법]
  # 1) 데이터 수집 (처음 한 번만)
  python3 backtest.py --fetch

  # 2) 백테스트 실행
  python3 backtest.py --run

  # 3) 시나리오 비교 (파라미터 최적화)
  python3 backtest.py --compare

  # 4) 전체 (수집 + 실행 + 비교)
  python3 backtest.py --all

[핵심 원칙]
  - strategy.py 그대로 재사용 (전략 코드 중복 없음)
  - AI 점수는 고정값(70점)으로 가정 (비용 절감)
  - 수수료 0.015% + 슬리피지 0.05% 반영
  - Look-ahead Bias 방지 (미래 데이터 안 씀)
"""

import os
import sys
import time
import json
import sqlite3
import argparse
import datetime
import requests
from dotenv import load_dotenv

load_dotenv()

# ── 경로 설정 ──────────────────────────────────────────────
SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
DATA_DB      = os.path.join(SCRIPT_DIR, "backtest_data.db")
RESULT_DB    = os.path.join(SCRIPT_DIR, "backtest_result.db")

# ── 백테스트 기본 설정 ─────────────────────────────────────
DEFAULT_START      = "2024-01-01"
DEFAULT_END        = datetime.date.today().strftime("%Y-%m-%d")
INITIAL_CASH       = 5_000_000     # 500만원 시드
BUY_AMOUNT         = 200_000       # 종목당 20만원
MAX_POSITIONS      = 5             # 최대 5종목
FEE_RATE           = 0.00015       # 수수료 0.015%
SELL_TAX_RATE      = 0.0023        # 매도세 0.23% (코스피)
SLIPPAGE_RATE      = 0.0005        # 슬리피지 0.05%
AI_SCORE_FIXED     = 70            # AI 점수 고정값

# ── 데이터 수집 설정 ───────────────────────────────────────
FETCH_SLEEP        = 0.12          # API 호출 간격 (rate limit 방지)
MAX_STOCKS         = 200           # 수집할 종목 수


# ============================================================
# 공통 DB 헬퍼
# ============================================================
def db_connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=15)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


# ============================================================
# 1. 데이터 수집
# ============================================================
class DataFetcher:
    """KIS API로 과거 데이터 수집 → backtest_data.db 저장"""

    def __init__(self):
        self.appkey   = os.getenv("KIS_APPKEY", "")
        self.secret   = os.getenv("KIS_SECRET", "")
        self.base_url = "https://openapi.koreainvestment.com:9443"
        self.token    = ""
        self._init_db()

    def _init_db(self):
        conn = db_connect(DATA_DB)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_ohlcv (
                code        TEXT NOT NULL,
                date        TEXT NOT NULL,
                open        INTEGER DEFAULT 0,
                high        INTEGER DEFAULT 0,
                low         INTEGER DEFAULT 0,
                close       INTEGER DEFAULT 0,
                volume      INTEGER DEFAULT 0,
                value       REAL    DEFAULT 0,  -- 거래대금 (억원)
                change_rate REAL    DEFAULT 0,
                PRIMARY KEY (code, date)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS stock_list (
                code        TEXT PRIMARY KEY,
                name        TEXT,
                market      TEXT,
                fetched_at  TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ohlcv_code ON daily_ohlcv(code)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ohlcv_date ON daily_ohlcv(date)")
        conn.commit(); conn.close()
        print(f"✅ 데이터 DB 초기화 ({DATA_DB})")

    def get_token(self) -> str:
        if self.token:
            return self.token
        res = requests.post(
            f"{self.base_url}/oauth2/tokenP",
            json={"grant_type": "client_credentials",
                  "appkey": self.appkey, "appsecret": self.secret},
            timeout=10,
        ).json()
        self.token = res.get("access_token", "")
        if self.token:
            print("✅ 한투 토큰 발급")
        else:
            print("❌ 토큰 발급 실패:", res.get("msg1", ""))
        return self.token

    def get_headers(self, tr_id: str) -> dict:
        return {
            "authorization": f"Bearer {self.get_token()}",
            "appkey":        self.appkey,
            "appsecret":     self.secret,
            "tr_id":         tr_id,
        }

    def fetch_stock_list(self) -> list:
        """코스피/코스닥 전체 종목 목록"""
        codes = []
        conn  = db_connect(DATA_DB)

        for market in ["J", "Q"]:  # J=코스피, Q=코스닥
            try:
                res = requests.get(
                    f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-price",
                    headers={**self.get_headers("FHKST01010100"),
                             "custtype": "P"},
                    params={"FID_COND_MRKT_DIV_CODE": market,
                            "FID_INPUT_ISCD": "0000"},
                    timeout=10,
                ).json()

                # 거래량 순위로 종목 리스트 수집
                res2 = requests.get(
                    f"{self.base_url}/uapi/domestic-stock/v1/ranking/volume",
                    headers=self.get_headers("FHPST01710000"),
                    params={
                        "FID_COND_MRKT_DIV_CODE": market,
                        "FID_COND_SCR_DIV_CODE":  "20171",
                        "FID_INPUT_ISCD":          "0000",
                        "FID_DIV_CLS_CODE":        "0",
                        "FID_BLNG_CLS_CODE":       "0",
                        "FID_TRGT_CLS_CODE":       "111111111",
                        "FID_TRGT_EXLS_CLS_CODE":  "000000",
                        "FID_INPUT_PRICE_1":       "1000",
                        "FID_INPUT_PRICE_2":       "9999999",
                        "FID_VOL_CNT":             "100000",
                        "FID_INPUT_DATE_1":        "",
                    },
                    timeout=10,
                ).json()

                for item in res2.get("output", [])[:MAX_STOCKS]:
                    code = item.get("mksc_shrn_iscd", "").strip()
                    name = item.get("hts_kor_isnm", "").strip()
                    if code and code.isdigit():
                        codes.append((code, name, market))
                        conn.execute(
                            "INSERT OR IGNORE INTO stock_list VALUES (?,?,?,?)",
                            (code, name, market,
                             datetime.datetime.now().isoformat()[:10])
                        )

                conn.commit()
                print(f"✅ {market} 종목 {len([c for c in codes if c[2]==market])}개 수집")
                time.sleep(0.5)

            except Exception as e:
                print(f"⚠️ {market} 종목 리스트 오류: {e}")

        conn.close()
        return codes

    def fetch_daily_ohlcv(self, code: str, name: str,
                          start: str, end: str) -> int:
        """종목별 일봉 데이터 수집"""
        try:
            s_dt = start.replace("-", "")
            e_dt = end.replace("-", "")

            res = requests.get(
                f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
                headers=self.get_headers("FHKST03010100"),
                params={
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD":         code,
                    "FID_INPUT_DATE_1":       s_dt,
                    "FID_INPUT_DATE_2":       e_dt,
                    "FID_PERIOD_DIV_CODE":    "D",
                    "FID_ORG_ADJ_PRC":        "0",
                },
                timeout=10,
            ).json()

            candles = res.get("output2", [])
            if not candles:
                return 0

            conn = db_connect(DATA_DB)
            inserted = 0
            for c in candles:
                date  = c.get("stck_bsop_date", "")
                if not date or len(date) != 8:
                    continue
                date_fmt = f"{date[:4]}-{date[4:6]}-{date[6:]}"

                close  = int(c.get("stck_clpr",  0) or 0)
                open_  = int(c.get("stck_oprc",  0) or 0)
                high   = int(c.get("stck_hgpr",  0) or 0)
                low    = int(c.get("stck_lwpr",  0) or 0)
                vol    = int(c.get("acml_vol",   0) or 0)
                value  = float(c.get("acml_tr_pbmn", 0) or 0) / 1e8
                chg    = float(c.get("prdy_ctrt", 0) or 0)

                if close <= 0:
                    continue

                conn.execute("""
                    INSERT OR REPLACE INTO daily_ohlcv
                        (code, date, open, high, low, close, volume, value, change_rate)
                    VALUES (?,?,?,?,?,?,?,?,?)
                """, (code, date_fmt, open_, high, low, close, vol,
                      round(value, 2), round(chg, 2)))
                inserted += 1

            conn.commit(); conn.close()
            return inserted

        except Exception as e:
            print(f"  ⚠️ {code}({name}) 오류: {e}")
            return 0

    def run(self, start: str = DEFAULT_START, end: str = DEFAULT_END):
        """전체 데이터 수집"""
        print(f"\n📥 데이터 수집 시작: {start} ~ {end}")
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        # 1) 종목 리스트
        stocks = self.fetch_stock_list()
        if not stocks:
            print("❌ 종목 리스트 없음 — .env 확인")
            return

        # 2) 일봉 수집
        total = len(stocks)
        for i, (code, name, market) in enumerate(stocks):
            # 이미 수집된 건 스킵
            conn  = db_connect(DATA_DB)
            count = conn.execute(
                "SELECT COUNT(*) FROM daily_ohlcv WHERE code=? AND date>=?",
                (code, start)
            ).fetchone()[0]
            conn.close()

            if count > 200:
                print(f"  ⏭️ [{i+1}/{total}] {code}({name}) — 이미 수집됨({count}일)")
                continue

            cnt = self.fetch_daily_ohlcv(code, name, start, end)
            print(f"  ✅ [{i+1}/{total}] {code}({name}) — {cnt}일 저장")
            time.sleep(FETCH_SLEEP)

        # 3) 결과
        conn  = db_connect(DATA_DB)
        total_rows = conn.execute(
            "SELECT COUNT(*) FROM daily_ohlcv WHERE date>=?", (start,)
        ).fetchone()[0]
        total_codes = conn.execute(
            "SELECT COUNT(DISTINCT code) FROM daily_ohlcv WHERE date>=?", (start,)
        ).fetchone()[0]
        conn.close()

        print(f"\n🎉 수집 완료!")
        print(f"  종목: {total_codes}개 | 총 {total_rows:,}일 데이터")


# ============================================================
# 2. 백테스트 엔진
# ============================================================
class BacktestEngine:
    """
    과거 데이터를 날짜순으로 재생하며 매매 시뮬레이션.
    strategy.py의 get_rule_score() + passes_buy_filter() 재사용.
    """

    def __init__(self, params: dict = None):
        # 전략 파라미터
        p = params or {}
        self.sell_1st_rate    = p.get("sell_1st_rate",    0.05)
        self.sell_1st_qty     = p.get("sell_1st_qty",     0.30)
        self.sell_2nd_rate    = p.get("sell_2nd_rate",    0.10)
        self.sell_2nd_qty     = p.get("sell_2nd_qty",     0.40)
        self.stop_loss        = p.get("stop_loss",        -0.05)
        self.stop_after_1st   = p.get("stop_after_1st",  -0.02)
        self.stop_weak        = p.get("stop_weak",        -0.03)
        self.trail_after_1st  = p.get("trail_after_1st",  0.025)
        self.score_min        = p.get("score_min",         45)  # ★ 백테스트용 완화
        self.buy_amount       = p.get("buy_amount",   BUY_AMOUNT)
        self.max_positions    = p.get("max_positions", MAX_POSITIONS)
        self.ai_score_fixed   = p.get("ai_score_fixed", AI_SCORE_FIXED)

        # 포지션
        self.cash       = INITIAL_CASH
        self.positions  = {}   # {code: {entry, qty, stage, peak}}
        self.trades     = []   # 매매 이력
        self.equity_curve = [] # 일별 자산

    def _get_indicators(self, code: str, date: str, conn) -> dict:
        """해당 종목의 date 이전 데이터로 지표 계산 (Look-ahead Bias 방지)"""
        rows = conn.execute("""
            SELECT date, open, high, low, close, volume, value, change_rate
            FROM daily_ohlcv
            WHERE code=? AND date<=?
            ORDER BY date DESC LIMIT 70
        """, (code, date)).fetchall()

        if len(rows) < 20:
            return {}

        closes  = [r[4] for r in rows]
        volumes = [r[5] for r in rows]
        values  = [r[6] for r in rows]

        # MA
        ma5  = sum(closes[:5])  / 5  if len(closes) >= 5  else 0
        ma20 = sum(closes[:20]) / 20 if len(closes) >= 20 else 0
        ma60 = sum(closes[:60]) / 60 if len(closes) >= 60 else 0

        # RSI 14 (★ 0나누기 방지)
        rsi = 50.0
        if len(closes) >= 15:
            gains  = [closes[i]-closes[i+1] for i in range(14) if closes[i]>closes[i+1]]
            losses = [abs(closes[i]-closes[i+1]) for i in range(14) if closes[i]<=closes[i+1]]
            avg_g  = sum(gains)/14 if gains else 0
            avg_l  = sum(losses)/14 if losses else 0
            if avg_l == 0:
                rsi = 100.0 if avg_g > 0 else 50.0
            else:
                rsi = 100 - 100/(1 + avg_g/avg_l)

        # 거래량 증가율
        vol_today = volumes[0] if volumes else 0
        vol_avg   = sum(volumes[1:21])/20 if len(volumes) >= 21 else 1
        vol_ratio = vol_today / vol_avg * 100 if vol_avg > 0 else 0

        return {
            "close":        closes[0],
            "change_rate":  rows[0][7],
            "trading_value": values[0],  # 억원
            "volume_ratio": vol_ratio,
            "vol_tnrt":     0,
            "rsi":          round(rsi, 1),
            "ma5":          round(ma5, 2),
            "ma20":         round(ma20, 2),
            "ma60":         round(ma60, 2),
            "foreign_5d":   0,   # 외국인 데이터 없음 (DB에 없음)
            "institution_5d": 0,
        }

    def _calc_total_score(self, ind: dict) -> int:
        """룰 점수 + AI 고정점수 결합"""
        from strategy import Strategy
        s = Strategy()
        rule_score = s.get_rule_score(ind)
        # AI 점수는 고정값 사용 (실시간 AI 호출 X)
        combined = int(rule_score * 0.5 + self.ai_score_fixed * 0.5)
        return max(0, min(100, combined))

    def _apply_fee(self, price: float, qty: int, is_sell: bool) -> float:
        """수수료 + 슬리피지 + 매도세 적용"""
        fee = price * qty * FEE_RATE
        slip = price * qty * SLIPPAGE_RATE
        tax = price * qty * SELL_TAX_RATE if is_sell else 0
        return fee + slip + tax

    def _do_buy(self, code: str, price: float, date: str) -> bool:
        """가상 매수"""
        if len(self.positions) >= self.max_positions:
            return False
        if code in self.positions:
            return False

        qty    = max(int(self.buy_amount / price), 1)
        cost   = price * qty
        fee    = self._apply_fee(price, qty, is_sell=False)
        total  = cost + fee

        if self.cash < total:
            return False

        self.cash -= total
        self.positions[code] = {
            "entry": price, "qty": qty,
            "stage": 0, "peak": price,
            "date":  date,
        }
        return True

    def _do_sell(self, code: str, price: float, qty: int,
                date: str, reason: str) -> float:
        """가상 매도. 반환: 실현 손익"""
        if code not in self.positions:
            return 0

        pos    = self.positions[code]
        entry  = pos["entry"]
        fee    = self._apply_fee(price, qty, is_sell=True)
        proceeds = price * qty - fee
        self.cash += proceeds

        profit_krw  = (price - entry) * qty - fee
        profit_rate = (price - entry) / entry * 100

        self.trades.append({
            "code":        code,
            "entry":       entry,
            "exit":        price,
            "qty":         qty,
            "profit_rate": round(profit_rate, 2),
            "profit_krw":  round(profit_krw),
            "reason":      reason,
            "buy_date":    pos["date"],
            "sell_date":   date,
        })

        if qty >= pos["qty"]:
            del self.positions[code]
        else:
            pos["qty"] -= qty

        return profit_krw

    def _check_sell(self, code: str, pos: dict, date: str):
        """매도 조건 체크"""
        price = pos.get("current", pos["entry"])
        entry = pos["entry"]
        qty   = pos["qty"]
        stage = pos["stage"]
        rate  = (price - entry) / entry

        # 고점 갱신
        if price > pos["peak"]:
            pos["peak"] = price

        # ── 2차 익절 ─────────────────────────────────────────
        if stage < 2 and rate >= self.sell_2nd_rate:
            sell_qty = max(int(qty * self.sell_2nd_qty / (1 - self.sell_1st_qty)), 1)
            sell_qty = min(sell_qty, qty)
            self._do_sell(code, price, sell_qty, date, f"2차익절({rate:+.1%})")
            if code in self.positions:
                self.positions[code]["stage"] = 2
            return

        # ── 1차 익절 ─────────────────────────────────────────
        if stage < 1 and rate >= self.sell_1st_rate:
            sell_qty = max(int(qty * self.sell_1st_qty), 1)
            self._do_sell(code, price, sell_qty, date, f"1차익절({rate:+.1%})")
            if code in self.positions:
                self.positions[code]["stage"] = 1
            return

        # ── 트레일링 스탑 (1차 후) ───────────────────────────
        if stage >= 1:
            trail = self.trail_after_1st
            peak_rate = (pos["peak"] - entry) / entry
            if peak_rate - rate >= trail:
                self._do_sell(code, price, qty, date, f"트레일링({rate:+.1%})")
                return

        # ── 손절 ─────────────────────────────────────────────
        stop = self.stop_after_1st if stage >= 1 else self.stop_loss
        if rate <= stop:
            self._do_sell(code, price, qty, date, f"손절({rate:+.1%})")

    def run(self, codes: list, start: str, end: str) -> dict:
        """백테스트 실행"""
        conn = db_connect(DATA_DB)

        # 거래일 목록
        dates = [r[0] for r in conn.execute("""
            SELECT DISTINCT date FROM daily_ohlcv
            WHERE date BETWEEN ? AND ?
            ORDER BY date
        """, (start, end)).fetchall()]

        print(f"\n🔄 백테스트 실행: {start} ~ {end}")
        print(f"   종목 {len(codes)}개 | 거래일 {len(dates)}일")

        equity_start = INITIAL_CASH

        for i, date in enumerate(dates):
            # 오늘 가격 업데이트
            for code in list(self.positions.keys()):
                row = conn.execute("""
                    SELECT close FROM daily_ohlcv WHERE code=? AND date=?
                """, (code, date)).fetchone()
                if row:
                    self.positions[code]["current"] = row[0]

            # 매도 체크 (보유 종목)
            for code in list(self.positions.keys()):
                if code in self.positions:
                    self._check_sell(code, self.positions[code], date)

            # 매수 체크 (슬롯 있을 때)
            if len(self.positions) < self.max_positions:
                # 오늘 지표 계산 후 점수 높은 순 정렬
                candidates = []
                for code in codes:
                    if code in self.positions:
                        continue
                    ind = self._get_indicators(code, date, conn)
                    if not ind:
                        continue
                    # 백테스트: 조건 완화 (등락률 0% 이상, 거래대금 10억 이상)
                    if ind["change_rate"] < 0.0:
                        continue
                    if ind["trading_value"] < 10:
                        continue
                    score = self._calc_total_score(ind)
                    if score >= self.score_min:
                        candidates.append((score, code, ind))

                candidates.sort(key=lambda x: x[0], reverse=True)

                for score, code, ind in candidates[:3]:
                    if len(self.positions) >= self.max_positions:
                        break
                    price = ind["close"]
                    if self._do_buy(code, price, date):
                        pass  # 매수 성공

            # 자산 기록
            pos_value = sum(
                p.get("current", p["entry"]) * p["qty"]
                for p in self.positions.values()
            )
            total_equity = self.cash + pos_value
            self.equity_curve.append((date, total_equity))

            if (i + 1) % 50 == 0:
                print(f"   [{i+1}/{len(dates)}] {date} | "
                      f"자산:{total_equity:,.0f}원 | "
                      f"포지션:{len(self.positions)}")

        conn.close()
        return self._calc_metrics()

    def _calc_metrics(self) -> dict:
        """성과 메트릭 계산"""
        if not self.trades:
            return {"error": "매매 없음"}

        profits     = [t["profit_rate"] for t in self.trades]
        profit_krws = [t["profit_krw"]  for t in self.trades]
        wins        = [p for p in profits if p >= 0]
        losses_     = [p for p in profits if p < 0]

        # MDD 계산
        mdd         = 0.0
        peak_equity = INITIAL_CASH
        for _, equity in self.equity_curve:
            if equity > peak_equity:
                peak_equity = equity
            dd = (peak_equity - equity) / peak_equity
            if dd > mdd:
                mdd = dd

        # 최종 자산
        final_equity = self.equity_curve[-1][1] if self.equity_curve else INITIAL_CASH
        total_return = (final_equity - INITIAL_CASH) / INITIAL_CASH * 100

        # 샤프지수 (일별 수익률 기준)
        daily_returns = []
        prev_eq = INITIAL_CASH
        for _, eq in self.equity_curve:
            if prev_eq > 0:
                daily_returns.append((eq - prev_eq) / prev_eq)
            prev_eq = eq

        sharpe = 0.0
        if daily_returns:
            avg_r = sum(daily_returns) / len(daily_returns)
            std_r = (sum((r - avg_r)**2 for r in daily_returns) / len(daily_returns))**0.5
            sharpe = (avg_r / std_r * (252**0.5)) if std_r > 0 else 0

        # Profit Factor
        total_win_krw  = sum(k for k in profit_krws if k >= 0)
        total_loss_krw = abs(sum(k for k in profit_krws if k < 0))
        pf = total_win_krw / total_loss_krw if total_loss_krw > 0 else 999

        # 최대 연속 손절
        max_consec_loss = 0
        cur_consec      = 0
        for p in profits:
            if p < 0:
                cur_consec += 1
                max_consec_loss = max(max_consec_loss, cur_consec)
            else:
                cur_consec = 0

        return {
            "total_trades":     len(profits),
            "win_count":        len(wins),
            "loss_count":       len(losses_),
            "win_rate":         round(len(wins)/len(profits)*100, 1) if profits else 0,
            "avg_profit":       round(sum(profits)/len(profits), 2) if profits else 0,
            "avg_win":          round(sum(wins)/len(wins), 2) if wins else 0,
            "avg_loss":         round(sum(losses_)/len(losses_), 2) if losses_ else 0,
            "best_trade":       round(max(profits), 2) if profits else 0,
            "worst_trade":      round(min(profits), 2) if profits else 0,
            "total_profit_krw": round(sum(profit_krws)),
            "total_return":     round(total_return, 2),
            "mdd":              round(mdd * 100, 2),
            "sharpe":           round(sharpe, 2),
            "profit_factor":    round(pf, 2),
            "max_consec_loss":  max_consec_loss,
            "initial_cash":     INITIAL_CASH,
            "final_equity":     round(final_equity),
        }


# ============================================================
# 3. 결과 출력
# ============================================================
def print_metrics(metrics: dict, name: str = "기본 전략"):
    print(f"\n{'━'*50}")
    print(f"📊 [{name}] 백테스트 결과")
    print(f"{'━'*50}")

    if "error" in metrics:
        print(f"❌ {metrics['error']}")
        return

    wr = metrics["win_rate"]
    tr = metrics["total_return"]
    e1 = "✅" if wr >= 50 else "⚠️"
    e2 = "✅" if tr >= 0  else "❌"

    print(f"\n[거래 통계]")
    print(f"  총 거래: {metrics['total_trades']}건 "
          f"(익절:{metrics['win_count']} / 손절:{metrics['loss_count']})")
    print(f"  {e1} 승률:        {wr}%")
    print(f"  평균 수익:     {metrics['avg_profit']:+.2f}%")
    print(f"  평균 익절:     {metrics['avg_win']:+.2f}%")
    print(f"  평균 손절:     {metrics['avg_loss']:+.2f}%")
    print(f"  최고:          {metrics['best_trade']:+.2f}%")
    print(f"  최저:          {metrics['worst_trade']:+.2f}%")
    print(f"  연속 손절:     최대 {metrics['max_consec_loss']}회")

    print(f"\n[수익성]")
    print(f"  {e2} 총 수익률:  {tr:+.2f}%")
    print(f"  총 실현손익:   {metrics['total_profit_krw']:+,.0f}원")
    print(f"  최종 자산:     {metrics['final_equity']:,.0f}원")
    print(f"  초기 자산:     {metrics['initial_cash']:,.0f}원")

    print(f"\n[리스크]")
    mdd_e = "✅" if metrics['mdd'] < 15 else "⚠️"
    sh_e  = "✅" if metrics['sharpe'] >= 1.0 else "⚠️"
    pf_e  = "✅" if metrics['profit_factor'] >= 1.5 else "⚠️"
    print(f"  {mdd_e} MDD:          -{metrics['mdd']:.2f}%")
    print(f"  {sh_e} 샤프지수:     {metrics['sharpe']:.2f}")
    print(f"  {pf_e} Profit Factor: {metrics['profit_factor']:.2f}")
    print(f"{'━'*50}")


# ============================================================
# 4. 시나리오 비교
# ============================================================
SCENARIOS = [
    {
        "name": "현재 전략 (기준)",
        "params": {},
    },
    {
        "name": "손절 완화 (-7%)",
        "params": {"stop_loss": -0.07},
    },
    {
        "name": "손절 강화 (-3%)",
        "params": {"stop_loss": -0.03},
    },
    {
        "name": "AI 점수 기준 상향 (60점)",
        "params": {"score_min": 60},
    },
    {
        "name": "AI 점수 기준 하향 (50점)",
        "params": {"score_min": 50},
    },
    {
        "name": "1차 익절 빠르게 (+3%)",
        "params": {"sell_1st_rate": 0.03},
    },
    {
        "name": "1차 익절 느리게 (+7%)",
        "params": {"sell_1st_rate": 0.07},
    },
    {
        "name": "본절보호 강화 (-1%)",
        "params": {"stop_after_1st": -0.01},
    },
]


def run_compare(codes: list, start: str, end: str):
    """시나리오별 비교"""
    results = []
    for sc in SCENARIOS:
        print(f"\n⚙️  시나리오: {sc['name']}")
        engine  = BacktestEngine(params=sc["params"])
        metrics = engine.run(codes, start, end)
        metrics["name"] = sc["name"]
        results.append(metrics)
        print_metrics(metrics, sc["name"])

    # 요약 비교표
    print(f"\n{'━'*80}")
    print(f"📊 시나리오 비교 요약")
    print(f"{'━'*80}")
    print(f"{'전략':<25} {'승률':>6} {'수익률':>8} {'MDD':>8} {'샤프':>6} {'PF':>6}")
    print(f"{'─'*80}")
    for r in results:
        if "error" in r:
            continue
        print(f"{r['name']:<25} {r['win_rate']:>5.1f}% "
              f"{r['total_return']:>+7.2f}% "
              f"{'-'+str(r['mdd'])+'%':>8} "
              f"{r['sharpe']:>6.2f} "
              f"{r['profit_factor']:>6.2f}")
    print(f"{'━'*80}")

    # 최적 시나리오
    valid = [r for r in results if "error" not in r]
    if valid:
        best = max(valid, key=lambda r: r["total_return"])
        print(f"\n🏆 최고 수익률: [{best['name']}] {best['total_return']:+.2f}%")
        best_s = max(valid, key=lambda r: r["sharpe"])
        print(f"🏆 최고 샤프:   [{best_s['name']}] {best_s['sharpe']:.2f}")


# ============================================================
# 5. 진입점
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="영암9 단타봇 백테스터")
    parser.add_argument("--fetch",   action="store_true", help="데이터 수집")
    parser.add_argument("--run",     action="store_true", help="백테스트 실행")
    parser.add_argument("--compare", action="store_true", help="시나리오 비교")
    parser.add_argument("--all",     action="store_true", help="수집+실행+비교")
    parser.add_argument("--start",   default=DEFAULT_START, help=f"시작일 (기본:{DEFAULT_START})")
    parser.add_argument("--end",     default=DEFAULT_END,   help=f"종료일 (기본:{DEFAULT_END})")
    parser.add_argument("--stocks",  type=int, default=100, help="백테스트 종목 수 (기본:100)")
    args = parser.parse_args()

    if not any([args.fetch, args.run, args.compare, args.all]):
        parser.print_help()
        return

    # ── 데이터 수집 ──────────────────────────────────────────
    if args.fetch or args.all:
        fetcher = DataFetcher()
        fetcher.run(args.start, args.end)

    # ── 종목 리스트 로드 ─────────────────────────────────────
    conn   = db_connect(DATA_DB)
    stocks = conn.execute("""
        SELECT DISTINCT code FROM daily_ohlcv
        WHERE date >= ? GROUP BY code HAVING COUNT(*) >= 100
        ORDER BY COUNT(*) DESC LIMIT ?
    """, (args.start, args.stocks)).fetchall()
    conn.close()
    codes  = [r[0] for r in stocks]
    print(f"\n📋 백테스트 대상: {len(codes)}개 종목")

    if not codes:
        print("❌ 데이터 없음 — 먼저 --fetch 실행하세요")
        return

    # ── 단순 실행 ────────────────────────────────────────────
    if args.run:
        engine  = BacktestEngine()
        metrics = engine.run(codes, args.start, args.end)
        print_metrics(metrics, "현재 전략")

        # 최근 10건 매매 내역
        if engine.trades:
            print("\n📋 최근 10건 매매 내역")
            print(f"{'날짜':<12} {'종목':>8} {'손익률':>8} {'손익(원)':>12} {'사유'}")
            for t in engine.trades[-10:]:
                e = "✅" if t["profit_rate"] >= 0 else "❌"
                print(f"{t['sell_date']:<12} {t['code']:>8} "
                      f"{e}{t['profit_rate']:>+6.2f}% "
                      f"{t['profit_krw']:>+12,.0f} {t['reason']}")

    # ── 시나리오 비교 ────────────────────────────────────────
    if args.compare or args.all:
        run_compare(codes, args.start, args.end)


if __name__ == "__main__":
    main()
