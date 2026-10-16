"""
probe_sources.py — 전환점 연구 2단계 데이터 출처 탐색 (2026-10-17, 읽기 전용 · 아무것도 저장 안 함)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]

선물 베이시스·외국인 선물 순매수·미수 반대매매·신용 잔고를 모으려는데, 개발 환경에선 이 사이트들에
접속이 안 돼 응답 형식을 모름. 대장 서버에서 후보 경로를 한 번씩 찔러 응답 앞부분만 출력 →
그 결과로 수집기(market_flow.py)를 정확히 맞춘다. 주문·저장 없음.
실행:  python intelligence/probe_sources.py
"""
import os
import re
import sys
import json

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = {"User-Agent": "Mozilla/5.0"}


def show(title: str, text: str, n: int = 900):
    print(f"\n===== {title} =====")
    print(text[:n] if text else "(빈 응답)")


def naver_json(path: str):
    import requests
    try:
        r = requests.get(f"https://m.stock.naver.com/api/{path}", headers=UA, timeout=10)
        try:
            show(f"네이버 {path} ({r.status_code})", json.dumps(r.json(), ensure_ascii=False))
        except ValueError:
            show(f"네이버 {path} ({r.status_code}, JSON 아님)", r.text)
    except Exception as e:
        show(f"네이버 {path}", f"오류: {e}")


def naver_html(url: str, words=("베이시스", "외국인", "미결제")):
    import requests
    try:
        r = requests.get(url, headers=UA, timeout=10)
        r.encoding = r.apparent_encoding or "euc-kr"
        text = re.sub(r"<[^>]+>", " ", r.text)
        text = re.sub(r"\s+", " ", text)
        hits = []
        for w in words:
            for m in re.finditer(w, text):
                hits.append(text[max(0, m.start() - 80):m.start() + 220])
                if len(hits) > 8:
                    break
        show(f"네이버 HTML {url} ({r.status_code})", "\n---\n".join(hits) or text[:600])
    except Exception as e:
        show(f"네이버 HTML {url}", f"오류: {e}")


def kofia(obj: str, title: str):
    """금투협 종합통계(freesis) — 증시자금추이(예탁금·미수금·반대매매)·신용공여 잔고 후보."""
    import requests
    import datetime
    end = datetime.date.today()
    start = end - datetime.timedelta(days=20)
    body = {"dmSearch": {"tmpV40": "1000000", "tmpV41": "1", "tmpV1": "D",
                         "tmpV45": start.strftime("%Y%m%d"), "tmpV46": end.strftime("%Y%m%d"), "OBJ_NM": obj}}
    try:
        r = requests.post("https://freesis.kofia.or.kr/meta/getMetaDataList.do", json=body,
                          headers=dict(UA, **{"Content-Type": "application/json"}), timeout=15)
        show(f"금투협 {title} {obj} ({r.status_code})", r.text, 1200)
    except Exception as e:
        show(f"금투협 {title} {obj}", f"오류: {e}")


def kis_futures():
    """한투 선물 현재가 — 종목코드 후보를 돌려 응답(rt_cd/msg1/필드)을 확인."""
    sys.path.insert(0, os.path.join(_BASE, "core"))
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(_BASE, ".env"))
        from kis_api import KisAPI, _get
        api = KisAPI()
    except Exception as e:
        show("한투 선물", f"초기화 오류: {e}")
        return
    url = f"{api.base_url}/uapi/domestic-futureoption/v1/quotations/inquire-price"
    headers = {"authorization": f"Bearer {api.token}", "appkey": api.appkey, "appsecret": api.secret,
               "tr_id": "FHMIF10000000", "custtype": "P"}
    for code in ("101000", "A01612", "101W12", "10100000"):
        try:
            res = _get(url, headers=headers, params={"FID_COND_MRKT_DIV_CODE": "F", "FID_INPUT_ISCD": code},
                       timeout=10).json()
            show(f"한투 선물 현재가 {code}", json.dumps(res, ensure_ascii=False), 700)
        except Exception as e:
            show(f"한투 선물 현재가 {code}", f"오류: {e}")


if __name__ == "__main__":
    for p in ("index/FUT/basic", "index/FUT/integration", "index/KPI200/basic", "index/KPI200/integration"):
        naver_json(p)
    naver_html("https://finance.naver.com/sise/sise_index.naver?code=FUT")
    naver_html("https://finance.naver.com/sise/investorDealTrendDay.naver?bizdate=&sosok=", ("외국인", "선물"))
    kofia("STATSCU0100000070BO", "증시자금추이(예탁금·미수금·반대매매)")
    kofia("STATSCU0100000060BO", "신용공여 잔고")
    kis_futures()
    print("\n※ 위 출력을 통째로 보내주면 형식에 맞춰 수집기를 만들게(키·토큰 같은 비밀값은 출력 안 함)")
