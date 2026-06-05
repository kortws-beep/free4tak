import requests, os
from dotenv import load_dotenv
load_dotenv('/home/free4tak/k-bot/stock_bot/.env')

appkey = os.getenv("KIS_APPKEY")
secret = os.getenv("KIS_SECRET")
token  = requests.post(
    "https://openapi.koreainvestment.com:9443/oauth2/tokenP",
    json={"grant_type": "client_credentials",
          "appkey": appkey, "appsecret": secret}
).json().get("access_token", "")

headers = {"authorization": f"Bearer {token}",
           "appkey": appkey, "appsecret": secret,
           "custtype": "P"}

# 코스닥 마켓코드 탐색
url = "https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/volume-rank"
for mrkt in ["Q", "K", "NX", "KQ", "KOSDAQ"]:
    h = {**headers, "tr_id": "FHPST01710000"}
    params = {
        "FID_COND_MRKT_DIV_CODE":  mrkt,
        "FID_COND_SCR_DIV_CODE":   "20171",
        "FID_INPUT_ISCD":          "0000",
        "FID_DIV_CLS_CODE":        "0",
        "FID_BLNG_CLS_CODE":       "0",
        "FID_TRGT_CLS_CODE":       "111111111",
        "FID_TRGT_EXLS_CLS_CODE":  "000000",
        "FID_INPUT_PRICE_1":       "1000",
        "FID_INPUT_PRICE_2":       "",
        "FID_VOL_CNT":             "",
        "FID_INPUT_DATE_1":        "0",
    }
    try:
        r    = requests.get(url, headers=h, params=params, timeout=5)
        data = r.json()
        rt   = data.get("rt_cd")
        msg  = data.get("msg1","")[:40]
        cnt  = len(data.get("output", []))
        print(f"mrkt='{mrkt}': rt={rt} | {msg} | 종목:{cnt}")
        if rt == "0" and cnt > 0:
            print(f"  첫 종목: {data['output'][0].get('hts_kor_isnm')} {data['output'][0].get('mksc_shrn_iscd')}")
    except Exception as e:
        print(f"mrkt='{mrkt}': 오류 {e}")

# 코스닥 개별종목 일봉 테스트 (에코프로 086520)
print("\n[일봉 테스트 — 에코프로 086520 코스닥]")
for mrkt in ["J", "Q", "K"]:
    h2 = {**headers, "tr_id": "FHKST03010100"}
    r2 = requests.get(
        "https://openapi.koreainvestment.com:9443/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
        headers=h2,
        params={
            "FID_COND_MRKT_DIV_CODE": mrkt,
            "FID_INPUT_ISCD":         "086520",
            "FID_INPUT_DATE_1":       "20240101",
            "FID_INPUT_DATE_2":       "20260508",
            "FID_PERIOD_DIV_CODE":    "D",
            "FID_ORG_ADJ_PRC":        "0",
        },
        timeout=10
    ).json()
    candles = r2.get("output2", [])
    print(f"  mrkt={mrkt}: rt={r2.get('rt_cd')} | 캔들:{len(candles)}개")
    if candles:
        print(f"  첫 캔들: {candles[0].get('stck_bsop_date')} 종가:{candles[0].get('stck_clpr')}")
        break
