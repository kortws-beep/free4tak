"""
db_manager.py — 매매이력 / AI캐시 DB 관리
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

봇이 매매한 기록과 AI 분석 결과를 데이터베이스(SQLite)에 저장합니다.
- 매수/매도 이력 → 나중에 승률 계산, AI 학습에 활용
- AI 분석 캐시 → 같은 종목을 30분 내 다시 분석할 때 절약
                  (Claude API 비용 절감)

[주요 개선 사항]
1. WAL 모드 활성화 — 여러 봇이 동시에 DB를 써도 잠금(Lock) 안 걸림
2. AI 캐시 1일로 단축 (기존 7일 → 시장 급변 시 위험)
3. 가격 변동 감지 — 캐시 저장 시점 대비 ±5% 변동 시 캐시 무효화
4. 트랜잭션 보호 — DB 쓰기 중 죽어도 데이터 유실 없음
================================================================
"""
import sqlite3
import datetime
from typing import Optional


# 파일명
AI_CACHE_DB   = "ai_cache.db"
TRADE_HIST_DB = "trade_history.db"

# AI 캐시 유효 기간 (1일로 단축 — 시장 변화 빠름)
AI_CACHE_HOURS = 24

# 캐시 저장 시점 대비 가격 변동이 이만큼 크면 캐시 무효화
PRICE_DRIFT_THRESHOLD = 0.05  # 5%


# ============================================================
# DB 연결 헬퍼 (WAL 모드 자동 적용)
# ============================================================
def _connect(db_file: str, timeout: int = 15) -> sqlite3.Connection:
    """
    SQLite 연결을 만들고 WAL 모드를 켭니다.
    WAL(Write-Ahead Logging) 모드는 동시 읽기/쓰기 성능을 크게 향상시킵니다.
    여러 봇이 같은 DB를 사용해도 'database is locked' 에러가 잘 안 납니다.
    """
    conn = sqlite3.connect(db_file, timeout=timeout)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")  # WAL과 함께 쓰면 안전+빠름
    conn.execute("PRAGMA busy_timeout=10000")  # 잠금 시 10초 대기
    return conn


