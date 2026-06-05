"""
news_crawler.py - 섹터 모니터 DB 기반 동적 뉴스 수집 및 감성 분석
===================================================================
- sector_monitor.db에서 최근 3일간 거래대금 상위 테마 → 검색 키워드
- 네이버 뉴스 API 수집 → 감성 분석 → DB 및 CSV 저장
- AI 연동 요약 텍스트 생성 (latest_ai_summary.txt)
"""
import os
import sys
import re
import time
import sqlite3
import pandas as pd
from datetime import datetime, timedelta
from dotenv import load_dotenv
import requests
from collections import Counter

# .env 로드
_here = os.path.dirname(os.path.abspath(__file__))
for _env_path in [os.path.join(_here, ".env"), os.path.join(_here, "..", ".env")]:
    if os.path.exists(_env_path):
        load_dotenv(_env_path)
        break

# ============================================================
# 설정
# ============================================================
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
    print("❌ 오류: .env에 NAVER_CLIENT_ID / NAVER_CLIENT_SECRET 없음")
    sys.exit(1)

SECTOR_DB_PATH = os.path.join(_here, "sector_monitor.db")
NEWS_DB_PATH   = os.path.join(_here, "news_sentiment.db")
os.makedirs(os.path.dirname(NEWS_DB_PATH), exist_ok=True)

# 감성 사전
POSITIVE_WORDS = ['상승', '급등', '호재', '기대', '성장', '돌파', '최대', '강세',
                  '긍정', '개선', '확대', '선전', '호조', '혁신', '성과', '수익',
                  '증가', '신고가', '실적', '개선']
NEGATIVE_WORDS = ['하락', '급락', '악재', '우려', '하향', '부진', '위험', '손실',
                  '부정', '하락세', '불안', '하락장', '급감', '침체', '추락', '반락',
                  '경고', '둔화']

STOPWORDS = set([
    '이번', '그는', '그녀', '그들이', '또한', '이후', '통해', '위한', '때문',
    '이런', '저런', '우리', '자신', '이날', '추미애', '이재명', '윤석열',
    '국회', '정부', '여당', '야당', '토론회', '경기지사', '대전시', '서울시',
    '부산시', '최고치', '사상', '역대', '글로벌', '국제', '포럼', '세미나',
    '하나', '둘', '셋', '더', '많은', '큰', '작은'
])

# ============================================================
# 1. sector_monitor.db → 최근 3일치 상위 테마 그룹화
# ============================================================
def clean_theme_name(raw_name: str) -> str:
    """테마명 → 뉴스 검색용 단순 키워드"""
    name = raw_name.split('_')[0]
    name = re.sub(r'\([^)]*\)', '', name)
    name = name.split('/')[0]
    name = re.sub(r'[^\w가-힣]', '', name).strip()
    if len(name) < 2:
        return ""
    mapping = {
        "2차전지": "2차전지",
        "태양광": "태양광",
        "의복": "의류",
        "자동차": "자동차",
        "증권": "증권",
        "창투": "벤처투자",
        "로봇": "로봇",
        "반도체": "반도체"
    }
    return mapping.get(name, name)

def get_top_themes_from_3days(db_path: str, top_n: int = 10) -> list:
    if not os.path.exists(db_path):
        print(f"⚠️ {db_path} 없음 → 정적 키워드")
        return []
    conn = sqlite3.connect(db_path, timeout=10)
    three_days_ago = (datetime.now() - timedelta(days=3)).strftime('%Y-%m-%d')
    query = f"""
        SELECT theme_nm, SUM(trde_amt) as total_amt
        FROM sector_flow
        WHERE DATE(ts) >= '{three_days_ago}'
        GROUP BY theme_cd
        ORDER BY total_amt DESC
        LIMIT {top_n * 2}
    """
    df = pd.read_sql_query(query, conn)
    conn.close()
    if df.empty:
        return []
    df['clean'] = df['theme_nm'].apply(clean_theme_name)
    df = df[df['clean'] != ""]
    grouped = df.groupby('clean')['total_amt'].sum().reset_index()
    grouped = grouped.sort_values('total_amt', ascending=False).head(top_n)
    keywords = grouped['clean'].tolist()
    print(f"📊 [3일치 DB] 상위 {len(keywords)}개 키워드: {keywords}")
    return keywords

