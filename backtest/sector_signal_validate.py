"""
sector_signal_validate.py — 분야 날씨 보완 신호 검증: 미국 섹터 · 프리장 (2026-11-17, 대장 "미장 섹터·애프터장·프리장 추가, 08:50 판단")
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
3단계 분야 날씨(어제까지 흐름)는 하락장 28일 검증에서 약했음. 대장 보완 아이디어 두 가지를 1년 치로 검증:
  ① 미국 섹터: 전날 미국에서 그 분야에 해당하는 섹터·테마 ETF가 강했으면, 오늘 한국 그 분야도 시장보다 강한가?
     (분야↔ETF 연결은 아래 US_MAP — 이름 키워드로, 누리가 정한 것이라 대장 확인 필요)
  ② 프리장: 오늘 NXT 프리장(08:00~08:50) 마지막 가격으로 본 분야 평균 등락이 강하면, 본장에서도 시장보다 강한가?
     — 그날 전체(전일 종가→종가)와 '프리장 가격→종가'(프리장에서 이미 오른 뒤에도 이어지나) 둘 다.
분야 구성 = 지금 한투 관심그룹(알림·보관용 그룹 제외), 종목 일봉 = 리나 kr_stock_daily_data(이름 기준),
프리장 = backtest/data/nxt_premarket.db(2025-11~, NXT 상장 종목만). 분야 등락 = 구성 종목 평균, '시장 대비' = 그날 전 분야 평균과의 차.
각 신호로 분야를 줄 세워 상위 5 / 하위 5의 시장 대비 성적, 기간 앞/뒤 반.
실행: venv/bin/python backtest/sector_signal_validate.py
"""
import os
import sqlite3
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(BASE, "core"), os.path.join(BASE, "intelligence")]
DAILY_DB = os.path.join(BASE, "lina_bot", "kr_theme_finance.db")
PRE_DB = os.path.join(BASE, "backtest", "data", "nxt_premarket.db")
MW_DB = os.path.join(BASE, "intelligence", "money_weather.db")
NOT_SECTOR = {"isee5", "sub", "new", "끼", "v알림", "신규", "신규추천", "서브", "고정"}
# 분야 이름 키워드 → 미국 ETF(먼저 맞는 것 하나)
US_MAP = [("반도체", "SMH"), ("방산", "ITA"), ("우주", "ITA"), ("원전", "URA"), ("로봇", "BOTZ"), ("2차전지", "LIT"),
          ("신재생", "TAN"), ("태양", "TAN"), ("ESS", "TAN"), ("바이오", "XLV"), ("제약", "XLV"), ("의료", "XLV"),
          ("화장품", "XLP"), ("음식", "XLP"), ("전력", "XLU"), ("전선", "XLI"), ("조선", "XLI"), ("기계", "XLI"),
          ("철강", "XLB"), ("화학", "XLB"), ("통신", "XLC"), ("게임", "XLC"), ("인터넷", "XLC"), ("금융", "XLF"),
          ("은행", "XLF"), ("증권", "XLF"), ("자동차", "XLY"), ("유통", "XLY"), ("건설", "XLI"), ("에너지", "XLE")]


def us_etf(group: str):
    return next((etf for kw, etf in US_MAP if kw.lower() in group.lower()), None)


def load():
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE, ".env"))
    from kis_api import KisAPI
    import sector_watch as sw
    groups = {g: v for g, v in sw.load_groups(KisAPI(), sw.hts_id()).items() if g.strip().lower() not in NOT_SECTOR}
    names = {n for v in groups.values() for _, n in v}
    d = sqlite3.connect(DAILY_DB)
    closes, opens = {}, {}
    for n, dt, c, o in d.execute("SELECT stock_name, date, close_price, open_price FROM kr_stock_daily_data "
                                 "WHERE date >= '2025-10-20'"):
        if n in names and c:
            closes.setdefault(n, {})[dt] = c
            if o:
                opens.setdefault(n, {})[dt] = o
    d.close()
    p = sqlite3.connect(PRE_DB)
    pre = {(dt, code): price for dt, code, price in p.execute("SELECT date, code, price FROM pre WHERE price > 0")}
    p.close()
    m = sqlite3.connect(MW_DB)
    us = {}
    for sym, dt, close in m.execute("SELECT symbol, date, close FROM global_daily WHERE area IN ('미국 섹터','미국 테마')"):
        us.setdefault(sym, {})[dt] = close
    m.close()
    return groups, closes, opens, pre, us


