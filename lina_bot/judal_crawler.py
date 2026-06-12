import sqlite3
import re
import time
from bs4 import BeautifulSoup
from curl_cffi import requests

# 대장의 통합 DB 파일 이름
DB_PATH = "kr_theme_finance.db"
BASE_URL = "https://www.judal.co.kr"

def init_db():
    """DB 초기화 로직"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS kr_theme_stocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            theme_name TEXT NOT NULL,
            stock_name TEXT NOT NULL,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(theme_name, stock_name)
        )
    """)
    conn.commit()
    conn.close()

def crawl_judal():
    print("🚀 [주달 크롤러] 메인 페이지에서 테마 목록 스캔을 시작합니다...")
    init_db()
    
    try:
        res = requests.get(BASE_URL, impersonate="chrome", timeout=10)
    except Exception as e:
        print(f"❌ 주달 사이트 접속 실패: {e}")
        return
        
    soup = BeautifulSoup(res.content, 'html.parser')
    theme_links = {}
    
    # 🚫 [핵심 방어막] 테마가 아닌, 시장 전체를 의미하는 불필요한 단어들 차단!
    exclude_words = [
        "코스피", "코스닥", "KOSPI", "KOSDAQ", "전체", "상승", "하락", 
        "보합", "신고가", "신저가", "거래량", "거래대금", "외국인", "기관", 
        "시가총액", "관리종목", "ETF", "ETN", "스팩"
    ]
    
    for a in soup.find_all('a', href=True):
        text = a.get_text(strip=True)
        
        # 정규식: "어떤글자(숫자)" 형태인지 검사
        if re.match(r'^.+\(\d+\)$', text):
            theme_name = re.sub(r'\(\d+\)$', '', text).strip()
            
            # 필터링: 블랙리스트 단어가 포함되어 있으면 쿨하게 패스!
            if any(bad_word in theme_name.upper() for bad_word in exclude_words):
                continue
                
            href = a['href']
            if href.startswith('/'):
                href = BASE_URL + href
            elif not href.startswith('http'):
                continue
            
            theme_links[theme_name] = href

    print(f"✅ 총 {len(theme_links)}개의 '진짜' 테마 링크를 추려냈습니다! 긁어옵니다...\n")
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    total_saved = 0
    
    for i, (theme_name, link) in enumerate(theme_links.items(), 1):
        print(f"🔍 [{i}/{len(theme_links)}] '{theme_name}' 테마 파헤치는 중...")
        try:
            sub_res = requests.get(link, impersonate="chrome", timeout=10)
            sub_soup = BeautifulSoup(sub_res.content, 'html.parser')
            
            stocks = set()
            for a in sub_soup.find_all('a', href=True):
                href = a['href']
                text = a.get_text(strip=True)
                
                if re.search(r'\d{6}', href) and len(text) > 0:
                    clean_stock = text.replace("투자분석", "").replace("보기", "").strip()
                    if clean_stock and not clean_stock.isdigit():
                        stocks.add(clean_stock)
            
            for stock in stocks:
                try:
                    cursor.execute("""
                        INSERT OR IGNORE INTO kr_theme_stocks (theme_name, stock_name)
                        VALUES (?, ?)
                    """, (theme_name, stock))
                    total_saved += 1
                except Exception:
                    pass
            
            conn.commit()
            time.sleep(0.3) 
            
        except Exception as e:
            print(f"⚠️ '{theme_name}' 페이지 오류 (스킵): {e}")

    conn.close()
    print(f"\n🎉 [크롤링 완료] 시장 전체 데이터는 걸러내고 총 {total_saved}개의 알짜 테마 종목이 세팅되었습니다! 🚀")

if __name__ == "__main__":
    crawl_judal()