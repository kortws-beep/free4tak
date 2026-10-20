"""
lina_watch_view.py — 리나등록 종목 한눈에 보기 (대시보드 /lina, 2026-10-20 대장 요청)
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
대장: "리나에 등록한 종목들만 스마트폰에서 한 번에 볼 수 있게 — 매번 리나를 확인하거나 종목이 많으면 놓친다."
→ manual_watch_state.json(리나 등록 목록) + 한투 복수시세(30종목 한 번 호출)로 종목마다
   지금가·수익률·평가손익과 '리나 자동익절이 지금 어느 단계인지(추적 전/추적 중/30% 익절 뒤)'와
   다음 매도선까지 얼마 남았는지를 보여줌.
   ★ 10-20 대장: 매도 버튼 추가 — [전량 매도](시장가, 정규장 09:00~15:20만, PIN 필요) 접수되면 리나 등록 해제.
   [해제만](HTS에서 이미 판 경우). 수량은 '등록 수량'과 계좌 매도가능수량 중 작은 값 — 리나 자동익절과 같은 원칙
   (키움 계좌엔 대장 장기 보유분이 있어서 등록 수량 없이 '전부' 팔면 안 됨). 결과는 리나 채널로 알림.
접속: 폰에 Tailscale 켜고 http://100.81.218.118:5000/lina (홈 화면에 추가하면 앱처럼)
"""
import os
import sys
import time
import hmac
import datetime
import threading

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_BASE, "lina_bot"))
import manual_autosell as A  # noqa: E402

STATE = os.path.join(_BASE, "lina_bot", "manual_watch_state.json")
NEAR_PCT = 1.0          # 매도선까지 이만큼(%) 이내면 '근접' 표시
CACHE_SEC = 15
FEE_DRAG_PCT = 0.224   # lina_bot.MANUAL_WATCH_FEE_DRAG_PCT와 같은 값(해제 수익률 보정)
LINA_CHANNEL = "1508487747508240525"   # lina_bot.REPORT_CHANNEL_ID(리나등록 알림 채널)
_cache = {"t": 0.0, "px": {}}
_api = None
_sell_lock = threading.Lock()     # 버튼 두 번 눌러 두 번 팔리는 것 방지


