"""
lina_watch_view.py — 리나등록 종목 한눈에 보기 (대시보드 /lina, 2026-10-20 대장 요청)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
대장: "리나에 등록한 종목들만 스마트폰에서 한 번에 볼 수 있게 — 매번 리나를 확인하거나 종목이 많으면 놓친다."
→ manual_watch_state.json(리나 등록 목록) + 한투 복수시세(30종목 한 번 호출)로 종목마다
   지금가·수익률·평가손익과 '리나 자동익절이 지금 어느 단계인지(추적 전/추적 중/30% 익절 뒤)'와
   다음 매도선까지 얼마 남았는지를 보여줌. 보기 전용 — 주문 버튼 없음.
접속: 폰에 Tailscale 켜고 http://100.81.218.118:5000/lina (홈 화면에 추가하면 앱처럼)
"""
import os
import sys
import time
import datetime

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_BASE, "lina_bot"))
import manual_autosell as A  # noqa: E402

STATE = os.path.join(_BASE, "lina_bot", "manual_watch_state.json")
NEAR_PCT = 1.0          # 매도선까지 이만큼(%) 이내면 '근접' 표시
CACHE_SEC = 15
_cache = {"t": 0.0, "px": {}}
_api = None


def row(key: str, w: dict, q: dict) -> dict:
    """등록 1건 + 시세 → 화면 한 줄. q = get_multi_price 한 종목({"price","chg"}), 없으면 {}."""
    code = w.get("code") or key.split("@")[0]
    entry = float(w.get("entry_price") or 0)
    price = float(q.get("price") or 0)
    qty = w.get("qty") or 0
    peak = w.get("peak_price")
    stage = w.get("stage", 1 if peak else 0)
    r = {"code": code, "name": w.get("name", code), "account": "데이봇" if w.get("account") == "daybot" else "키움",
         "entry": entry, "price": price, "chg": q.get("chg"), "qty": qty, "rate": None, "pnl": None,
         "stage": stage, "status": "", "line": None, "gap": None, "near": False,
         "registered": (w.get("registered_at") or "")[5:16]}
    if entry <= 0 or price <= 0:
        r["status"] = "시세 없음"
        return r
    r["rate"] = (price / entry - 1) * 100
    if qty:
        r["pnl"] = (price - entry) * qty
    if peak is None:
        r["line"] = entry * (1 + A.START_PCT / 100)
        r["gap"] = (r["line"] / price - 1) * 100
        r["status"] = f"추적 전 · +{A.START_PCT:g}%({r['line']:,.0f})부터 추적"
        return r
    peak = max(float(peak), price)
    trail = A.TRAIL1_PCT if stage <= 1 else A.TRAIL2_PCT
    r["line"] = peak * (1 - trail / 100)
    r["gap"] = (price / r["line"] - 1) * 100          # 지금가가 매도선보다 몇 % 위에 있나
    r["near"] = r["gap"] <= NEAR_PCT
    head = "추적 중" if stage <= 1 else "30% 익절 뒤"
    r["status"] = f"{head} · 고점 {peak:,.0f} → 매도선 {r['line']:,.0f}(-{trail:g}%)"
    if stage <= 1:
        r["status"] += f" · +{A.TP1_PCT:g}%({entry * (1 + A.TP1_PCT / 100):,.0f})에 30% 익절"
    return r


def summary(rows: list) -> dict:
    held = [x for x in rows if x["pnl"] is not None]
    cost = sum(x["entry"] * x["qty"] for x in held)
    pnl = sum(x["pnl"] for x in held)
    return {"count": len(rows), "pnl": pnl, "rate": (pnl / cost * 100) if cost else None,
            "near": sum(1 for x in rows if x["near"]), "mode": A.mode()}


def _prices(codes: list) -> dict:
    global _api
    if time.time() - _cache["t"] < CACHE_SEC and set(codes) <= set(_cache["px"]):
        return _cache["px"]
    try:
        if _api is None:
            from kis_api import KisAPI
            _api = KisAPI()
        px = _api.get_multi_price(codes) if codes else {}
        _cache.update(t=time.time(), px=px)
    except Exception as e:
        print(f"⚠️ [리나보기] 시세 조회 실패: {e}")
    return _cache["px"]


def build() -> dict:
    from common_utils import read_state
    watches = {k: w for k, w in (read_state(STATE, default={}) or {}).items() if isinstance(w, dict)}
    px = _prices(sorted({w.get("code") or k.split("@")[0] for k, w in watches.items()}))
    rows = [row(k, w, px.get(w.get("code") or k.split("@")[0], {})) for k, w in watches.items()]
    rows.sort(key=lambda x: (not x["near"], -(x["rate"] if x["rate"] is not None else -999)))
    return {"rows": rows, "summary": summary(rows), "at": datetime.datetime.now().strftime("%H:%M:%S")}


