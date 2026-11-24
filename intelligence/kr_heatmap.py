"""
kr_heatmap.py — 우리만의 한국 섹터맵: 한투 관심그룹(대장 분야 정리) 히트맵 데이터 (2026-11-24, 대장)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
대장: "전체 맵·업종별 맵은 세이브티커가 주니, 우리만의 맵을 만들자 — 한투 관심종목 중심으로."
→ 대장이 한투 HTS에 분야별로 정리한 관심그룹(반도체_기판PCB, 전력_전선케이블, 로봇_주관심 …)을 칸으로,
  그 안 종목을 작은 칸으로 그린다. 칸 크기 = 시가총액(또는 거래대금), 색 = 오늘 등락률.
  · 그룹 구성: sector_watch.load_groups(한투 API) — 30분마다 다시 읽고 파일에 저장(한투 호출 최소화, 실패하면 저장본)
  · 시세·시총·거래대금: 네이버 다종목 시세(한 번에 120종목, 한투 초당 한도 안 씀) — 30초 캐시
  · 분야 등락 = 시총 가중 평균(대형주 영향 큼) + 단순 평균 · 오른 종목 비율 같이
  · new·SUB·isee5 같은 '분야 아닌 그룹'(sector_weather.NOT_SECTOR)은 따로 골라 볼 수 있게(분야 합계엔 안 넣음)
매매·알림 없음 — 보여주기만(대시보드 🗺️ 섹터맵 탭).
실행(확인용): venv/bin/python intelligence/kr_heatmap.py
"""
import datetime
import json
import os
import sys
import time

import requests

_HERE = os.path.dirname(os.path.abspath(__file__))
_BASE = os.path.dirname(_HERE)
for _d in ("core", "intelligence"):
    if os.path.join(_BASE, _d) not in sys.path:
        sys.path.insert(0, os.path.join(_BASE, _d))

GROUP_CACHE = os.path.join(_HERE, "kr_heatmap_groups.json")
GROUP_TTL = 1800          # 관심그룹 구성 30분마다
QUOTE_TTL = 30            # 시세 30초 캐시(대시보드 여러 번 열어도 네이버 한 번)
CHUNK = 100
NAVER = "https://polling.finance.naver.com/api/realtime/domestic/stock/"
INDEX = "https://m.stock.naver.com/api/index/{}/basic"
UA = {"User-Agent": "Mozilla/5.0"}
NOT_SECTOR = {"isee5", "sub", "new", "끼", "v알림", "신규", "신규추천", "서브", "고정"}

# ★ 11-24 대장 "삼성전자 칸이 너무 커 — 그 자리에 S7을": sbot 대형주(S7) 8종목을 한 분야로 묶고 다른 분야에선 뺌.
#   목록은 bots/sbot.py MEGA_CAP_CODES와 같아야 함(tests/test_kr_heatmap.py가 대조).
S7_NAME = "S7 대형주"
S7_CODES = {"005930": "삼성전자", "000660": "SK하이닉스", "402340": "SK스퀘어", "005935": "삼성전자우",
            "009150": "삼성전기", "032830": "삼성생명", "028260": "삼성물산", "005380": "현대차"}

# ★ 11-25 대장 "로봇 대장주는 레인보우로보틱스·로보티즈 — 얘네가 커야 해(원익홀딩스가 너무 큼)":
#   대장이 지정한 대장주는 어느 분야에 있든 그 분야에서 가장 큰 칸과 같은 크기 이상 + 👑. 이름으로 지정(여기만 고치면 됨).
LEADERS = {"레인보우로보틱스", "로보티즈"}

_quote_cache = {"t": 0, "data": {}}
_build_cache = {"t": 0, "data": None}