def row(key: str, w: dict, q: dict) -> dict:
    """등록 1건 + 시세 → 화면 한 줄. q = get_multi_price 한 종목({"price","chg"}), 없으면 {}."""
    code = w.get("code") or key.split("@")[0]
    entry = float(w.get("entry_price") or 0)
    price = float(q.get("price") or 0)
    qty = w.get("qty") or 0
    peak = w.get("peak_price")
    stage = w.get("stage", 1 if peak else 0)
    r = {"key": key, "code": code, "name": w.get("name", code), "account": "데이봇" if w.get("account") == "daybot" else "키움",
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


def _brokers() -> dict:
    from kiwoom_api import KiwoomAPI
    from kis_api import KisAPI
    return {"kiwoom": KiwoomAPI(), "daybot": A.KisBroker(KisAPI())}


def _notify(msg: str):
    try:
        from notifier import Notifier
        n = Notifier(name="lina")
        if os.getenv("DISCORD_BOT_TOKEN_N"):
            n.bot_token, n.channel = os.getenv("DISCORD_BOT_TOKEN_N"), LINA_CHANNEL
        n.send(msg, critical=True)
    except Exception as e:
        print(f"⚠️ [리나보기] 알림 실패: {e}")


def _log(code: str, w: dict, sell_price, profit_rate):
    """리나 해제와 같은 이력 테이블(manual_watch_log)에 남김 — 나중 분석용."""
    try:
        import sqlite3
        conn = sqlite3.connect(os.path.join(_BASE, "lina_bot", "three_month_leader_log.db"), timeout=10)
        try:
            conn.execute("""CREATE TABLE IF NOT EXISTS manual_watch_log (
                ts TEXT, event TEXT, code TEXT, name TEXT, entry_price REAL,
                sell_price REAL, profit_rate REAL, peak_price REAL, registered_at TEXT)""")
            conn.execute("INSERT INTO manual_watch_log VALUES (?,?,?,?,?,?,?,?,?)", (
                datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "deregister", code, w.get("name"),
                w.get("entry_price"), sell_price, profit_rate, w.get("peak_price"), w.get("registered_at")))
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        print(f"⚠️ [리나보기] 이력 기록 실패: {e}")


def _pop(key: str):
    """등록 해제 — 락 잡고 최신 파일에서 빼기(리나 루프의 병합 저장은 없어진 종목을 되살리지 않음)."""
    from common_utils import _state_lock, _read_state_raw, _write_state_raw
    with _state_lock(STATE):
        latest = _read_state_raw(STATE, {})
        w = latest.pop(key, None)
        if w is not None:
            _write_state_raw(STATE, latest)
    return w


def act(key: str, action: str, pin: str, hhmm: str = None, brokers=None) -> dict:
    """대시보드 버튼 → {"ok", "msg"}. action: "sell"(전량 시장가 매도 후 해제) | "release"(해제만)."""
    want = os.getenv("DASHBOARD_PIN", "")
    if not want:
        return {"ok": False, "msg": "PIN이 설정 안 돼 있어 버튼이 잠겨 있어요(.env DASHBOARD_PIN)"}
    if not hmac.compare_digest(str(pin or ""), want):
        return {"ok": False, "msg": "PIN이 틀렸어요", "pin": False}
    with _sell_lock:
        from common_utils import read_state
        w = (read_state(STATE, default={}) or {}).get(key)
        if not isinstance(w, dict):
            return {"ok": False, "msg": "이미 해제된 종목이에요"}
        code, name = w.get("code") or key.split("@")[0], w.get("name", key)
        price = float(_prices([code]).get(code, {}).get("price") or 0) if brokers is None else 0.0
        if action == "release":
            _pop(key)
            _log(code, w, None, None)
            _notify(f"📱 [대시보드] {name}({code}) 리나 등록 해제(매도 없이)")
            return {"ok": True, "msg": f"{name} 등록 해제했어요"}
        if action != "sell":
            return {"ok": False, "msg": "알 수 없는 동작"}
        hhmm = hhmm or datetime.datetime.now().strftime("%H%M")
        if not A.in_session(hhmm):
            return {"ok": False, "msg": "시장가 매도는 정규장 09:00~15:20에만 돼요"}
        reg = int(w.get("qty") or 0)
        if reg <= 0:
            return {"ok": False, "msg": "등록 수량이 없어 주문 안 해요(계좌의 다른 보유분까지 팔릴 수 있어서) — HTS에서 팔고 [해제만]"}
        br = (brokers or _brokers()).get(w.get("account", "kiwoom"))
        label = getattr(br, "label", "키움")
        avail = int(((br.holdings() or {}).get(code) or {}).get("avail", 0) or 0)
        qty = min(reg, avail)
        if qty <= 0:
            return {"ok": False, "msg": f"{label} 매도가능수량 0 — 이미 팔았으면 [해제만]"}
        ok, msg = br.sell_market(code, qty)
        if not ok:
            _notify(f"🚨 [대시보드 매도] {name}({code}) {qty}주 주문 실패 — {msg}")
            return {"ok": False, "msg": f"주문 실패: {msg}"}
        entry = float(w.get("entry_price") or 0)
        rate = ((price / entry - 1) * 100 - FEE_DRAG_PCT) if price and entry else None
        _pop(key)
        _log(code, w, price or None, rate)
        _notify(f"{'💰' if (rate or 0) >= 0 else '💔'} [대시보드 매도] {name}({code}) {label} 시장가 {qty}주 접수"
                + (f" @{price:,.0f}원 부근 · 평단 {entry:,.0f} 대비 {rate:+.2f}%(수수료 반영)" if rate is not None else "")
                + (f"\n   ⚠️ 등록 {reg}주 중 매도가능 {avail}주만 팔았어요" if qty < reg else "")
                + " — 리나 등록 해제")
        return {"ok": True, "msg": f"{name} {qty}주 시장가 매도 접수 · 등록 해제" + (f" ({rate:+.2f}%)" if rate is not None else "")}


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
 .btns{display:flex;gap:8px;margin-top:10px}.btns button{flex:1;padding:10px;border-radius:8px;border:1px solid var(--bd);background:#232735;color:var(--tx);font-size:14px}
 .btns .sell{background:#3b1d22;border-color:#7f1d1d;color:#fecaca;font-weight:700}
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
  R=d.rows;document.getElementById('list').innerHTML=d.rows.map((x,i)=>{
   const w=x.gap==null?0:Math.max(0,Math.min(100,100-x.gap*20));
   return `<div class="card ${x.near?'near':''}">
    <div class="r1"><div><span class="nm">${x.name}</span><span class="tag">${x.account}</span></div>
     <div class="rate ${c(x.rate)}">${s(x.rate)}</div></div>
    <div class="r2"><span>${f(x.price)}원 <span class="${c(x.chg)}">(오늘 ${s(x.chg)})</span></span>
     <span class="${c(x.pnl)}">${x.pnl==null?'수량 없음':(x.pnl>0?'+':'')+f(x.pnl)+'원'}</span></div>
    <div class="r2"><span>평단 ${f(x.entry)} · ${x.qty||'-'}주</span><span>등록 ${x.registered}</span></div>
    <div class="st">${x.near?'<span class="badge">⚠️ 매도선 근접 </span>':''}${x.status}</div>
    ${x.stage>0&&x.gap!=null?`<div class="bar"><i style="width:${w}%"></i></div>`:''}
    <div class="btns"><button class="sell" onclick="go(${i},'sell')">전량 매도</button>
     <button onclick="go(${i},'release')">해제만</button></div></div>`}).join('')||'<div class="card">등록된 종목이 없어요</div>';
 }catch(e){document.getElementById('at').textContent='⚠️ 불러오기 실패 — 다시 시도 중';}
}
function pin(reset){
 let p=null;try{p=localStorage.getItem('lina_pin')}catch(e){}
 if(!p||reset){p=prompt('대시보드 PIN');if(!p)return null;try{localStorage.setItem('lina_pin',p)}catch(e){}}
 return p;
}
let R=[];
async function go(i,action){
 const x=R[i],key=x.key;
 const amt=x.qty&&x.price?' (약 '+f(x.qty*x.price)+'원, '+s(x.rate)+')':'';
 const q=action==='sell'?`${x.name} ${x.qty||'?'}주 시장가 전량 매도할까요?${amt}\n접수되면 리나 등록도 해제돼요.`
                        :`${x.name} 리나 등록만 해제할까요? (매도 주문 안 나가요)`;
 if(!confirm(q))return;
 let p=pin(false);if(!p)return;
 let d=await (await fetch('api/lina/act',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key,action,pin:p})})).json();
 if(d.pin===false){p=pin(true);if(!p)return;
  d=await (await fetch('api/lina/act',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key,action,pin:p})})).json();}
 alert((d.ok?'✅ ':'⚠️ ')+d.msg);load();
}
load();setInterval(load,15000);
</script></body></html>"""
