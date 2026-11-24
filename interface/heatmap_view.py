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
 .wx{display:flex;flex-wrap:wrap;gap:6px;align-items:center;font-size:12px;margin:4px 0 6px}.wx .chip{background:var(--card);border:1px solid #facc15;
  border-radius:8px;padding:3px 8px;cursor:pointer}.wx .lab{color:var(--sub)}.sec.pick{outline:2px solid #facc15;outline-offset:-2px;z-index:1}
 .tile{position:absolute;border:1px solid rgba(15,17,23,.85);display:flex;flex-direction:column;align-items:center;justify-content:center;
  text-align:center;overflow:hidden;color:#fff;text-shadow:0 1px 2px rgba(0,0,0,.6);line-height:1.15}
 .tile .n{font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%;padding:0 2px}
 .legend{display:flex;flex-wrap:wrap;align-items:center;gap:4px;font-size:11px;color:var(--sub);margin:6px 0}.legend .sw{width:26px;height:10px;display:inline-block;border-radius:2px}
 table{width:100%;border-collapse:collapse;font-size:13px;margin-top:10px}td,th{padding:6px 4px;border-bottom:1px solid var(--bd);text-align:left}
 th{color:var(--sub);font-weight:500;font-size:11px}td.r,th.r{text-align:right}.small{font-size:11px;color:var(--sub)}
 tr.click{cursor:pointer}
 #pop{position:fixed;z-index:50;display:none;width:300px;max-width:calc(100vw - 24px);background:#1a1d27f2;border:1px solid var(--bd);
  border-radius:12px;padding:12px;box-shadow:0 8px 24px rgba(0,0,0,.5);pointer-events:none;font-size:13px}
 #pop .t{display:flex;justify-content:space-between;align-items:baseline;font-size:17px;font-weight:700;margin:2px 0 6px}
 #pop .pb{display:flex;height:6px;border-radius:3px;overflow:hidden;background:#3f3f46;margin:4px 0}
 #pop .row{display:flex;justify-content:space-between;align-items:center;padding:5px 0;border-top:1px solid var(--bd)}
 #pop .rk{color:#818cf8;width:16px;display:inline-block}#pop .nm{font-weight:600}#pop .me{background:#312e81;color:#c7d2fe;border-radius:4px;padding:0 4px;font-size:10px;margin-left:4px}
</style></head><body>
<h1>🗺️ 우리 섹터맵</h1><div class="sub" id="sub">불러오는 중…</div>
<div class="wx" id="wx"></div>
<div class="idx" id="idx"></div>
<div class="bar" id="views"></div>
<div class="bar"><span class="small" style="align-self:center">칸 크기</span>
 <button data-s="value" class="on">거래대금</button><button data-s="cap">시가총액</button><button data-s="eq">균등</button></div>
<div id="map"></div><div id="pop"></div>
<div class="legend"><span class="sw" style="background:rgb(220,38,38)"></span>-5%↓<span class="sw" style="background:rgba(220,38,38,.5)"></span>-3~-5
 <span class="sw" style="background:rgba(220,38,38,.3)"></span>~-3<span class="sw" style="background:#52525b"></span>0
 <span class="sw" style="background:#38bdf8"></span>~+5<span class="sw" style="background:#22c55e"></span>+5%↑</div>
<div class="sub" id="br"></div>
<table id="tbl"></table>
<div class="small" style="margin-top:8px">분야 = 대장 한투 관심그룹 · 등락은 시총 가중(괄호 안 단순 평균) · 시세 네이버(30초) · 그룹 구성 30분마다 다시 읽음</div>
<script>
let D=null,view='all',size='value';
const esc=s=>String(s??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const sg=v=>v>0?'+':'';
// ★ 11-24 대장 색: 0%=회색 · +0.1~5%=하늘색 · +5%↑=초록 · -0.1~-3%=빨강 30% · -3~-5%=빨강 50% · -5%↓=빨강
function color(c){if(c==null||Math.abs(c)<0.05)return '#52525b';
 if(c>0)return c<5?'#38bdf8':'#22c55e';
 return c>-3?'rgba(220,38,38,.3)':c>-5?'rgba(220,38,38,.5)':'rgb(220,38,38)'}
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
// ★ 11-25 거래대금이 거의 없을 때(프리장 초반·장 시작 전)는 시총으로 대신 — 칸이 다 같은 크기가 되는 것 방지
let valueOK=true;
const wOf=m=>size==='cap'||(size==='value'&&!valueOK)?(m.cap||1):size==='eq'?1:(m.value||1);
function tiles(el,mem,x,y,w,h){const mx=Math.max(...mem.map(wOf));   // ★ 11-25 대장 지정 대장주(👑)는 그 분야 가장 큰 칸 이상
 for(const r of squarify(mem.map(m=>({...m,w:m.leader?Math.max(wOf(m),mx):wOf(m)})).sort((a,b)=>b.w-a.w),x,y,w,h)){
  const m=r.item,fs=Math.max(9,Math.min(16,Math.sqrt(r.w*r.h)/6));const t=document.createElement('div');t.className='tile';
  Object.assign(t.style,{left:r.x+'px',top:r.y+'px',width:r.w+'px',height:r.h+'px',background:color(m.chg),fontSize:fs+'px'});
  t.title=`${m.leader?'👑 '+m.leader+' · ':''}${m.tag?'🔗 '+m.tag+' · ':''}${m.name} ${sg(m.chg)}${m.chg}% · 시총 ${(m.cap/1e12).toFixed(2)}조 · 거래대금 ${Math.round(m.value/1e8)}억`;
  if(r.w>34&&r.h>22)t.innerHTML=`<div class="n">${m.leader?'👑':''}${esc(m.name)}</div>`+(r.h>34?`<div>${sg(m.chg)}${m.chg.toFixed(2)}%</div>`:'');
  el.appendChild(t)}}
function draw(){const el=document.getElementById('map');el.innerHTML='';if(!D)return;
 {const all=D.sectors.flatMap(s=>s.members);valueOK=all.filter(m=>m.value>0).length>=all.length*0.5}
 el.style.height=Math.max(420,Math.round(window.innerHeight*0.68))+'px';const W=el.clientWidth,H=el.clientHeight;
 let secs=view==='all'?D.sectors:[...D.sectors,...D.watch].filter(s=>s.group===view);
 if(view!=='all'&&secs.length){tiles(el,secs[0].members,0,0,W,H);return}
 const items=secs.map(s=>({...s,w:size==='eq'?s.n:s.members.reduce((a,m)=>a+wOf(m),0)}));
 // ★ 11-24 대장 "너무 커": 한 분야(S7 대형주 등)가 지도의 20%를 넘지 않게 — 넘치는 칸은 20%로 깎음
 {const tot=items.reduce((a,b)=>a+b.w,0),cap=tot*0.2;const big=items.filter(i=>i.w>cap);
  if(big.length){const rest=tot-big.reduce((a,b)=>a+b.w,0);const lim=rest*0.2/(1-0.2*big.length||1);big.forEach(i=>i.w=Math.max(lim,0)||i.w)}}
 for(const r of squarify(items.sort((a,b)=>b.w-a.w),0,0,W,H)){const s=r.item;const box=document.createElement('div');box.className='sec'+((window.__picks||[]).includes(s.group)?' pick':'');
  Object.assign(box.style,{left:r.x+'px',top:r.y+'px',width:r.w+'px',height:r.h+'px'});
  const hd=document.createElement('div');hd.className='sh';const U=(D.us&&D.us.groups||{})[s.group];const um=U&&U.mark?(U.reliable?U.mark:`<span style="opacity:.45">${U.mark}</span>`):'';
  hd.innerHTML=`${(window.__picks||[]).includes(s.group)?'🎯 ':''}${um}${esc(s.group)} <b class="${s.chg_w>0?'p':'m'}">${sg(s.chg_w)}${s.chg_w}%</b>`;
  hd.onclick=e=>{e.stopPropagation();hidePop();setView(s.group)};box.appendChild(hd);
  box.onmousemove=e=>showPop(s,e.clientX,e.clientY);box.onmouseleave=hidePop;
  box.onclick=e=>{if(e.target!==hd)showPop(s,e.clientX,e.clientY)};tiles(box,s.members,0,16,r.w,Math.max(0,r.h-16));el.appendChild(box)}}
// ★ 11-24 대장: 분야에 마우스 올리면 요약 카드(등락·상승/보합/하락 막대·시총 상위 5). 폰은 칸을 누르면 뜨고 다른 곳 누르면 닫힘
const won=v=>v>=1e12?(v/1e12).toFixed(1)+'조':Math.round(v/1e8).toLocaleString()+'억';
function popHtml(s){const n=s.n||s.members.length,up=s.up,dn=s.down,fl=s.flat??(n-up-dn);
 const top=[...s.members].sort((a,b)=>b.cap-a.cap).slice(0,5),best=[...s.members].sort((a,b)=>b.chg-a.chg)[0];
 return `<div class="small">${(window.__picks||[]).includes(s.group)?'🎯 오늘 진입 분야 · ':''}한투 관심그룹</div>
 <div class="t"><span>${esc(s.group)}</span><span class="${s.chg_w>0?'p':'m'}">${sg(s.chg_w)}${s.chg_w}%</span></div>
 <div class="pb"><span style="width:${up/n*100}%;background:#22c55e"></span><span style="width:${fl/n*100}%;background:#71717a"></span><span style="width:${dn/n*100}%;background:#dc2626"></span></div>
 <div class="small">${n}종목 중 <span class="p">${up}개 상승</span> · ${fl}개 보합 · <span class="m">${dn}개 하락</span> · 단순평균 ${sg(s.chg)}${s.chg}%</div>
 ${usHtml(s.group)}
 <div style="margin:10px 0 4px;font-weight:600">시가총액 상위 ${top.length}</div>`+
 top.map((m,i)=>`<div class="row"><div><span class="rk">${i+1}</span><span class="nm">${m.leader?'👑':''}${esc(m.name)}</span>${m.leader?`<span class="me" style="background:#713f12;color:#fde68a">${esc(m.leader)}</span>`:''}${m.tag?`<span class="me" style="background:#1e3a5f;color:#bae6fd">${esc(m.tag)}</span>`:''}${m.code===best.code?'<span class="me">오늘 최강</span>':''}
  <div class="small" style="margin-left:16px">${esc(m.code)} · 시총 ${won(m.cap)} · 거래 ${won(m.value)}</div></div>
  <div style="text-align:right">${m.price?Math.round(m.price).toLocaleString():''}<div class="${m.chg>0?'p':(m.chg<0?'m':'')}">${sg(m.chg)}${m.chg.toFixed(2)}%</div></div></div>`).join('')+
 (best&&!top.find(m=>m.code===best.code)?`<div class="small" style="margin-top:6px">오늘 최강: ${esc(best.name)} ${sg(best.chg)}${best.chg}%</div>`:'')+
 (()=>{const ex=s.members.filter(m=>(m.leader||m.tag)&&!top.find(t=>t.code===m.code));   // ★ 11-25 상위 5 밖 대장·꼬리표 종목도
  return ex.length?`<div class="small" style="margin-top:6px">${ex.map(m=>`${m.leader?'👑':'🔗'}${esc(m.name)}(${esc(m.leader||m.tag)}) <span class="${m.chg>0?'p':'m'}">${sg(m.chg)}${m.chg}%</span>`).join(' · ')}</div>`:''})()}
function usHtml(g){const U=(D.us&&D.us.groups||{})[g];if(!U||!U.pairs||!U.pairs.length)return '';
 return `<div style="margin-top:8px;padding:6px 8px;background:#0f1117;border-radius:8px"><div class="small">🇺🇸 밤사이 미국 짝꿍 (${esc(D.us.date)})${U.reliable?'':' · 참고용(2년 검증상 잘 안 맞는 분야)'}</div>
 <div>${U.mark||''} 평균 <b class="${U.avg>0?'p':'m'}">${sg(U.avg)}${U.avg}%</b> · `+U.pairs.map(([t,p])=>`${esc(t)} <span class="${p>0?'p':'m'}">${sg(p)}${p}%</span>`).join(' · ')+`</div></div>`}
const pop=document.getElementById('pop');
function showPop(s,x,y){pop.innerHTML=popHtml(s);pop.style.display='block';const w=pop.offsetWidth,h=pop.offsetHeight;
 let L=x+14,T=y+14;if(L+w>innerWidth-8)L=Math.max(8,x-w-14);if(T+h>innerHeight-8)T=Math.max(8,innerHeight-h-8);pop.style.left=L+'px';pop.style.top=T+'px'}
function hidePop(){pop.style.display='none'}
document.addEventListener('click',e=>{if(!e.target.closest('.sec'))hidePop()});
function setView(v){hidePop();view=v;document.querySelectorAll('#views button').forEach(b=>b.classList.toggle('on',b.dataset.v===v));draw();window.scrollTo(0,0)}
function render(){
 const W=D.weather||{},P=(W.picks||[]).map(p=>p.group);
 document.getElementById('wx').innerHTML=`<span class="lab">🌦️ 오늘 태세 <b>${esc(W.stock||'-')}</b>${W.override?' → 대장 해제':''} · 🎯 진입 분야</span>`+
  ((W.picks||[]).length?W.picks.map(p=>`<span class="chip" data-g="${esc(p.group)}">${esc(p.state.split(' ')[0])} ${esc(p.group)} ${p.score>0?'+':''}${p.score}${p.leader?' · '+esc(p.leader):''}</span>`).join('')
   :'<span class="lab">(점수 +1 이상 분야 없음)</span>')+'<span class="lab small">아침 분야 날씨 상위 3 · 관찰용(봇 매수엔 아직 미연결)</span>';
 document.querySelectorAll('#wx .chip').forEach(c=>c.onclick=()=>setView(c.dataset.g));
 const UG=Object.entries((D.us&&D.us.groups)||{}).filter(([g,v])=>v.mark&&v.reliable);
 document.getElementById('wx').innerHTML+=`<div style="flex-basis:100%;height:0"></div><span class="lab">🇺🇸 밤사이 미국 짝꿍 (${esc((D.us||{}).date||'-')})</span>`+
  (UG.length?UG.sort((a,b)=>b[1].avg-a[1].avg).map(([g,v])=>`<span class="lab">${v.mark}${esc(g)} <b class="${v.avg>0?'p':'m'}">${sg(v.avg)}${v.avg}%</b></span>`).join(' · ')
   :'<span class="lab">(믿을 만한 분야 중 ±2% 넘은 곳 없음)</span>')+'<span class="lab small">⭐+2%↑ 🔻-2%↓ · 흐린 표시는 짝꿍이 잘 안 맞는 분야</span>';
 window.__picks=P;
 document.getElementById('sub').textContent=D.at+' 기준 · 한투 관심그룹 '+(D.sectors.length)+'개 분야'+
  (D.pre?` · 🌅 NXT 프리장 시세(${D.pre}종목)`:'')+(size==='value'&&D.sectors.flatMap(s=>s.members).filter(m=>m.value>0).length<D.sectors.flatMap(s=>s.members).length*0.5?' · 거래대금 적어 칸 크기는 시총으로':'');
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
