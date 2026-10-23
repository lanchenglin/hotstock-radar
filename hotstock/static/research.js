'use strict';
const $ = id => document.getElementById(id);
let config = {}, research = {}, data = {}, accessKey = sessionStorage.getItem('radar_research_key') || '';
$('key').value = accessKey;
function node(tag, text, cls) { const el=document.createElement(tag); if(text!==undefined)el.textContent=text; if(cls)el.className=cls; return el; }
function message(text, error=false){$('statusline').textContent=text;$('statusline').classList.toggle('warn',error);}
async function api(path, method='GET', body){const response=await fetch(path,{method,headers:{'X-API-Key':accessKey,'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});const result=await response.json();if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:JSON.stringify(result.detail));return result;}
function list(id){return $(id).value.split(/[,，\n]/).map(s=>s.trim()).filter(Boolean);}
async function reload(fill=false){data=await api('/api/research');research=data.config;config=await api('/api/config');
 if(fill){$('marketUrl').value=config.provider_options.base_url||'';$('insecure').checked=!!config.provider_options.allow_insecure_http;$('maxSymbols').value=config.provider_options.max_symbols||30;$('symbols').value=research.symbols.join(', ');$('themes').value=research.theme_names.join(', ');$('maxThemes').value=research.max_themes;$('maxNews').value=research.max_news_symbols;$('topicSeconds').value=research.topics_seconds;$('newsSeconds').value=research.news_seconds;$('auto').checked=research.enabled;$('pause').checked=research.pause_entries_on_risk;}
 $('topicJob').textContent=JSON.stringify(data.jobs.topics,null,2);$('newsJob').textContent=JSON.stringify(data.jobs.news,null,2);$('metadata').textContent=JSON.stringify(data.metadata,null,2);
 const tbody=$('themeRows');tbody.replaceChildren();for(const t of data.metadata.themes||[]){const cat=(data.metadata.catalog||[]).find(c=>c.id===t.id)||{};const tr=node('tr');[t.name,cat.change_pct===null||cat.change_pct===undefined?'未知':cat.change_pct+'%',t.members.length,t.known_at].forEach(x=>tr.append(node('td',x)));tbody.append(tr);}
 $('pool').textContent='涨停池观察日期：'+(data.metadata.market_date||'未采集')+'；返回 '+(data.metadata.limit_up_pool||[]).length+' 条。非完整历史数据库。';
 renderEvents();message('当前工作区：'+data.mode+'；行情通道：'+config.provider+'。'+data.coverage_note);}
function renderEvents(){const filter=$('search').value.trim();const risks=e=>e.risk_words.length&&['pending','tracking'].includes(e.status);const rows=(data.events||[]).filter(e=>(!$('riskOnly').checked||risks(e))&&(!filter||JSON.stringify([e.title,e.symbols,e.themes]).includes(filter)));$('counts').textContent=rows.length+' 条（最多显示最近200个事件）';$('events').replaceChildren();
 if(!rows.length){$('events').append(node('p','没有符合筛选条件的已采集事件；不代表没有消息或风险。','sub'));return;}
 for(const e of rows){const card=node('article',undefined,'event'+(risks(e)?' risk':''));card.append(node('h3',e.title));const tags=node('div');[e.status,...e.symbols,...e.themes,...e.risk_words].forEach(t=>tags.append(node('span',t,'pill')));card.append(tags);
 card.append(node('p','首次发现：'+e.first_seen_at+'　发布：'+(e.latest.published_at||'来源未提供')+'　发生：'+(e.latest.occurred_at||'未确认'),'sub'));
 card.append(node('p',e.latest.title_only?'仅公告目录标题，正文未读取。':e.latest.text||'来源未提供正文。'));
 card.append(node('small','来源：'+e.sources.join('、')+'；'+e.versions.length+' 个留存版本。转载数量不是独立确认次数。'));
 const actions=node('div',undefined,'row');if(e.latest.url){const a=node('a','查看来源');a.href=e.latest.url;a.target='_blank';a.rel='noopener noreferrer';actions.append(a);}
 const select=node('select');[['pending','待核实'],['tracking','跟踪中'],['reviewed','已人工复核'],['disproved','已澄清／证伪'],['closed','已关闭']].forEach(([v,t])=>{const o=node('option',t);o.value=v;select.append(o);});select.value=e.status;actions.append(select);
 const review=node('button','保存复核');review.onclick=()=>act(async()=>{const note=prompt('记录复核理由（至少5个字符；不是价格预测）');if(!note)return;const url=prompt('证据链接（可留空）','')||'';await api('/api/research/events/'+e.id,'PATCH',{status:select.value,note,evidence_url:url});await reload();});actions.append(review);
 const versions=node('button','查看留存版本');versions.onclick=()=>act(async()=>{const r=await api('/api/research/events/'+e.id+'/versions');$('details').hidden=false;$('detailText').textContent=JSON.stringify(r,null,2);$('details').scrollIntoView({behavior:'smooth'});});actions.append(versions);
 const ai=node('button','AI 摘要（仅此条原文）');ai.disabled=!data.llm_enabled;ai.onclick=()=>act(async()=>{const r=await api('/api/research/events/'+e.id+'/summarize','POST',{});$('details').hidden=false;$('detailText').textContent=JSON.stringify(r,null,2);$('details').scrollIntoView({behavior:'smooth'});});actions.append(ai);card.append(actions);$('events').append(card);}
}
async function act(fn){try{await fn();}catch(e){message(e.message,true);}}
$('unlock').onclick=()=>act(async()=>{accessKey=$('key').value;sessionStorage.setItem('radar_research_key',accessKey);await reload(true);});
$('activate').onclick=()=>act(async()=>{const options={base_url:$('marketUrl').value.trim(),allow_insecure_http:$('insecure').checked,max_symbols:Number($('maxSymbols').value),history_days:90,collection_timeout_seconds:120};if($('marketToken').value)options.token=$('marketToken').value;await api('/api/config','PUT',{provider:'tencent_baostock',mode:'live',provider_options:options});$('marketToken').value='';await reload();});
$('saveResearch').onclick=()=>act(async()=>{await api('/api/research/config','PUT',{...research,enabled:$('auto').checked,pause_entries_on_risk:$('pause').checked,symbols:list('symbols'),theme_names:list('themes'),max_themes:Number($('maxThemes').value),max_news_symbols:Number($('maxNews').value),topics_seconds:Number($('topicSeconds').value),news_seconds:Number($('newsSeconds').value)});await reload();});
$('marketRefresh').onclick=()=>act(async()=>{message('本轮行情采集中；失败会保留错误，不会切换到模拟数据。');await api('/api/refresh','POST',{provider:'tencent_baostock'});await reload();});
for(const k of ['topics','news'])$(k).onclick=()=>act(async()=>{await api('/api/research/collect/'+k,'POST',{});await reload();});
$('reload').onclick=()=>act(()=>reload());$('search').oninput=renderEvents;$('riskOnly').onchange=renderEvents;
$('importNews').onclick=()=>act(async()=>{await api('/api/research/import','POST',JSON.parse($('importJson').value));await reload();});
act(()=>reload(true));setInterval(()=>{if(Object.values(data.jobs||{}).some(j=>j.state==='running'))act(()=>reload());},5000);
