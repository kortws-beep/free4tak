import sqlite3
import os

MAPPING_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "us_kr_mapping.db")

def init_mapping_db():
    """맵핑 데이터베이스 및 초기 세팅"""
    conn = sqlite3.connect(MAPPING_DB)
    cursor = conn.cursor()
    
    # 맵핑 테이블 생성
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS us_kr_mapping (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            us_ticker TEXT NOT NULL,       -- 예: INTC, NVDA
            us_name TEXT NOT NULL,         -- 예: 인텔, 엔비디아
            kr_name TEXT NOT NULL,         -- 예: 인텍플러스, 가온칩스
            reason TEXT,                   -- 수혜 사유 (납품, 유리기판 등)
            is_static INTEGER DEFAULT 1,   -- 1: 대장 고정 DB, 0: AI가 자동 발굴한 종목
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # 샘플 데이터가 없을 때만 초기 데이터 입력 (인텔 예시)
    cursor.execute("SELECT COUNT(*) FROM us_kr_mapping")
    if cursor.fetchone()[0] == 0:
        samples = [
            ("INTC", "인텔", "인텍플러스", "인텔 패키징 장비 주요 공급사", 1),
            ("INTC", "인텔", "가온칩스", "인텔 파운드리 디자인솔루션 파트너", 1),
            ("INTC", "인텔", "고영", "인텔 어드밴스드 패키징 검사장비 공급", 1),
            ("NVDA", "엔비디아", "SK하이닉스", "HBM 주요 공급사", 1),
            ("NVDA", "엔비디아", "한미반도체", "HBM 필수 장비 TC본더 독점력", 1)
        ]
        cursor.executemany("""
            INSERT INTO us_kr_mapping (us_ticker, us_name, kr_name, reason, is_static)
            VALUES (?, ?, ?, ?, ?)
        """, samples)
        conn.commit()
        print("✅ [DB] 미국장-한국장 초기 맵핑 사전 구축 완료!")
        
    conn.close()

def get_kr_stocks_by_ticker(us_ticker):
    """미국 티커를 주면 엮인 한국 종목 리스트를 DB에서 꺼내오는 함수"""
    conn = sqlite3.connect(MAPPING_DB)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT kr_name, reason, is_static 
        FROM us_kr_mapping 
        WHERE us_ticker = ?
    """, (us_ticker,))
    rows = cursor.fetchall()
    conn.close()
    return [{"kr_name": r[0], "reason": r[1], "is_static": r[2]} for r in rows]