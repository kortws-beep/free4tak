"""
weather_view.py — 대시보드 '🌦️ 날씨' 탭: 자금시장 날씨판·키키 관제·분야 날씨·이벤트 (2026-11-17, 대장 "데쉬보드에도 날씨판")
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
dashboard.py의 /weather(화면)·/api/weather(데이터). 키키가 아침 07:10에 정한 것과 같은 계산(weather_board·sector_weather)을
지금 기록으로 다시 보여주고, 봇 상태 파일에 적힌 '오늘 날씨'(키키가 실제로 건 태세)도 같이 보여 줌. 읽기 전용(버튼 없음).
"""
import datetime
import json
import os
import sqlite3
import sys

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _d in ("core", "intelligence"):
    if os.path.join(_BASE, _d) not in sys.path:
        sys.path.insert(0, os.path.join(_BASE, _d))


def build() -> dict:
    import weather_board
    import sector_weather
    import money_weather
    import weather_control
    from common_utils import read_state
    day = datetime.date.today().isoformat()
    b = weather_board.board(day)
    sec = sector_weather.weather(day)
    bots = {}
    for bot, f in weather_control.STATE_FILES.items():
        st = read_state(os.path.join(_BASE, f), default={}) or {}
        w = weather_control.today_weather(st, day)
        bots[bot] = {"weather": w or "(오늘 설정 전)", "action": weather_control.action_for(bot, w) if w else "",
                     "paused": bool(st.get("paused")), "pause_reason": st.get("pause_reason") or "",
                     "override": bool((st.get("weather") or {}).get("override"))}
    hist = []
    try:
        c = sqlite3.connect(f"file:{weather_board.MW_DB}?mode=ro", uri=True, timeout=5)
        hist = c.execute("SELECT date, layer, score, state FROM board_daily WHERE layer IN ('global','stock_total','coin_total') "
                         "ORDER BY date DESC LIMIT 30").fetchall()
        c.close()
    except Exception:
        pass
    by = {}
    for d, layer, sc, st in hist:
        by.setdefault(d, {})[layer] = f"{st} ({sc:+d})"
    return {
        "date": day, "now": datetime.datetime.now().strftime("%H:%M"),
        "layers": [{"name": n, "score": b[k][0], "state": b[k][1], "items": [list(i) for i in b[k][2]]}
                   for k, n in (("global", "1층 글로벌 자금"), ("kr", "2층 한국 주식"), ("coin", "2층 코인"))],
        "stock": b["stock_total"][1], "coin": b["coin_total"][1], "bots": bots,
        "sectors": [{"grp": g, "score": sc, "state": st, "leader": ld or "",
                     "why": " · ".join(f"{n} {why}" for n, s, why in it if s)} for g, sc, st, it, ld in sec],
        "events": [list(e) for e in money_weather.upcoming(21, day)],
        "history": sorted(([d, v.get("global", ""), v.get("stock_total", ""), v.get("coin_total", "")] for d, v in by.items()),
                          reverse=True)[:10],
    }