# ============================================================
# 2. 네이버 뉴스 API
# ============================================================
def get_news(keyword: str, display: int = 20) -> list:
    url = "https://openapi.naver.com/v1/search/news.json"
    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET
    }
    params = {"query": keyword, "display": display, "sort": "date"}
    try:
        res = requests.get(url, headers=headers, params=params, timeout=10)
        if res.status_code == 200:
            return res.json().get('items', [])
        else:
            print(f"  API 오류 {res.status_code}")
            return []
    except Exception as e:
        print(f"  요청 실패: {e}")
        return []

# ============================================================
# 3. 감성 분석 & 명사 추출
# ============================================================
def simple_sentiment(text: str) -> str:
    pos = sum(1 for w in POSITIVE_WORDS if w in text)
    neg = sum(1 for w in NEGATIVE_WORDS if w in text)
    if pos > neg:
        return "긍정"
    elif neg > pos:
        return "부정"
    return "중립"

def extract_nouns(text: str) -> list:
    words = re.findall(r'[가-힣]{2,}', text)
    filtered = []
    for w in words:
        if w in STOPWORDS:
            continue
        if len(w) == 2 and w in ['회의', '의장', '위원']:
            continue
        filtered.append(w)
    return filtered

# ============================================================
# 4. DB 초기화 (컬럼 누락시 자동 추가)
# ============================================================
def init_news_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=10)
    cursor = conn.cursor()
    # 테이블 생성 (없으면)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS news_sentiment (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            keyword TEXT NOT NULL,
            title TEXT,
            description TEXT,
            link TEXT,
            pub_date TEXT,
            sentiment TEXT,
            nouns TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # 누락 컬럼 체크 및 추가
    cursor.execute("PRAGMA table_info(news_sentiment)")
    existing = [col[1] for col in cursor.fetchall()]
    if 'link' not in existing:
        cursor.execute("ALTER TABLE news_sentiment ADD COLUMN link TEXT")
        print("✅ link 컬럼 추가")
    if 'pub_date' not in existing:
        cursor.execute("ALTER TABLE news_sentiment ADD COLUMN pub_date TEXT")
        print("✅ pub_date 컬럼 추가")
    if 'description' not in existing:
        cursor.execute("ALTER TABLE news_sentiment ADD COLUMN description TEXT")
        print("✅ description 컬럼 추가")
    conn.commit()
    # 인덱스
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_news_date ON news_sentiment(date)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_news_keyword ON news_sentiment(keyword)")
    conn.commit()
    return conn