class DBManager:
    """
    매매이력 + AI캐시 DB를 관리하는 클래스.
    봇 인스턴스 1개당 DBManager 1개를 만들어 사용합니다.
    """

    def __init__(self,
                 ai_cache_db: str = AI_CACHE_DB,
                 trade_hist_db: str = TRADE_HIST_DB):
        self.ai_cache_db   = ai_cache_db
        self.trade_hist_db = trade_hist_db

    # ============================================================
    # AI 캐시 DB
    # ============================================================
    def init_ai_db(self):
        """AI 캐시 테이블을 만듭니다 (없으면 생성)."""
        try:
            conn = _connect(self.ai_cache_db)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ai_analysis (
                    code         TEXT PRIMARY KEY,
                    score        INTEGER NOT NULL,
                    reason       TEXT,
                    cached_price REAL DEFAULT 0,
                    analyzed_at  TEXT NOT NULL
                )
            """)
            # 기존 DB에 cached_price 컬럼 없으면 추가 (마이그레이션)
            try:
                conn.execute("ALTER TABLE ai_analysis ADD COLUMN cached_price REAL DEFAULT 0")
            except sqlite3.OperationalError:
                pass  # 이미 있음

            conn.commit()
            conn.close()
            print(f"✅ AI DB 초기화 ({self.ai_cache_db})")
        except Exception as e:
            print(f"❌ AI DB 초기화 오류: {e}")

    def get_ai_cache(self, code: str, current_price: float = 0) -> Optional[dict]:
        """
        AI 캐시 조회.
        - 24시간 이내 + 현재가가 캐시 시점 대비 ±5% 이내일 때만 유효
        - 그 외에는 None 반환 → 새로 분석해야 함
        """
        try:
            conn = _connect(self.ai_cache_db)
            row  = conn.execute("""
                SELECT score, reason, cached_price, analyzed_at
                FROM ai_analysis WHERE code = ?
            """, (code,)).fetchone()
            conn.close()

            if not row:
                return None

            score, reason, cached_price, analyzed_at = row

            # 시간 체크
            try:
                analyzed_dt = datetime.datetime.fromisoformat(analyzed_at)
            except ValueError:
                return None
            age_hours = (datetime.datetime.now() - analyzed_dt).total_seconds() / 3600
            if age_hours >= AI_CACHE_HOURS:
                return None

            # 가격 변동 체크 (캐시 저장 당시 가격이 있을 때만)
            if cached_price and current_price > 0:
                drift = abs(current_price - cached_price) / cached_price
                if drift >= PRICE_DRIFT_THRESHOLD:
                    print(f"   🔄 캐시 무효 {code} | 가격변동 {drift*100:.1f}% — 재분석 필요")
                    return None

            return {
                "score":       score,
                "reason":      reason,
                "analyzed_at": analyzed_at,
            }
        except Exception as e:
            print(f"⚠️ AI 캐시 조회 오류 {code}: {e}")
            return None

    def save_ai_cache(self, code: str, score: int, reason: str,
                      current_price: float = 0):
        """AI 분석 결과를 캐시에 저장 (현재가도 함께 저장해 변동 감지에 활용)"""
        try:
            now  = datetime.datetime.now().isoformat(timespec="seconds")
            conn = _connect(self.ai_cache_db)
            conn.execute("""
                INSERT INTO ai_analysis (code, score, reason, cached_price, analyzed_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(code) DO UPDATE SET
                    score        = excluded.score,
                    reason       = excluded.reason,
                    cached_price = excluded.cached_price,
                    analyzed_at  = excluded.analyzed_at
            """, (code, score, reason, current_price, now))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"⚠️ AI 캐시 저장 오류 {code}: {e}")

    def clean_ai_db(self):
        """24시간 지난 캐시 일괄 삭제"""
        try:
            cutoff = (datetime.datetime.now()
                      - datetime.timedelta(hours=AI_CACHE_HOURS)).isoformat(timespec="seconds")
            conn   = _connect(self.ai_cache_db)
            cur    = conn.execute(
                "DELETE FROM ai_analysis WHERE analyzed_at < ?", (cutoff,)
            )
            deleted = cur.rowcount
            conn.commit()
            conn.close()
            if deleted:
                print(f"🗑️ AI 캐시 만료 {deleted}개 삭제")
        except Exception as e:
            print(f"⚠️ AI DB 정리 오류: {e}")

    # ============================================================
    # 매매 이력 DB
    # ============================================================
    def init_trade_db(self):
        """매매이력 테이블 + 시장지수 일별 테이블 생성"""
        try:
            conn = _connect(self.trade_hist_db)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    code           TEXT    NOT NULL,
                    stock_name     TEXT,
                    buy_price      REAL    NOT NULL,
                    buy_time       TEXT    NOT NULL,
                    sell_price     REAL,
                    sell_time      TEXT,
                    qty            INTEGER NOT NULL,
                    profit_rate    REAL,
                    sell_reason    TEXT,
                    ai_score       INTEGER,
                    ai_reason      TEXT,
                    change_rate    REAL,
                    volume_ratio   REAL,
                    rsi            REAL,
                    ma_aligned     INTEGER,
                    foreign_5d     REAL,
                    institution_5d REAL,
                    buy_tag        TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_trades_code
                ON trades(code, sell_time)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_trades_sell_time
                ON trades(sell_time)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS market_daily (
                    date          TEXT PRIMARY KEY,
                    kospi_change  REAL,
                    kosdaq_change REAL,
                    created_at    TEXT NOT NULL
                )
            """)
            conn.commit()
            conn.close()
            print(f"✅ 매매이력 DB 초기화 ({self.trade_hist_db})")
        except Exception as e:
            print(f"❌ 매매이력 DB 초기화 오류: {e}")

    def save_buy_history(self, code: str, buy_price: float, qty: int,
                         ai_score: int, ai_reason: str, indicators: dict,
                         stock_name: str = "", buy_tag: str = ""):
        """매수 이력 저장 (sell_price는 NULL로, 매도 시 업데이트)"""
        try:
            now  = datetime.datetime.now().isoformat(timespec="seconds")
            ma5, ma20, ma60 = (indicators.get("ma5", 0),
                               indicators.get("ma20", 0),
                               indicators.get("ma60", 0))
            ma_aligned = 1 if (ma5 > ma20 > ma60 > 0) else 0

            conn = _connect(self.trade_hist_db)
            conn.execute("""
                INSERT INTO trades
                    (code, stock_name, buy_price, buy_time, qty, ai_score, ai_reason,
                     change_rate, volume_ratio, rsi, ma_aligned,
                     foreign_5d, institution_5d, buy_tag)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                code, stock_name, buy_price, now, qty, ai_score, ai_reason,
                indicators.get("change_rate",    0),
                indicators.get("volume_ratio",   0),
                indicators.get("rsi",            50),
                ma_aligned,
                indicators.get("foreign_5d",     0),
                indicators.get("institution_5d", 0),
                buy_tag,
            ))
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"⚠️ 매수이력 저장 오류 {code}: {e}")

    def save_sell_history(self, code: str, sell_price: float,
                          sell_reason: str, sold_qty: int = 0):
        """
        매도 이력 저장.
        - 가장 최근의 미청산 매수 건을 찾아 매도가/매도시점/수익률 업데이트
        - sold_qty가 0이면 전량 매도, >0이면 분할 매도 (분할은 별도 행 추가)
        """
        try:
            now  = datetime.datetime.now().isoformat(timespec="seconds")
            conn = _connect(self.trade_hist_db)
            row  = conn.execute("""
                SELECT id, buy_price, qty FROM trades
                WHERE code=? AND sell_price IS NULL
                ORDER BY id DESC LIMIT 1
            """, (code,)).fetchone()

            if not row:
                conn.close()
                return

            trade_id, buy_price, total_qty = row
            profit_rate = ((sell_price - buy_price) / buy_price * 100
                           if buy_price else 0)

            # ★ 트랜잭션 원자성 보장
            try:
                if sold_qty == 0 or sold_qty >= total_qty:
                    # 전량 매도
                    conn.execute("""
                        UPDATE trades
                        SET sell_price=?, sell_time=?, profit_rate=?, sell_reason=?
                        WHERE id=?
                    """, (sell_price, now, round(profit_rate, 2), sell_reason, trade_id))
                else:
                    # 분할 매도 — INSERT + UPDATE 원자적 처리
                    conn.execute("""
                        INSERT INTO trades
                            (code, buy_price, buy_time, qty, sell_price, sell_time,
                             profit_rate, sell_reason)
                        SELECT code, buy_price, buy_time, ?, ?, ?, ?, ?
                        FROM trades WHERE id=?
                    """, (sold_qty, sell_price, now,
                          round(profit_rate, 2), sell_reason, trade_id))
                    conn.execute("""
                        UPDATE trades SET qty=? WHERE id=?
                    """, (total_qty - sold_qty, trade_id))
                conn.commit()  # ★ 두 작업 모두 성공 시에만 커밋
            except Exception as tx_e:
                conn.rollback()  # ★ 실패 시 전체 롤백
                raise tx_e
            conn.close()
            emoji = "✅" if profit_rate >= 0 else "❌"
            print(f"   {emoji} 이력저장 {code} | {profit_rate:+.2f}% | {sell_reason}")
        except Exception as e:
            print(f"⚠️ 매도이력 저장 오류 {code}: {e}")

    def get_trade_history(self, code: str, limit: int = 10) -> list:
        """특정 종목의 과거 매매 기록 조회 (AI 분석에 컨텍스트로 제공)"""
        try:
            conn = _connect(self.trade_hist_db)
            rows = conn.execute("""
                SELECT buy_time, sell_time, buy_price, sell_price,
                       profit_rate, sell_reason, ai_score, ai_reason
                FROM trades WHERE code=? AND sell_price IS NOT NULL
                ORDER BY id DESC LIMIT ?
            """, (code, limit)).fetchall()
            conn.close()
            return rows
        except Exception as e:
            print(f"⚠️ 이력 조회 오류 {code}: {e}")
            return []

    def get_recent_performance(self, limit: int = 20) -> Optional[dict]:
        """
        최근 N건의 성과 통계.
        - total: 거래 건수
        - win_rate: 승률 (수익 본 비율)
        - avg_profit: 평균 수익률
        - best/worst: 최대/최소 수익률
        """
        try:
            conn = _connect(self.trade_hist_db)
            rows = conn.execute("""
                SELECT profit_rate FROM trades
                WHERE sell_price IS NOT NULL
                ORDER BY id DESC LIMIT ?
            """, (limit,)).fetchall()
            conn.close()
            if not rows:
                return None
            profits = [r[0] for r in rows if r[0] is not None]
            if not profits:
                return None
            wins = [p for p in profits if p >= 0]
            return {
                "total":      len(profits),
                "win_rate":   round(len(wins) / len(profits) * 100, 1),
                "avg_profit": round(sum(profits) / len(profits), 2),
                "best":       round(max(profits), 2),
                "worst":      round(min(profits), 2),
            }
        except Exception as e:
            print(f"⚠️ 성과 조회 오류: {e}")
            return None

    def get_today_realized(self, today: str = None) -> int:
        """
        오늘 실현 손익 합계 (매도 완료 건만).
        '디스코드 !성과' 명령어에서 사용.
        """
        if not today:
            today = datetime.datetime.now().strftime("%Y-%m-%d")
        try:
            conn = _connect(self.trade_hist_db)
            rows = conn.execute("""
                SELECT buy_price, sell_price, qty FROM trades
                WHERE sell_price IS NOT NULL AND sell_time >= ?
            """, (today,)).fetchall()
            conn.close()
            return sum(int((sp - bp) * qty) for bp, sp, qty in rows
                       if sp is not None and bp is not None)
        except Exception:
            return 0

    def get_dynamic_score_threshold(self,
                                    base_threshold: int = 55,
                                    sample_size: int = 20) -> int:
        """
        ★ 신규 기능: 동적 매수 임계치
        최근 거래 승률에 따라 매수 점수 기준을 자동 조정.
        - 승률 40% 미만 → 기준점 +5점 (더 깐깐하게)
        - 승률 60% 초과 → 기준점 -3점 (적극적으로)
        - 그 외 → 기본값 유지
        """
        perf = self.get_recent_performance(sample_size)
        if not perf or perf["total"] < 10:
            return base_threshold

        win_rate = perf["win_rate"]
        if win_rate < 40:
            print(f"   📉 최근승률 {win_rate}% 낮음 → 기준점 +5")
            return base_threshold + 5
        elif win_rate > 60:
            print(f"   📈 최근승률 {win_rate}% 높음 → 기준점 -3")
            return max(45, base_threshold - 3)
        return base_threshold