def _num(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def load_groups(api_factory=None, now: float = None) -> dict:
    """{그룹명: [(code, name), ...]} — 저장본이 30분 안 됐으면 그대로, 아니면 한투에서 다시(실패하면 저장본)."""
    now = now or time.time()
    saved = {}
    try:
        with open(GROUP_CACHE, encoding="utf-8") as f:
            saved = json.load(f)
        if now - saved.get("t", 0) < GROUP_TTL and saved.get("groups"):
            return {k: [tuple(x) for x in v] for k, v in saved["groups"].items()}
    except (OSError, ValueError):
        pass
    try:
        import sector_watch
        if api_factory is None:
            from kis_api import KisAPI
            api_factory = KisAPI
        groups = sector_watch.load_groups(api_factory(), sector_watch.hts_id())
        if groups:
            tmp = GROUP_CACHE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"t": now, "groups": groups}, f, ensure_ascii=False)
            os.replace(tmp, GROUP_CACHE)
            return groups
    except Exception as e:
        print(f"⚠️ [섹터맵] 관심그룹 읽기 실패 — 저장본 사용: {e}")
    return {k: [tuple(x) for x in v] for k, v in (saved.get("groups") or {}).items()}


def parse_quote(d: dict) -> dict:
    """네이버 다종목 시세 한 줄 → {name, price, chg, cap(원), value(원)} (값 없으면 None)."""
    return {"name": d.get("stockName", ""), "price": _num(d.get("closePriceRaw") or d.get("closePrice")),
            "chg": _num(d.get("fluctuationsRatioRaw") or d.get("fluctuationsRatio")),
            "cap": _num(d.get("marketValueFullRaw")),
            "value": _num(d.get("accumulatedTradingValueRaw"))}   # 원 단위(648억 = 64,849,000,000)


def quotes(codes: list, get=None, now: float = None) -> dict:
    """{code: parse_quote(...)} — 30초 캐시. get(url)→json 주입 가능(테스트)."""
    now = now or time.time()
    if get is None and now - _quote_cache["t"] < QUOTE_TTL and all(c in _quote_cache["data"] for c in codes):
        return _quote_cache["data"]
    get = get or (lambda url: requests.get(url, headers=UA, timeout=8).json())
    out = {}
    for i in range(0, len(codes), CHUNK):
        try:
            for d in get(NAVER + ",".join(codes[i:i + CHUNK])).get("datas", []) or []:
                code = d.get("itemCode")
                if code:
                    out[code] = parse_quote(d)
        except Exception as e:
            print(f"⚠️ [섹터맵] 네이버 시세 실패({i}~): {e}")
    _quote_cache.update(t=now, data=out)
    return out


def index_snapshot(get=None) -> list:
    get = get or (lambda url: requests.get(url, headers=UA, timeout=5).json())
    out = []
    for idx, nm in (("KOSPI", "코스피"), ("KOSDAQ", "코스닥"), ("KPI200", "코스피200")):
        try:
            b = get(INDEX.format(idx))
            out.append({"name": nm, "price": _num(b.get("closePrice")), "chg": _num(b.get("fluctuationsRatio")),
                        "status": b.get("marketStatus", "")})
        except Exception:
            pass
    return out


def summarize(groups: dict, q: dict) -> dict:
    """그룹·종목 → 화면용. 분야(sectors)와 분야 아닌 관심그룹(watch)을 나눔."""
    def members(stocks):
        mem = []
        for code, name in stocks:
            x = q.get(code)
            if not x or x.get("chg") is None or not x.get("price"):
                continue
            nm = name or x["name"]
            mem.append({"code": code, "name": nm, "chg": round(x["chg"], 2), "price": x.get("price"), "leader": nm in LEADERS,
                        "cap": x.get("cap") or 0, "value": x.get("value") or 0})
        mem.sort(key=lambda m: -m["cap"])
        return mem

    sectors, watch = [], []
    for g, stocks in groups.items():
        mem = members(stocks)
        if not mem:
            continue
        cap = sum(m["cap"] for m in mem)
        row = {"group": g, "n": len(mem), "cap": cap, "value": sum(m["value"] for m in mem),
               "chg_w": round(sum(m["chg"] * m["cap"] for m in mem) / cap, 2) if cap else None,
               "chg": round(sum(m["chg"] for m in mem) / len(mem), 2),
               "up": sum(m["chg"] > 0 for m in mem), "down": sum(m["chg"] < 0 for m in mem),
               "flat": sum(m["chg"] == 0 for m in mem), "members": mem}
        (watch if g.strip().lower() in NOT_SECTOR else sectors).append(row)
    sectors.sort(key=lambda s: -(s["chg_w"] if s["chg_w"] is not None else s["chg"]))
    uniq = {m["code"]: m["chg"] for s in sectors for m in s["members"]}
    n = len(uniq) or 1
    return {"sectors": sectors, "watch": watch,
            "breadth": {"n": len(uniq), "up": round(sum(c > 0 for c in uniq.values()) / n * 100),
                        "flat": round(sum(c == 0 for c in uniq.values()) / n * 100),
                        "down": round(sum(c < 0 for c in uniq.values()) / n * 100)}}