# ============================================================
# 5. DB 저장 함수
# ============================================================
def save_news_to_db(conn, date_str, keyword, title, description, link, pub_date, sentiment, nouns_str):
    conn.execute("""
        INSERT INTO news_sentiment (date, keyword, title, description, link, pub_date, sentiment, nouns)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (date_str, keyword, title, description, link, pub_date, sentiment, nouns_str))
    conn.commit()

# ============================================================
# 6. AI 요약 생성
# ============================================================
def generate_summary(df, keywords, output_path="latest_ai_summary.txt"):
    if df.empty:
        return
    total = len(df)
    pos_cnt = df[df['sentiment'] == '긍정'].shape[0]
    neg_cnt = df[df['sentiment'] == '부정'].shape[0]
    neu_cnt = total - pos_cnt - neg_cnt
    pos_ratio = pos_cnt / total * 100
    neg_ratio = neg_cnt / total * 100

    score_map = {'긍정': 1, '중립': 0, '부정': -1}
    kw_sent = df.groupby('keyword').apply(
        lambda x: x['sentiment'].map(score_map).mean(), include_groups=False
    ).sort_values(ascending=False)

    all_nouns = []
    for nouns_str in df['nouns'].dropna():
        all_nouns.extend(nouns_str.split(', '))
    noun_counter = Counter(all_nouns)
    top_nouns = noun_counter.most_common(5)

    today = datetime.now().strftime("%Y-%m-%d")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(f"📊 {today} 시장 감성 리포트 (3일치 DB 기반)\n\n")
        f.write(f"전체 긍정 {pos_ratio:.1f}% / 부정 {neg_ratio:.1f}% / 중립 {neu_cnt/total*100:.1f}%\n\n")
        f.write("✅ 상위 3개 긍정 키워드:\n")
        for kw, score in kw_sent.head(3).items():
            f.write(f"  - {kw}: {score:.2f}\n")
        neg_kw = kw_sent[kw_sent < 0]
        if not neg_kw.empty:
            f.write("\n⚠️ 부정 키워드:\n")
            for kw, score in neg_kw.head(3).items():
                f.write(f"  - {kw}: {score:.2f}\n")
        else:
            f.write("\n⚠️ 부정 키워드 없음\n")
        f.write("\n🔥 핫 키워드 TOP 5:\n")
        for word, cnt in top_nouns:
            f.write(f"  - {word} ({cnt}회)\n")
        if pos_ratio > 40 and neg_ratio < 15:
            opinion = "반도체와 2차전지 중심의 강한 상승 심리. 투자 기회 확대."
        elif neg_ratio > 30:
            opinion = "부정적 뉴스 증가, 단기 조정 가능성 유의."
        else:
            opinion = "중립적 시장 분위기. 업종별 순환매 관찰 필요."
        f.write(f"\n💡 종합 의견: {opinion}\n")
    print(f"✨ AI 요약 저장: {output_path}")

# ============================================================
# 메인 실행
# ============================================================
def main():
    print("🚀 뉴스 수집 시작...")
    today_str = datetime.now().strftime("%Y%m%d")

    # 1) DB에서 키워드 추출
    keywords = get_top_themes_from_3days(SECTOR_DB_PATH, top_n=8)
    if not keywords:
        print("⚠️ DB 키워드 없음 → 정적 키워드 사용")
        keywords = ["반도체", "2차전지", "금리", "환율", "엔비디아", "비트코인", "삼성전자", "알테오젠"]

    print(f"🔍 검색 키워드: {keywords}")

    # 2) DB 초기화
    news_conn = init_news_db(NEWS_DB_PATH)

    all_news = []
    for kw in keywords:
        print(f"🔍 '{kw}' 검색 중...")
        items = get_news(kw, display=20)
        for item in items:
            title = item['title'].replace('<b>', '').replace('</b>', '')
            desc = item['description'].replace('<b>', '').replace('</b>', '')
            link = item['link']
            pub_date = item['pubDate']
            full_text = title + " " + desc
            sentiment = simple_sentiment(full_text)
            nouns = extract_nouns(full_text)
            nouns_str = ', '.join(nouns[:5])

            all_news.append({
                'keyword': kw,
                'title': title,
                'description': desc,
                'link': link,
                'pub_date': pub_date,
                'sentiment': sentiment,
                'nouns': nouns_str
            })
            # DB 저장
            save_news_to_db(news_conn, today_str, kw, title, desc, link, pub_date, sentiment, nouns_str)
        time.sleep(0.5)

    news_conn.close()

    # 3) CSV 저장
    df = pd.DataFrame(all_news)
    csv_path = f"news_{today_str}.csv"
    df.to_csv(csv_path, index=False, encoding='utf-8-sig')
    print(f"✅ 총 {len(df)}개 뉴스 수집 완료 → {csv_path}")
    print(f"📀 SQLite DB 저장 완료: {NEWS_DB_PATH}")

    # 4) 간단 콘솔 리포트
    print("\n" + "="*50)
    print("📊 실시간 투자 키워드 분석 리포트")
    print("="*50)
    pos = df[df['sentiment']=='긍정'].shape[0]
    neg = df[df['sentiment']=='부정'].shape[0]
    neu = len(df) - pos - neg
    print(f"\n[시장 감성] 긍정: {pos/len(df)*100:.1f}% / 부정: {neg/len(df)*100:.1f}% / 중립: {neu/len(df)*100:.1f}%")

    score_map = {'긍정':1, '중립':0, '부정':-1}
    kw_score = df.groupby('keyword').apply(lambda x: x['sentiment'].map(score_map).mean(), include_groups=False).sort_values(ascending=False)
    print("\n[키워드별 투자 심리]")
    for kw, score in kw_score.head(8).items():
        emoji = "▲" if score > 0.2 else ("●" if -0.2 <= score <= 0.2 else "▼")
        print(f"  {emoji} {kw}: {score:.2f}")

    # 5) AI 요약 생성
    generate_summary(df, keywords)
    print("\n✨ 분석 완료! AI 요약이 latest_ai_summary.txt 에 저장됨")

if __name__ == "__main__":
    main()