HTML = """<!DOCTYPE html>
<html lang="ko"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="apple-mobile-web-app-capable" content="yes"><meta name="mobile-web-app-capable" content="yes">
<meta name="theme-color" content="#0f1117">
<title>리나 등록종목</title>
<style>
 :root{--bg:#0f1117;--card:#1a1d27;--bd:#2a2d3a;--tx:#e2e8f0;--sub:#94a3b8;--up:#ef4444;--dn:#4dabf7;--warn:#f59e0b}
 *{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font-family:-apple-system,'Noto Sans KR',sans-serif;padding:12px 16px 40px}
 h1{font-size:18px;margin:4px 0 2px}.sub{color:var(--sub);font-size:12px}
 .top{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:12px;margin:10px 0;display:flex;justify-content:space-between;align-items:center}
 .big{font-size:22px;font-weight:700}.card{background:var(--card);border:1px solid var(--bd);border-radius:12px;padding:12px;margin:8px 0}
 .card.near{border-color:var(--warn);box-shadow:0 0 0 1px var(--warn)}
 .r1{display:flex;justify-content:space-between;align-items:baseline}.nm{font-size:16px;font-weight:700}
 .tag{font-size:11px;color:var(--sub);border:1px solid var(--bd);border-radius:6px;padding:1px 5px;margin-left:6px}
 .rate{font-size:18px;font-weight:700}.up{color:var(--up)}.dn{color:var(--dn)}
 .r2{display:flex;justify-content:space-between;color:var(--sub);font-size:13px;margin-top:4px}
 .st{font-size:12px;color:var(--sub);margin-top:6px;line-height:1.4}.badge{color:var(--warn);font-weight:700}
 .bar{height:4px;background:var(--bd);border-radius:2px;margin-top:6px;overflow:hidden}.bar i{display:block;height:100%;background:var(--warn)}
</style></head><body>
<h1>📋 리나 등록종목</h1><div class="sub" id="at">불러오는 중…</div>
<div class="top"><div><div class="sub">평가손익(수량 입력 종목)</div><div class="big" id="pnl">-</div></div>
<div style="text-align:right"><div class="sub" id="mode"></div><div class="sub" id="cnt"></div></div></div>
<div id="list"></div>
<script>
const f=n=>n==null?'-':Math.round(n).toLocaleString('ko-KR');
const s=(n,d=2)=>n==null?'-':(n>0?'+':'')+n.toFixed(d)+'%';
const c=n=>n==null?'':(n>0?'up':n<0?'dn':'');
const MODE={paper:'🧪 연습(주문 X)',live:'💰 실제 매도',off:'🔔 알림만'};
async function load(){
 try{
  const d=await (await fetch('api/lina')).json(),S=d.summary;
  document.getElementById('at').textContent=d.at+' 기준 · 15초마다 갱신';
  const p=document.getElementById('pnl');p.textContent=(S.pnl>0?'+':'')+f(S.pnl)+'원 ('+s(S.rate)+')';p.className='big '+c(S.pnl);
  document.getElementById('mode').textContent=MODE[S.mode]||S.mode;
  document.getElementById('cnt').textContent=S.count+'종목'+(S.near?' · ⚠️ 매도선 근접 '+S.near:'');
  document.getElementById('list').innerHTML=d.rows.map(x=>{
   const w=x.gap==null?0:Math.max(0,Math.min(100,100-x.gap*20));
   return `<div class="card ${x.near?'near':''}">
    <div class="r1"><div><span class="nm">${x.name}</span><span class="tag">${x.account}</span></div>
     <div class="rate ${c(x.rate)}">${s(x.rate)}</div></div>
    <div class="r2"><span>${f(x.price)}원 <span class="${c(x.chg)}">(오늘 ${s(x.chg)})</span></span>
     <span class="${c(x.pnl)}">${x.pnl==null?'수량 없음':(x.pnl>0?'+':'')+f(x.pnl)+'원'}</span></div>
    <div class="r2"><span>평단 ${f(x.entry)} · ${x.qty||'-'}주</span><span>등록 ${x.registered}</span></div>
    <div class="st">${x.near?'<span class="badge">⚠️ 매도선 근접 </span>':''}${x.status}</div>
    ${x.stage>0&&x.gap!=null?`<div class="bar"><i style="width:${w}%"></i></div>`:''}</div>`}).join('')||'<div class="card">등록된 종목이 없어요</div>';
 }catch(e){document.getElementById('at').textContent='⚠️ 불러오기 실패 — 다시 시도 중';}
}
load();setInterval(load,15000);
</script></body></html>"""