_wx_cache = {"day": None, "data": None}
PICK_MAX = 3


def weather_picks(day: str = None) -> dict:
    """키키 날씨(봇에 건 오늘 태세·대장 해제 여부) + 아침 분야 날씨 상위(점수 ≥1, 최대 3개) — 하루 한 번 계산.
    ★ 11-24 대장: '날씨 해제 때 진입 가는 관심종목 분야 표시(3개 이내)'. 분야 날씨는 아직 봇 매수에 연결 안 됨(관찰용)."""
    day = day or datetime.date.today().isoformat()
    if _wx_cache["day"] == day and _wx_cache["data"] is not None:
        return _wx_cache["data"]
    out = {"stock": "", "override": False, "picks": []}
    try:
        import sector_weather
        out["picks"] = [{"group": g, "score": sc, "state": st, "leader": ld or ""}
                        for g, sc, st, _it, ld in sector_weather.weather(day) if sc >= 1][:PICK_MAX]
    except Exception as e:
        print(f"⚠️ [섹터맵] 분야 날씨 실패: {e}")
    try:
        from common_utils import read_state
        st = read_state(os.path.join(_BASE, "sbot_state.json"), default={}) or {}
        w = st.get("weather") or {}
        if w.get("date") == day:
            out["stock"], out["override"] = w.get("label", ""), bool(w.get("override"))
    except Exception:
        pass
    _wx_cache.update(day=day, data=out)
    return out


def with_s7(groups: dict) -> dict:
    """S7 8종목을 한 분야로, 다른 분야(분야 아닌 new·SUB 등 관심그룹은 그대로)에선 뺌."""
    out = {S7_NAME: list(S7_CODES.items())}
    for g, stocks in groups.items():
        keep = stocks if g.strip().lower() in NOT_SECTOR else [(c, n) for c, n in stocks if c not in S7_CODES]
        if keep:
            out[g] = keep
    return out


def build(force: bool = False) -> dict:
    now = time.time()
    if not force and _build_cache["data"] and now - _build_cache["t"] < QUOTE_TTL:
        return _build_cache["data"]
    groups = with_s7(load_groups())
    codes = sorted({c for v in groups.values() for c, _ in v})
    data = summarize(groups, quotes(codes))
    data["index"] = index_snapshot()
    data["weather"] = weather_picks()
    try:
        import us_pairs
        data["us"] = us_pairs.signals()          # ★ 11-25 미국 짝꿍 밤 등락(10분 캐시)
    except Exception as e:
        print(f"⚠️ [섹터맵] 미국 짝꿍 실패: {e}")
        data["us"] = {"date": "", "groups": {}}
    data["at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    _build_cache.update(t=now, data=data)
    return data


if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_BASE, ".env"))
    d = build(force=True)
    print(d["at"], d["index"], d["breadth"])
    for s in d["sectors"]:
        print(f"{s['group']:16s} 가중 {s['chg_w']:+6.2f}% 평균 {s['chg']:+6.2f}% ↑{s['up']} ↓{s['down']} "
              f"시총 {s['cap'] / 1e12:5.1f}조 · 1위 {s['members'][0]['name']} {s['members'][0]['chg']:+.1f}%")
    print("관심(분야 아님):", [(w["group"], w["n"], w["chg_w"]) for w in d["watch"]])
