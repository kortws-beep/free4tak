"""
heatmap_view.py — 대시보드 '🗺️ 섹터맵' 탭: 한투 관심그룹(대장 분야) 히트맵 (2026-11-24, 대장 "우리만의 맵")
================================================================
[이 파일이 하는 일 — 비개발자용 설명]
dashboard.py의 /map(화면)·/api/map(데이터). 데이터는 intelligence/kr_heatmap.py.
큰 칸 = 분야(관심그룹), 작은 칸 = 그 안 종목. 칸 크기는 거래대금(기본)·시가총액·균등 중 고름, 색은 오늘 등락률.
분야를 누르면 그 분야만 크게, new·SUB·isee5(분야 아닌 관심그룹)도 골라 볼 수 있음. 아래엔 분야 순위표. 30초마다 새로고침.
"""

HTML = """<!DOCTYPE html>
<html lang="ko"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="theme-color" content="#0f1117"><title>우리 섹터맵</title>
<style>
 :root{--bg:#0f1117;--card:#1a1d27;--bd:#2a2d3a;--tx:#e2e8f0;--sub:#94a3b8;--g:#4ade80;--r:#f87171}
 *{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font-family:-apple-system,'Noto Sans KR',sans-serif;padding:4px 16px 40px}
 h1{font-size:18px;margin:6px 0 2px}.sub{color:var(--sub);font-size:12px;margin-bottom:8px}
 .bar{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0}.bar button{background:var(--card);color:var(--sub);border:1px solid var(--bd);
  border-radius:8px;padding:5px 9px;font-size:12px;cursor:pointer}.bar button.on{color:var(--tx);border-color:var(--tx);font-weight:700}
 .idx{display:flex;gap:6px;margin:6px 0}.idx div{flex:1;background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:6px;text-align:center;font-size:12px}
 .idx b{display:block;font-size:15px}.p{color:var(--g)}.m{color:var(--r)}
 #map{position:relative;width:100%;height:420px;background:#11131a;border-radius:10px;overflow:hidden}
 .sec{position:absolute;border:1px solid #0f1117;overflow:hidden}
 .sh{position:absolute;left:0;right:0;top:0;height:16px;font-size:11px;line-height:16px;padding:0 4px;background:rgba(15,17,23,.75);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;cursor:pointer;z-index:2}
 .tile{position:absolute;border:1px solid rgba(15,17,23,.85);display:flex;flex-direction:column;align-items:center;justify-content:center;
  text-align:center;overflow:hidden;color:#fff;text-shadow:0 1px 2px rgba(0,0,0,.6);line-height:1.15}
 .tile .n{font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%;padding:0 2px}
 .legend{display:flex;flex-wrap:wrap;align-items:center;gap:4px;font-size:11px;color:var(--sub);margin:6px 0}.legend .sw{width:26px;height:10px;display:inline-block;border-radius:2px}
 table{width:100%;border-collapse:collapse;font-size:13px;margin-top:10px}td,th{padding:6px 4px;border-bottom:1px solid var(--bd);text-align:left}
 th{color:var(--sub);font-weight:500;font-size:11px}td.r,th.r{text-align:right}.small{font-size:11px;color:var(--sub)}
 tr.click{cursor:pointer}
</style></head><body>
<h1>🗺️ 우리 섹터맵</h1><div class="sub" id="sub">불러오는 중…</div>
<div class="idx" id="idx"></div>
<div class="bar" id="views"></div>
<div class="bar"><span class="small" style="align-self:center">칸 크기</span>
 <button data-s="value" class="on">거래대금</button><button data-s="cap">시가총액</button><button data-s="eq">균등</button></div>
<div id="map"></div>
<div class="legend">하락<span class="sw" style="background:#b91c1c"></span><span class="sw" style="background:#7f1d1d"></span><span class="sw" style="background:#3f3f46"></span>
 <span class="sw" style="background:#14532d"></span><span class="sw" style="background:#16a34a"></span><span class="sw" style="background:#22c55e"></span>상승 <span class="small">· ±5%↑ 가장 진함</span></div>
<div class="sub" id="br"></div>
<table id="tbl"></table>
<div class="small" style="margin-top:8px">분야 = 대장 한투 관심그룹 · 등락은 시총 가중(괄호 안 단순 평균) · 시세 네이버(30초) · 그룹 구성 30분마다 다시 읽음</div>
<script>
let D=null,view='all',size='value';
const esc=s=>String(s??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const sg=v=>v>0?'+':'';
function color(c){if(c==null)return '#3f3f46';const t=Math.max(-1,Math.min(1,c/5));
 if(Math.abs(c)<0.05)return '#3f3f46';
 return t>0?`rgb(${Math.round(20+(34-20)*t)},${Math.round(83+(197-83)*t)},${Math.round(45+(94-45)*t)})`
            :`rgb(${Math.round(63+(220-63)*-t)},${Math.round(63+(38-63)*-t)},${Math.round(70+(38-70)*-t)})`}
// squarified treemap: items [{w,...}] into rect → [{x,y,w,h,item}]
function squarify(items,x,y,w,h){const out=[];const tot=items.reduce((a,b)=>a+b.w,0);if(!tot||w<=0||h<=0)return out;
 const scale=w*h/tot;let rest=items.map(i=>({...i,a:i.w*scale})).filter(i=>i.a>0);
 while(rest.length){const short=Math.min(w,h);let row=[],best=Infinity;
  for(const it of rest){const r=[...row,it];const s=r.reduce((a,b)=>a+b.a,0);const mx=Math.max(...r.map(z=>z.a)),mn=Math.min(...r.map(z=>z.a));
   const worst=Math.max(short*short*mx/(s*s),(s*s)/(short*short*mn));if(worst<=best){best=worst;row=r}else break}
  const s=row.reduce((a,b)=>a+b.a,0);
  if(w>=h){const cw=s/h;let cy=y;for(const it of row){const ch=it.a/cw;out.push({x,y:cy,w:cw,h:ch,item:it});cy+=ch}x+=cw;w-=cw}
  else{const ch=s/w;let cx=x;for(const it of row){const cw2=it.a/ch;out.push({x:cx,y,w:cw2,h:ch,item:it});cx+=cw2}y+=ch;h-=ch}
  rest=rest.slice(row.length)}
 return out}
const wOf=m=>size==='cap'?(m.cap||1):size==='eq'?1:(m.value||1);
function tiles(el,mem,x,y,w,h){for(const r of squarify(mem.map(m=>({...m,w:wOf(m)})).sort((a,b)=>b.w-a.w),x,y,w,h)){
  const m=r.item,fs=Math.max(9,Math.min(16,Math.sqrt(r.w*r.h)/6));const t=document.createElement('div');t.className='tile';
  Object.assign(t.style,{left:r.x+'px',top:r.y+'px',width:r.w+'px',height:r.h+'px',background:color(m.chg),fontSize:fs+'px'});
  t.title=`${m.name} ${sg(m.chg)}${m.chg}% · 시총 ${(m.cap/1e12).toFixed(2)}조 · 거래대금 ${Math.round(m.value/1e8)}억`;
  if(r.w>34&&r.h>22)t.innerHTML=`<div class="n">${esc(m.name)}</div>`+(r.h>34?`<div>${sg(m.chg)}${m.chg.toFixed(2)}%</div>`:'');
  el.appendChild(t)}}
function draw(){const el=document.getElementById('map');el.innerHTML='';if(!D)return;
 el.style.height=Math.max(420,Math.round(window.innerHeight*0.68))+'px';const W=el.clientWidth,H=el.clientHeight;
 let secs=view==='all'?D.sectors:[...D.sectors,...D.watch].filter(s=>s.group===view);
 if(view!=='all'&&secs.length){tiles(el,secs[0].members,0,0,W,H);return}
 const items=secs.map(s=>({...s,w:size==='eq'?s.n:s.members.reduce((a,m)=>a+wOf(m),0)}));
 for(const r of squarify(items.sort((a,b)=>b.w-a.w),0,0,W,H)){const s=r.item;const box=document.createElement('div');box.className='sec';
  Object.assign(box.style,{left:r.x+'px',top:r.y+'px',width:r.w+'px',height:r.h+'px'});
  const hd=document.createElement('div');hd.className='sh';hd.innerHTML=`${esc(s.group)} <b class="${s.chg_w>0?'p':'m'}">${sg(s.chg_w)}${s.chg_w}%</b>`;
  hd.onclick=()=>setView(s.group);box.appendChild(hd);tiles(box,s.members,0,16,r.w,Math.max(0,r.h-16));el.appendChild(box)}}
function setView(v){view=v;document.querySelectorAll('#views button').forEach(b=>b.classList.toggle('on',b.dataset.v===v));draw();window.scrollTo(0,0)}
function render(){
 document.getElementById('sub').textContent=D.at+' 기준 · 한투 관심그룹 '+(D.sectors.length)+'개 분야';
 document.getElementById('idx').innerHTML=D.index.map(i=>`<div>${esc(i.name)}<b class="${i.chg>0?'p':'m'}">${i.price?.toLocaleString()}</b><span class="${i.chg>0?'p':'m'}">${sg(i.chg)}${i.chg}%</span></div>`).join('');
 const b=D.breadth;document.getElementById('br').innerHTML=`분야 종목 ${b.n}개 · <span class="p">상승 ${b.up}%</span> · 보합 ${b.flat}% · <span class="m">하락 ${b.down}%</span>`;
 document.getElementById('views').innerHTML=`<button data-v="all">🗺️ 분야 전체</button>`+D.watch.map(w=>`<button data-v="${esc(w.group)}">⭐ ${esc(w.group)}</button>`).join('')+
  (view!=='all'&&!D.watch.find(w=>w.group===view)?`<button data-v="${esc(view)}">🔎 ${esc(view)}</button>`:'');
 document.querySelectorAll('#views button').forEach(x=>{x.onclick=()=>setView(x.dataset.v);x.classList.toggle('on',x.dataset.v===view)});
 document.getElementById('tbl').innerHTML='<tr><th>분야</th><th class="r">등락</th><th class="r">↑/↓</th><th>대장(시총 1위)·최강</th></tr>'+
  D.sectors.map(s=>{const top=[...s.members].sort((a,c)=>c.chg-a.chg)[0];return `<tr class="click" data-g="${esc(s.group)}"><td>${esc(s.group)}</td>
  <td class="r ${s.chg_w>0?'p':'m'}">${sg(s.chg_w)}${s.chg_w}% <span class="small">(${sg(s.chg)}${s.chg})</span></td><td class="r">${s.up}/${s.down}</td>
  <td class="small">${esc(s.members[0].name)} ${sg(s.members[0].chg)}${s.members[0].chg}% · ${esc(top.name)} ${sg(top.chg)}${top.chg}%</td></tr>`}).join('');
 document.querySelectorAll('tr.click').forEach(r=>r.onclick=()=>setView(r.dataset.g));
 draw()}
document.querySelectorAll('[data-s]').forEach(x=>x.onclick=()=>{size=x.dataset.s;document.querySelectorAll('[data-s]').forEach(y=>y.classList.toggle('on',y===x));draw()});
function load(){fetch('api/map').then(r=>r.json()).then(d=>{D=d;render()}).catch(e=>{document.getElementById('sub').textContent='불러오기 실패: '+e})}
load();setInterval(load,30000);let rt;
window.addEventListener('resize',()=>{clearTimeout(rt);rt=setTimeout(draw,150)});   // 창·폰 회전 때 다시 그림
</script></body></html>"""