HTML = """<!DOCTYPE html>
<html lang="ko"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="theme-color" content="#0f1117"><title>자금시장 날씨판</title>
<style>
 :root{--bg:#0f1117;--card:#1a1d27;--bd:#2a2d3a;--tx:#e2e8f0;--sub:#94a3b8;--g:#4ade80;--y:#facc15;--o:#fb923c;--r:#f87171}
 *{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font-family:-apple-system,'Noto Sans KR',sans-serif;padding:4px 16px 40px}
 h1{font-size:18px;margin:6px 0 2px}.sub{color:var(--sub);font-size:12px;margin-bottom:10px}
 .card{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:12px;margin:10px 0}
 .big{display:flex;gap:8px}.big .card{flex:1;margin:0;text-align:center}.big b{font-size:20px;display:block;margin-top:4px}
 h2{font-size:14px;margin:0 0 8px}.it{font-size:13px;padding:3px 0;color:var(--sub)}.it b{color:var(--tx);font-weight:600}
 .p{color:var(--g)}.m{color:var(--r)}table{width:100%;border-collapse:collapse;font-size:13px}
 td,th{padding:6px 4px;border-bottom:1px solid var(--bd);text-align:left;vertical-align:top}th{color:var(--sub);font-weight:500;font-size:11px}
 .small{font-size:11px;color:var(--sub)}.warn{font-size:11px;color:var(--o);margin-top:6px}
</style></head><body>
<h1>🌦️ 자금시장 날씨판</h1><div class="sub" id="sub">불러오는 중…</div>
<div class="big"><div class="card">주식 봇 태세<b id="stock">-</b></div><div class="card">코인 봇 태세<b id="coin">-</b></div></div>
<div class="card"><h2>🗼 키키 관제 — 봇에 실제로 건 오늘 태세</h2><div id="bots"></div>
 <div class="small">🔴쉼=신규매수 정지(매도·손절 계속) · 🟠방어=daybot 음봉·이벤트 엔진만/sbot 1슬롯/cbot BTC 오를 때만 · 🟡🟢=자유 · 키키 <b>!날씨해제</b>로 오늘 하루 풀기</div></div>
<div id="layers"></div>
<div class="card"><h2>🗺️ 분야 날씨 (관심그룹, 어제까지 기록)</h2><table id="sec"></table>
 <div class="warn">⚠️ 검증 중 — 하락장 28일 기록으론 분야 신호가 약해 아직 봇 진입에 안 씀(관찰·기록만)</div></div>
<div class="card"><h2>📅 다가오는 이벤트 (출처 포함)</h2><table id="ev"></table></div>
<div class="card"><h2>🕘 최근 날씨 기록</h2><table id="hist"></table></div>
<script>
const nm={sbot:'스윙(sbot)',daybot:'단타(daybot)',cbot:'코인(cbot)'};
const esc=s=>String(s??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
fetch('api/weather').then(r=>r.json()).then(d=>{
 document.getElementById('sub').textContent=d.date+' · '+d.now+' 기준 · 1층(글로벌)은 5년 검증 통과, 2층은 나쁠 때 한 칸 더 조심만';
 document.getElementById('stock').textContent=d.stock;document.getElementById('coin').textContent=d.coin;
 document.getElementById('bots').innerHTML=Object.entries(d.bots).map(([k,v])=>`<div class="it"><b>${nm[k]}</b> ${esc(v.weather)} ${v.action?'→ '+esc(v.action):''}${v.paused?' · <span class="m">정지('+esc(v.pause_reason||'수동')+')</span>':''}${v.override?' · 대장 해제':''}</div>`).join('');
 document.getElementById('layers').innerHTML=d.layers.map(L=>`<div class="card"><h2>${esc(L.name)} — ${esc(L.state)} (${L.score>0?'+':''}${L.score})</h2>`+
   L.items.map(i=>`<div class="it"><span class="${i[1]>0?'p':(i[1]<0?'m':'')}">${i[1]>0?'＋':(i[1]<0?'－':'·')}</span> <b>${esc(i[0])}</b> ${esc(i[2])}</div>`).join('')+'</div>').join('');
 document.getElementById('sec').innerHTML='<tr><th>분야</th><th>날씨</th><th>대장주</th><th>이유</th></tr>'+d.sectors.map(s=>`<tr><td>${esc(s.grp)}</td><td>${esc(s.state)} ${s.score>0?'+':''}${s.score}</td><td>${esc(s.leader)}</td><td class="small">${esc(s.why)}</td></tr>`).join('');
 document.getElementById('ev').innerHTML='<tr><th>날짜</th><th>일정</th><th>출처</th></tr>'+d.events.map(e=>`<tr><td>${esc(e[0].slice(5))} ${esc(e[1])}</td><td><b>${esc(e[2])}</b> ${esc(e[3])}</td><td class="small">${esc(e[6])}</td></tr>`).join('');
 document.getElementById('hist').innerHTML='<tr><th>날짜</th><th>1층 글로벌</th><th>주식</th><th>코인</th></tr>'+d.history.map(h=>`<tr><td>${esc(h[0].slice(5))}</td><td>${esc(h[1])}</td><td>${esc(h[2])}</td><td>${esc(h[3])}</td></tr>`).join('');
}).catch(e=>{document.getElementById('sub').textContent='불러오기 실패: '+e});
</script></body></html>"""