def main():
    groups, closes, opens, pre, us = load()
    days = sorted({dt for v in closes.values() for dt in v})
    print(f"분야 {len(groups)}개 · 거래일 {days[0]}~{days[-1]} {len(days)}일 · 미국 연결 "
          f"{sum(1 for g in groups if us_etf(g))}/{len(groups)} 분야")
    rows = {"us": [], "pre_day": [], "pre_cont": [], "pre_open": [], "us_open": []}
    for i in range(1, len(days)):
        d, prev = days[i], days[i - 1]
        g_day, g_pre, g_cont, g_us, g_oc = {}, {}, {}, {}, {}
        for g, members in groups.items():
            rs, ps, cs, oc = [], [], [], []
            for code, n in members:
                c = closes.get(n, {})
                if d in c and prev in c and c[prev]:
                    rs.append(c[d] / c[prev] - 1)
                    o = opens.get(n, {}).get(d)
                    if o:
                        oc.append(c[d] / o - 1)          # 본장 시가→종가(우리가 9시에 사면 그날 얻는 것)
                    pp = pre.get((d, code))
                    if pp:
                        ps.append(pp / c[prev] - 1)
                        cs.append(c[d] / pp - 1)
            if len(rs) >= 3:
                g_day[g] = sum(rs) / len(rs) * 100
            if len(oc) >= 3:
                g_oc[g] = sum(oc) / len(oc) * 100
            if len(ps) >= 2:
                g_pre[g], g_cont[g] = sum(ps) / len(ps) * 100, sum(cs) / len(cs) * 100
            etf = us_etf(g)
            if etf and etf in us:
                s = us[etf]
                ud = [x for x in sorted(s) if x < d]          # 한국 d일 아침 전에 끝난 미국 장
                if len(ud) >= 2 and s[ud[-2]]:
                    g_us[g] = (s[ud[-1]] / s[ud[-2]] - 1) * 100
        if len(g_day) < 10:
            continue
        avg = sum(g_day.values()) / len(g_day)

        def ends(sig, target, tavg):
            ks = [g for g in sig if g in target]
            if len(ks) < 10:
                return None
            ks.sort(key=lambda g: -sig[g])
            top, bot = ks[:5], ks[-5:]
            return (d, sum(target[g] for g in top) / 5 - tavg, sum(target[g] for g in bot) / 5 - tavg)

        r = ends(g_us, g_day, avg)
        if r:
            rows["us"].append(r)
        r = ends(g_pre, g_day, avg)
        if r:
            rows["pre_day"].append(r)
        if len(g_oc) >= 10:
            oavg = sum(g_oc.values()) / len(g_oc)
            r = ends(g_pre, g_oc, oavg)
            if r:
                rows["pre_open"].append(r)
            r = ends(g_us, g_oc, oavg)
            if r:
                rows["us_open"].append(r)
        if g_cont:
            cavg = sum(g_cont.values()) / len(g_cont)
            r = ends(g_pre, g_cont, cavg)
            if r:
                rows["pre_cont"].append(r)
    label = {"us": "① 전날 미국 섹터 강한 분야 → 오늘 분야 등락(전일종가→종가)",
             "pre_day": "② 프리장 강한 분야 → 오늘 분야 등락(전일종가→종가)",
             "pre_cont": "② 프리장 강한 분야 → 프리장 가격→종가(프리장에서 오른 뒤에도 이어지나 — NXT 호가 얇아 왜곡 큼)",
             "pre_open": "②' 프리장 강한 분야 → 본장 시가→종가(9시에 사면)",
             "us_open": "①' 전날 미국 섹터 강한 분야 → 본장 시가→종가(9시에 사면)"}
    for k, rs in rows.items():
        if not rs:
            print(f"{label[k]}: 표본 없음")
            continue
        h = len(rs) // 2

        def f(L, i):
            return sum(x[i] for x in L) / len(L)

        def w(L, i):
            return sum(x[i] > 0 for x in L) / len(L) * 100
        print(f"\n{label[k]} — {len(rs)}일 ({rs[0][0]}~{rs[-1][0]})")
        print(f"   상위5 시장 대비 {f(rs, 1):+.2f}%p (이긴 날 {w(rs, 1):.0f}%) · 앞 {f(rs[:h], 1):+.2f} / 뒤 {f(rs[h:], 1):+.2f}")
        print(f"   하위5 시장 대비 {f(rs, 2):+.2f}%p (이긴 날 {w(rs, 2):.0f}%) · 앞 {f(rs[:h], 2):+.2f} / 뒤 {f(rs[h:], 2):+.2f}")


if __name__ == "__main__":
    main()
