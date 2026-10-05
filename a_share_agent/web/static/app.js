const $ = (id) => document.getElementById(id);
const fmt = (v, digits=2) => (v === null || v === undefined || Number.isNaN(Number(v))) ? '--' : Number(v).toLocaleString('zh-CN',{maximumFractionDigits:digits,minimumFractionDigits:digits});
const pct = (v) => v === null || v === undefined ? '--' : `${Number(v)>=0?'+':''}${fmt(v,2)}%`;
const money = (v) => v === null || v === undefined ? '--' : Number(v).toLocaleString('zh-CN',{maximumFractionDigits:0});
const cls = (s) => ['strong','up','risk_on','hot','warming','PASS','ok','FILLED','ENTRY_CANDIDATE'].includes(String(s)) ? 'good' : ['weak','down','risk_off','panic','REJECT','error','CANCELLED'].includes(String(s)) ? 'bad' : 'warn';
const esc = (s) => String(s??'').replace(/[&<>"']/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let lastDashboard = null;
let liveReloadTimer = null;
let auditEventSource = null;
const seenNotificationEvents = new Set();

function notificationPermissionLabel(){
  const b=$('notifyBtn'); if(!b) return;
  if(!('Notification' in window)){b.textContent='站内提醒';b.disabled=true;return;}
  if(Notification.permission==='granted'){b.textContent='桌面通知已开';b.className='btn-ghost';}
  else if(Notification.permission==='denied'){b.textContent='通知被禁用';b.className='btn-ghost';}
  else {b.textContent='开启通知';b.className='btn-ghost';}
}
async function requestDesktopNotifications(){
  if(!('Notification' in window)) return;
  try{await Notification.requestPermission();}catch(_){ }
  notificationPermissionLabel();
}
function showToast(n){
  if(!n?.notify || seenNotificationEvents.has(n.event_id)) return;
  seenNotificationEvents.add(n.event_id);
  if(seenNotificationEvents.size>300){const first=seenNotificationEvents.values().next().value;seenNotificationEvents.delete(first);}
  const stack=$('toastStack'); if(!stack) return;
  const el=document.createElement('article'); el.className=`toast ${esc(n.severity||'info')}`;
  const fields=(n.fields||[]).slice(0,8).map(f=>`<div class="toast-field">${esc(f.label)}：<b>${esc(f.value)}</b></div>`).join('');
  el.innerHTML=`<div class="toast-head"><div class="toast-title">${esc(n.title||'交易提醒')}</div><button class="toast-close" aria-label="关闭">×</button></div><div class="toast-text">${esc(n.text||'')}</div>${fields?`<div class="toast-fields">${fields}</div>`:''}<div class="toast-actions"><button class="toast-open">查看审计</button></div>`;
  el.querySelector('.toast-close').addEventListener('click',()=>el.remove());
  el.querySelector('.toast-open').addEventListener('click',()=>{if(n.trace_id) openTrace(n.trade_date,n.trace_id); else if(n.event_id) openEvent(n.event_id); el.remove();});
  stack.prepend(el);
  setTimeout(()=>el.remove(),15000);
  if('Notification' in window && Notification.permission==='granted' && document.visibilityState!=='visible') {
    try{const note=new Notification(n.title||'A股 Agent',{body:[...(n.fields||[]).slice(0,3).map(f=>`${f.label}: ${f.value}`),n.text||''].filter(Boolean).join('\n')});note.onclick=()=>window.focus();}catch(_){ }
  }
}
async function handleAuditNotification(summary){
  if(!summary?.event_id) return;
  try{const r=await fetch(`/api/notifications/event/${encodeURIComponent(summary.event_id)}`);if(!r.ok)return;showToast(await r.json());}catch(_){ }
}

function sparkline(values, state='neutral', height=42){
  if(!values || values.length<2) return '<div class="spark"></div>';
  const w=240,h=height,p=2, min=Math.min(...values), max=Math.max(...values), span=(max-min)||1;
  const pts=values.map((v,i)=>`${p+i*(w-2*p)/(values.length-1)},${h-p-(v-min)*(h-2*p)/span}`).join(' ');
  const color=state==='up'?'#37d39b':state==='down'?'#ff6b7d':'#f2c35f';
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><polyline fill="none" stroke="${color}" stroke-width="2" points="${pts}"/></svg>`;
}
function jsonBlock(v){ return `<pre class="code-box">${esc(JSON.stringify(v??{},null,2))}</pre>`; }
function section(title, body){return `<section class="detail-section"><h3>${esc(title)}</h3>${body}</section>`}
function kv(obj){return `<div class="kv-grid">${Object.entries(obj).map(([k,v])=>`<div class="kv"><span>${esc(k)}</span><strong>${esc(v??'--')}</strong></div>`).join('')}</div>`}

function renderKPIs(d){
  const h=d.market?.market_health||{}, a=d.account||{}, b=a.balance||{}, p=a.pnl||{}, l=d.limitup||{};
  const items=[['市场状态',d.market?.market_regime||'--',d.market?.market_trend||'--'],['情绪阶段',d.market?.sentiment_phase||'--',d.market?.sentiment_state||'--'],['涨停 / 跌停',`${h.limit_up??'--'} / ${h.limit_down??'--'}`,`炸板率 ${h.blowup_rate!==undefined?pct(Number(h.blowup_rate)*100):'--'}`],['最高连板',`${l.max_streak??'--'} 板`,`梯队 ${l.levels?.length||0} 层`],['今日盈亏',p.pnl!==undefined?money(p.pnl):'--',p.pnl_pct!==undefined?pct(Number(p.pnl_pct)*100):'--']];
  $('kpiGrid').innerHTML=items.map(x=>`<div class="kpi"><div class="label">${x[0]}</div><div class="value">${x[1]}</div><div class="sub">${x[2]}</div></div>`).join('');
}
function renderSectors(sectors){
  if(!sectors?.length){$('sectorGrid').innerHTML='<div class="review-box">暂无主线板块数据。</div>';return;}
  $('sectorGrid').innerHTML=sectors.map(s=>{const t=s.trend||{}, r=s.route||{}, leaders=(s.leaders||[]).map(x=>x.name||x.stock||x.symbol||x.code).filter(Boolean).slice(0,3).join(' / ')||'--';return `<article class="sector-card clickable" data-sector-code="${esc(s.code||'')}" data-sector-name="${esc(s.name)}"><div class="sector-top"><div><div class="sector-rank">#${s.rank}${s.code?` · ${esc(s.code)}`:''}</div><div class="sector-name">${esc(s.name)}</div></div><div class="sector-score">${fmt(s.score,0)}</div></div><div class="sector-meta"><span class="tag ${cls(s.strength)}">${esc(s.strength)}</span><span class="tag">${esc(s.lifecycle)}</span><span class="tag ${cls(t.state)}">trend:${esc(t.state)}</span></div>${sparkline(t.sparkline||[],t.state)}<div class="sector-stats"><div>5日<strong class="${(t.change_5d||0)>=0?'positive':'negative'}">${pct(t.change_5d)}</strong></div><div>20日<strong class="${(t.change_20d||0)>=0?'positive':'negative'}">${pct(t.change_20d)}</strong></div><div>仓位倍率<strong>${fmt(r.position_multiplier,2)}×</strong></div></div><div class="route-box"><div><b>${esc(r.route_id||'--')}</b> · 门槛 +${fmt(r.threshold_delta,0)}</div><div class="route-line">风向标：${esc(leaders)}</div><div class="route-line route-allow">允许：${esc((r.allowed||[]).join(' / ')||'--')}</div><div class="route-line route-block">限制：${esc((r.blocked||[]).join(' / ')||'--')}</div></div><div class="card-action">点击查看板块详情 →</div></article>`}).join('');
  document.querySelectorAll('[data-sector-name]').forEach(el=>el.addEventListener('click',()=>openSector(el.dataset.sectorCode,el.dataset.sectorName)));
}
function renderLadder(l){
  $('maxStreak').textContent=`${l?.max_streak??'--'}板`; const levels=l?.levels||[], max=Math.max(1,...levels.map(x=>x.count||0));
  $('ladderBars').innerHTML=levels.map(x=>`<div class="ladder-bar"><div class="bar-count">${x.count}</div><div class="bar" style="height:${Math.max(6,(x.count/max)*96)}px"></div><div class="bar-label">${x.streak}板</div></div>`).join('') || '<div class="list-sub">暂无梯队数据</div>';
  const stocks=l?.stocks||[]; $('limitupStocks').innerHTML=stocks.slice(0,12).map(s=>`<div class="list-row clickable" data-symbol="${esc(s.symbol||'')}"><div><div class="list-main">${esc(s.name||s.symbol||'--')}</div><div class="list-sub">${esc(s.symbol||'')} · ${esc(s.sector||'--')}</div></div><div class="list-sub">${esc(s.limit_time||'--')} · 炸${s.blowups??0}</div><div class="list-value">${s.streak||0}板</div></div>`).join('') || '<div class="list-sub">MCP 当前未返回连板个股明细。</div>';
  document.querySelectorAll('#limitupStocks [data-symbol]').forEach(el=>el.addEventListener('click',()=>openStock(el.dataset.symbol)));
}
function renderCandidates(c){
  const signals=c?.signal_decisions||[], batch=c?.batch?.batch||[]; const rows=signals.length?signals:batch.map(x=>({symbol:x.symbol,payload:{score:x.score,decision:'PRELIMINARY',strategy_id:'--',reason:'批量初筛'}})); $('candidateCount').textContent=String(rows.length);
  if(!rows.length){$('candidateTable').innerHTML='<div class="review-box">今日尚无候选记录。运行 stock-pick 或 worker 后出现。</div>';return;}
  $('candidateTable').innerHTML=`<table class="data-table"><thead><tr><th>代码</th><th>名称</th><th>策略</th><th>评分</th><th>理由</th></tr></thead><tbody>${rows.slice(0,40).map(r=>{const p=r.payload||{},sym=r.symbol||p.symbol||'';return `<tr class="clickable" data-symbol="${esc(sym)}"><td><b>${esc(sym||'--')}</b></td><td>${esc(r.name||p.name||'--')}</td><td>${esc(r.strategy_id||p.strategy_id||'--')}</td><td class="score">${p.score??'--'}</td><td>${esc(p.reason||p.summary||p.reasons_for?.[0]||'--')}</td></tr>`}).join('')}</tbody></table>`;
  document.querySelectorAll('#candidateTable [data-symbol]').forEach(el=>el.addEventListener('click',()=>openStock(el.dataset.symbol)));
}
function renderAccount(a){const b=a?.balance||{}, p=a?.pnl||{}, pos=a?.positions||[];$('accountSummary').innerHTML=[['可用现金',money(b.cash)],['持仓市值',money(b.market_value)],['今日盈亏',money(p.pnl)]].map(x=>`<div class="mini-kpi"><span>${x[0]}</span><strong>${x[1]}</strong></div>`).join('');if(!pos.length){$('positionsTable').innerHTML='<div class="review-box">当前无持仓。</div>';return;}$('positionsTable').innerHTML=`<table class="data-table"><thead><tr><th>代码</th><th>数量</th><th>成本</th><th>现价</th><th>盈亏</th></tr></thead><tbody>${pos.map(x=>`<tr class="clickable" data-symbol="${esc(x.symbol||'')}"><td><b>${esc(x.symbol||'--')}</b></td><td>${x.quantity??x.volume??'--'}</td><td>${fmt(x.cost_price??x.cost)}</td><td>${fmt(x.price??x.last_price)}</td><td class="${Number(x.pnl||0)>=0?'positive':'negative'}">${money(x.pnl)}</td></tr>`).join('')}</tbody></table>`;document.querySelectorAll('#positionsTable [data-symbol]').forEach(el=>el.addEventListener('click',()=>openStock(el.dataset.symbol)));}
function renderMarket(m){const h=m?.market_health||{}; const vals=[['市场状态',m?.market_regime],['趋势方向',m?.market_trend],['情绪',m?.sentiment_phase],['涨停',h.limit_up],['跌停',h.limit_down],['涨跌比',h.advance_decline_ratio],['炸板率',h.blowup_rate!==undefined?pct(Number(h.blowup_rate)*100):'--']];$('marketContext').innerHTML=vals.map(x=>`<div class="context-item"><span>${x[0]}</span><strong class="${cls(x[1])}">${esc(x[1]??'--')}</strong></div>`).join('');$('benchmarkList').innerHTML=(m?.benchmarks||[]).map(x=>`<div class="list-row"><div><div class="list-main">${esc(x.symbol)}</div><div class="list-sub">MA20 ${fmt(x.ma20)}</div></div><div class="list-sub">斜率 ${fmt(x.ma20_slope,3)}</div><div class="list-value ${cls(x.state)}">${esc(x.state)}</div></div>`).join('');}
function renderHotSignals(x){const items=Array.isArray(x)?x:(x?.items||x?.data||[]);$('hotSignals').innerHTML=(items||[]).slice(0,8).map(s=>`<div class="list-row"><div><div class="list-main">${esc(s.sector||s.symbol||s.type||'热点')}</div><div class="list-sub">${esc(s.note||s.signal||s.type||'--')}</div></div><div class="list-sub">${esc(s.type||'--')}</div><div class="list-value">${s.strength??s.score??'--'}</div></div>`).join('') || '<div class="list-sub">暂无热点异动信号。</div>';}
function renderAudit(items){if(!items?.length){$('auditTimeline').innerHTML='<div class="review-box">暂无审计事件。</div>';return;}$('auditTimeline').innerHTML=items.map(x=>{const t=(x.event_time||'').split('T')[1]?.slice(0,8)||'--';return `<div class="timeline-item clickable" data-trace="${esc(x.trace_id||'')}" data-date="${esc((x.event_time||'').slice(0,10))}"><div class="time">${t}</div><div class="node"></div><div><div class="event-title">${esc(x.event_type)}${x.symbol?` · ${esc(x.symbol)}`:''}</div><div class="event-meta">${esc(x.phase||'--')} · ${esc(x.status||'--')}${x.strategy_id?` · ${esc(x.strategy_id)}`:''}</div></div></div>`}).join('');document.querySelectorAll('#auditTimeline [data-trace]').forEach(el=>el.addEventListener('click',()=>{if(el.dataset.trace) openTrace(el.dataset.date,el.dataset.trace)}));}
function renderReview(r){$('dailyReview').textContent=r?JSON.stringify(r,null,2):'今日 DAILY_REVIEW 尚未生成。系统会在收盘后的复盘阶段写入审计链。';}
function renderSystem(d){const s=d.system||{};$('modeBadge').textContent=(s.mode||'--').toUpperCase();$('phaseBadge').textContent=s.phase||'非交易调度窗口';$('asof').textContent=`快照时间：${d.as_of||'--'} · 策略版本 ${s.strategy_version||'--'} · 代码版本 ${s.code_version||'--'}`;const health=s.mcp_health||{}, oks=Object.values(health).filter(x=>x&&x.ok!==false).length, total=Object.keys(health).length;$('healthText').textContent=`MCP 连接 ${oks}/${total}`;}

function showDrawer(title, subtitle, eyebrow='DETAIL'){ $('drawerTitle').textContent=title;$('drawerSubtitle').textContent=subtitle||'';$('drawerEyebrow').textContent=eyebrow;$('drawerBody').innerHTML='<div class="loading">加载中…</div>';$('drawer').classList.remove('hidden');$('drawerBackdrop').classList.remove('hidden'); }
function closeDrawer(){$('drawer').classList.add('hidden');$('drawerBackdrop').classList.add('hidden');}
$('drawerClose').addEventListener('click',closeDrawer);$('drawerBackdrop').addEventListener('click',closeDrawer);

async function openSector(code,name){showDrawer(name,code||'无板块代码','板块详情');try{const q=new URLSearchParams();if(code)q.set('code',code);if(name)q.set('name',name);const r=await fetch(`/api/sector/detail?${q}`);if(!r.ok)throw new Error(await r.text());const d=await r.json(),s=d.sector||{},t=s.trend||{},e=d.explanation||{};$('drawerBody').innerHTML=section('市场与板块定位',kv({'大盘':d.market?.market_regime,'市场趋势':d.market?.market_trend,'情绪':d.market?.sentiment_phase,'热度分':fmt(s.score,0),'强弱':s.strength,'生命周期':s.lifecycle,'板块趋势':t.state,'5日':pct(t.change_5d),'20日':pct(t.change_20d)}))+section('趋势',sparkline(t.sparkline||[],t.state,90))+section('Strategy Router',kv({'Route':e.route_id,'仓位倍率':e.position_multiplier!==null?`${fmt(e.position_multiplier,2)}×`:'--','门槛调整':e.threshold_delta!==null?`+${fmt(e.threshold_delta,0)}`:'--','允许':(e.allowed||[]).join(' / ')||'--','条件允许':(e.conditional||[]).join(' / ')||'--','阻止':(e.blocked||[]).join(' / ')||'--'}))+section('风向标',renderStockCards(d.leaders||[]))+section('板块连板股',renderStockCards((d.limitup_stocks||[]).map(x=>({...x,quote:{price:null}})),true))+section('热点信号',jsonBlock(d.hot_signals||[]));wireDrawerLinks();}catch(e){$('drawerBody').innerHTML=`<div class="alert">板块详情加载失败：${esc(e.message)}</div>`}}
function renderStockCards(items,limitup=false){if(!items.length)return '<div class="muted">暂无数据</div>';return `<div class="stock-cards">${items.map(x=>{const q=x.quote||{};return `<button class="stock-card" data-open-stock="${esc(x.symbol||'')}"><b>${esc(x.name||x.symbol||'--')}</b><span>${esc(x.symbol||'')}</span>${limitup?`<em>${x.streak||0}板 · ${esc(x.limit_time||'--')}</em>`:`<em>${q.price!==undefined&&q.price!==null?fmt(q.price):'--'} · ${q.pct!==undefined?pct(q.pct):'--'}</em>`}</button>`}).join('')}</div>`}

async function openStock(symbol){if(!symbol)return;showDrawer(symbol,'行情 · 策略 · 风控 · 审计','股票详情');try{const r=await fetch(`/api/stock/${encodeURIComponent(symbol)}`);if(!r.ok)throw new Error(await r.text());const d=await r.json(),m=d.market_data||{},q=m.quote?.data||m.tdx_quote?.data||{},tech=m.technical?.data||{},sig=d.deterministic_signals||{},audit=d.audit||{},bars=Array.isArray(m.kline?.data)?m.kline.data:(m.kline?.data?.bars||[]),closes=bars.map(x=>Number(x.close)).filter(Number.isFinite);$('drawerBody').innerHTML=section('实时概览',kv({'价格':q.price??q.last??'--','涨跌幅':q.pct!==undefined?pct(q.pct):'--','成交额':q.amount!==undefined?money(q.amount):'--','持仓':(d.account?.positions||[]).length?'有':'无','今日成交':(d.account?.today_trades||[]).length,'Audit事件':audit.event_count||0}))+section('120日走势',sparkline(closes,closes.length>1?(closes.at(-1)>=closes[0]?'up':'down'):'neutral',100))+section('确定性策略信号',jsonBlock(sig))+section('技术指标',jsonBlock(tech))+section('筹码 / 资金',`<div class="split-json">${jsonBlock(m.chip?.data)}${jsonBlock(m.fund_flow?.data)}</div>`)+section('F10 / 财务',`<div class="split-json">${jsonBlock(m.f10?.data)}${jsonBlock(m.financial?.data)}</div>`)+section('新闻/公告',jsonBlock(m.news?.data))+section('当天决策链',renderEventCards(audit.events||[]));wireDrawerLinks();}catch(e){$('drawerBody').innerHTML=`<div class="alert">个股详情加载失败：${esc(e.message)}</div>`}}
function renderEventCards(events){if(!events.length)return '<div class="muted">该交易日没有该股票的审计事件。</div>';return `<div class="event-cards">${events.map(e=>`<button class="event-card" data-trace-date="${esc(e.trade_date||'')}" data-trace-id="${esc(e.trace_id||'')}"><span>${esc((e.event_time||'').split('T')[1]?.slice(0,8)||'--')}</span><b>${esc(e.event_type)}</b><em>${esc(e.status||'--')} · ${esc(e.strategy_id||'--')}</em></button>`).join('')}</div>`}
function wireDrawerLinks(){document.querySelectorAll('#drawerBody [data-open-stock]').forEach(el=>el.addEventListener('click',()=>openStock(el.dataset.openStock)));document.querySelectorAll('#drawerBody [data-trace-id]').forEach(el=>el.addEventListener('click',()=>{if(el.dataset.traceId)openTrace(el.dataset.traceDate,el.dataset.traceId)}));}

async function loadReplayDates(){const r=await fetch('/api/replay/dates');const d=await r.json();const dates=d.dates||[];$('replayDate').innerHTML=dates.map(x=>`<option value="${esc(x)}">${esc(x)}</option>`).join('');if(!dates.length && lastDashboard?.as_of){const today=lastDashboard.as_of.slice(0,10);$('replayDate').innerHTML=`<option value="${today}">${today}</option>`;} }
async function loadReplay(date){date=date||$('replayDate').value;if(!date)return;const r=await fetch(`/api/replay/${encodeURIComponent(date)}`);if(!r.ok)throw new Error(await r.text());const d=await r.json(),verify=d.verify||{};$('replaySummary').innerHTML=[['交易日',d.trade_date,'Exact Replay'],['事件数',d.event_count,'不可变事件'],['Hash Chain',verify.ok?'OK':'FAIL',verify.ok?'完整':'审计链异常'],['Trace数',(d.traces||[]).length,'决策链路'],['涉及股票',(d.symbols||[]).length,'有symbol事件']].map(x=>`<div class="kpi"><div class="label">${x[0]}</div><div class="value ${x[1]==='FAIL'?'bad':''}">${x[1]}</div><div class="sub">${x[2]}</div></div>`).join('');$('traceList').innerHTML=(d.traces||[]).map(t=>`<button class="trace-row" data-replay-trace="${esc(t.trace_id)}"><div><b>${esc(t.symbol||t.strategy_id||t.types?.[0]||'系统链路')}</b><span>${esc(t.types?.slice(0,3).join(' → ')||'')}</span></div><div><strong>${t.event_count}</strong><span>${esc((t.first_time||'').split('T')[1]?.slice(0,8)||'--')}</span></div></button>`).join('')||'<div class="muted">暂无 Trace。</div>';$('replayTimeline').innerHTML=(d.timeline||[]).map(e=>`<div class="timeline-item clickable" data-replay-event="${esc(e.event_id||'')}"><div class="time">${esc((e.event_time||'').split('T')[1]?.slice(0,8)||'--')}</div><div class="node"></div><div><div class="event-title">${esc(e.event_type)}${e.symbol?` · ${esc(e.symbol)}`:''}</div><div class="event-meta">${esc(e.phase||'--')} · ${esc(e.status||'--')}</div></div></div>`).join('');$('replayReview').textContent=JSON.stringify(d.daily_review??{},null,2);$('replayManifest').textContent=JSON.stringify(d.manifest??d.event_type_counts??{},null,2);document.querySelectorAll('[data-replay-trace]').forEach(el=>el.addEventListener('click',()=>openTrace(date,el.dataset.replayTrace)));document.querySelectorAll('[data-replay-event]').forEach(el=>el.addEventListener('click',()=>openEvent(el.dataset.replayEvent)));}
async function openTrace(date,traceId){showDrawer(traceId,`交易日 ${date}`,'TRACE REPLAY');try{const r=await fetch(`/api/replay/${encodeURIComponent(date)}/trace/${encodeURIComponent(traceId)}`);if(!r.ok)throw new Error(await r.text());const d=await r.json();$('drawerBody').innerHTML=section('Trace 摘要',kv({'交易日':date,'事件数':d.event_count,'Trace ID':traceId}))+section('完整事件链',(d.events||[]).map(e=>`<article class="trace-event"><div class="trace-event-head"><b>${esc(e.event_type)}</b><span>${esc((e.event_time||'').split('T')[1]?.slice(0,8)||'--')}</span></div>${kv({'phase':e.phase,'symbol':e.symbol,'strategy':e.strategy_id,'status':e.status,'intent':e.intent_id})}${jsonBlock(e.materialized_payload??e.payload)}</article>`).join(''));}catch(e){$('drawerBody').innerHTML=`<div class="alert">Trace 加载失败：${esc(e.message)}</div>`}}
async function openEvent(eventId){showDrawer(eventId,'单一审计事件','AUDIT EVENT');try{const r=await fetch(`/api/audit/event/${encodeURIComponent(eventId)}`);if(!r.ok)throw new Error(await r.text());const e=await r.json();$('drawerBody').innerHTML=section('事件元数据',kv({'type':e.event_type,'time':e.event_time,'phase':e.phase,'symbol':e.symbol,'strategy':e.strategy_id,'trace':e.trace_id,'intent':e.intent_id,'status':e.status,'payload hash':e.payload_sha256,'event hash':e.event_hash}))+section('Payload',jsonBlock(e.materialized_payload??e.payload));}catch(e){$('drawerBody').innerHTML=`<div class="alert">事件加载失败：${esc(e.message)}</div>`}}

async function load(force=false){$('refreshBtn').disabled=true;$('refreshBtn').textContent='刷新中…';$('alertBox').classList.add('hidden');try{const res=await fetch(`/api/dashboard${force?'?force=true':''}`);if(!res.ok)throw new Error(await res.text());const d=await res.json();lastDashboard=d;renderSystem(d);renderKPIs(d);renderSectors(d.hot_sectors);renderLadder(d.limitup);renderCandidates(d.candidates);renderAccount(d.account);renderMarket(d.market);renderHotSignals(d.hot_signals);renderAudit(d.audit_timeline);renderReview(d.daily_review);}catch(e){$('alertBox').textContent=`控制台数据加载失败：${e.message}`;$('alertBox').classList.remove('hidden');}finally{$('refreshBtn').disabled=false;$('refreshBtn').textContent='刷新快照';}}

async function loadWorkerStatus(){
  try{
    const r=await fetch('/api/worker/status');
    if(!r.ok) throw new Error(await r.text());
    const d=await r.json(), s=d.status||{};
    const badge=$('workerBadge');
    const state=String(s.state||'OFFLINE').toUpperCase();
    badge.textContent=`WORKER ${state}`;
    badge.className=`badge ${['RUNNING','STARTING'].includes(state)?'good':['DEGRADED','ERROR'].includes(state)?'bad':'warn'}`;
    badge.title=s.heartbeat_at?`heartbeat ${s.heartbeat_at}${s.current_phase?` · ${s.current_phase}`:''}`:'尚无 Worker heartbeat';
  }catch(e){
    const badge=$('workerBadge'); badge.textContent='WORKER OFFLINE'; badge.className='badge bad'; badge.title=e.message;
  }
}
function scheduleLiveReload(){
  if(liveReloadTimer) return;
  liveReloadTimer=setTimeout(()=>{
    liveReloadTimer=null;
    if($('dashboardView').classList.contains('active')) load(false);
  },500);
}
function connectAuditStream(){
  if(!window.EventSource) return;
  if(auditEventSource) auditEventSource.close();
  const badge=$('liveBadge');
  auditEventSource=new EventSource('/api/events/stream');
  auditEventSource.onopen=()=>{badge.textContent='LIVE 已连接';badge.className='badge good';};
  auditEventSource.addEventListener('audit',(ev)=>{
    badge.textContent='LIVE 实时';badge.className='badge good';
    try{const d=JSON.parse(ev.data);badge.title=`${d.event_type||'AUDIT'} · ${d.event_time||''}`;handleAuditNotification(d);}catch(_){ }
    scheduleLiveReload();
  });
  auditEventSource.onerror=()=>{badge.textContent='LIVE 重连中';badge.className='badge warn';};
}

function switchView(name){document.querySelectorAll('.view').forEach(v=>v.classList.toggle('active',v.id===`${name}View`));document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('active',t.dataset.view===name));if(name==='replay'){loadReplayDates().then(()=>loadReplay()).catch(e=>{$('alertBox').textContent=`Replay加载失败：${e.message}`;$('alertBox').classList.remove('hidden')});}if(name==='backtest'){loadBacktests().catch(e=>{$('btDetail').textContent='回测列表加载失败：'+e.message;});}}
document.querySelectorAll('.tab').forEach(t=>t.addEventListener('click',()=>switchView(t.dataset.view)));$('refreshBtn').addEventListener('click',()=>load(true));$('loadReplayBtn').addEventListener('click',()=>loadReplay().catch(e=>alert(e.message)));$('openTodayReplay').addEventListener('click',()=>switchView('replay'));if($('notifyBtn'))$('notifyBtn').addEventListener('click',requestDesktopNotifications);
notificationPermissionLabel();load(false);loadWorkerStatus();connectAuditStream();setInterval(loadWorkerStatus,10000);setInterval(()=>{if($('dashboardView').classList.contains('active'))load(false)},30000);

let btPollTimer=null;
function btPct(v){return v===undefined||v===null?'--':`${(Number(v)*100).toFixed(2)}%`}
async function loadBacktests(){
  const r=await fetch('/api/backtests'); if(!r.ok) throw new Error(await r.text()); const runs=await r.json();
  $('btRuns').innerHTML=runs.map(x=>`<button class="trace-row" data-bt-run="${esc(x.run_id)}"><div><b>${esc(x.run_id)}</b><span>${esc(x.settings?.start_date||'')} → ${esc(x.settings?.end_date||'')}</span></div><div><strong>${btPct(x.metrics?.total_return)}</strong><span>DD ${btPct(x.metrics?.max_drawdown)}</span></div></button>`).join('')||'<div class="muted">暂无回测。</div>';
  document.querySelectorAll('[data-bt-run]').forEach(el=>el.addEventListener('click',()=>loadBacktestDetail(el.dataset.btRun)));
  if(runs.length) loadBacktestDetail(runs[0].run_id);
}
async function loadBacktestDetail(runId){
  const r=await fetch(`/api/backtests/${encodeURIComponent(runId)}`); if(!r.ok) throw new Error(await r.text()); const d=await r.json(),m=d.metrics||{},c=d.coverage||{};
  $('btSummary').innerHTML=[['总收益',btPct(m.total_return),'基准 '+btPct(m.benchmark_return)],['最大回撤',btPct(m.max_drawdown),`持续 ${m.max_drawdown_days||0} 日`],['Sharpe',fmt(m.sharpe),`Calmar ${fmt(m.calmar)}`],['胜率',btPct(m.win_rate),`PF ${fmt(m.profit_factor)}`],['月收益≥30%',`${m.months_ge_target||0}/${m.months_total||0}`,btPct(m.months_ge_target_rate)]].map(x=>`<div class="kpi"><div class="label">${x[0]}</div><div class="value">${x[1]}</div><div class="sub">${x[2]}</div></div>`).join('');
  const q=d.data_quality||{};
  $('btDetail').innerHTML=`<b>${esc(runId)}</b><div class="bt-metric">股票覆盖 ${c.tested_symbols||0}/${c.requested_symbols||0} · 信号 ${c.signal_count||0} · 候选 ${c.candidate_count||0} · 平仓 ${m.closed_trades||0}</div><div class="bt-actions"><a class="bt-link" target="_blank" href="/api/backtests/${encodeURIComponent(runId)}/file/report.html">HTML报告</a><a class="bt-link" target="_blank" href="/api/backtests/${encodeURIComponent(runId)}/file/report.json">JSON报告</a><a class="bt-link" href="/api/backtests/${encodeURIComponent(runId)}/file/trades.csv">交易CSV</a><a class="bt-link" href="/api/backtests/${encodeURIComponent(runId)}/file/rejections.csv">拒绝记录</a></div><pre class="code-box">${esc(JSON.stringify({metrics:m,by_strategy:d.by_strategy,by_market_regime:d.by_market_regime,by_sector_strength:d.by_sector_strength,data_quality:q,universe:d.universe},null,2))}</pre>`;
}
async function runBacktest(){
  const btn=$('runBacktestBtn'); btn.disabled=true; $('btJobStatus').textContent='正在提交…';
  const payload={start_date:$('btStart').value,end_date:$('btEnd').value,initial_cash:Number($('btCash').value),min_score:Number($('btScore').value),max_universe:Number($('btMaxUniverse').value),slippage_bps:Number($('btSlippage').value),walk_forward:$('btWF').checked};
  try{const r=await fetch('/api/backtests/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}); if(!r.ok) throw new Error(await r.text()); const j=await r.json(); $('btJobStatus').textContent=`任务 ${j.job_id} 已提交`; pollBacktestJob(j.job_id);}catch(e){$('btJobStatus').textContent=`失败：${e.message}`;btn.disabled=false;}
}
async function pollBacktestJob(id){clearTimeout(btPollTimer);try{const r=await fetch(`/api/backtests/jobs/${encodeURIComponent(id)}`),j=await r.json();$('btJobStatus').textContent=`${id}: ${j.status}${j.error?' · '+j.error:''}`;if(j.status==='COMPLETED'){ $('runBacktestBtn').disabled=false; await loadBacktests(); if(j.run_id) await loadBacktestDetail(j.run_id); return;}if(j.status==='FAILED'){ $('runBacktestBtn').disabled=false; return;}btPollTimer=setTimeout(()=>pollBacktestJob(id),2000);}catch(e){$('btJobStatus').textContent=e.message;$('runBacktestBtn').disabled=false;}}
if($('runBacktestBtn'))$('runBacktestBtn').addEventListener('click',runBacktest);
