"use strict";
// Data and operations come only from the local server. Missing values stay missing.
(() => {
  const $=id=>document.getElementById(id);
  const state={dashboard:null,config:null,providers:[],events:[],trades:[],theme:null,error:null,loading:false,authOpen:false,lastLoaded:null};
  const viewNames={radar:"主线与候选",plans:"交易计划",portfolio:"持仓与风控",review:"复盘记录",settings:"数据源与设置"};
  const modeNames={demo:"DEMO 演示",live:"实时源研究",historical:"历史快照"};
  const statusNames={ready:"条件确认",watch:"等待确认",blocked:"暂不可执行",expired:"已过期",active:"跟踪中",planned:"已计划",cancelled:"已取消",filled:"已记录成交",closed:"已结束",triggered:"已触发",cannot_sell:"暂无法卖出",review:"待核实"};
  const strategyNames={expansion:"主线扩散",pullback:"回踩转强"};
  const riskFields=[
    ["risk_per_trade_pct","单笔风险预算","%",0.01,0.01,5,"按账户权益计算的计划亏损预算"],
    ["max_position_pct","单只持仓上限","%",0.1,0.1,100,"单个标的的计划持仓比例"],
    ["max_theme_pct","同主题持仓上限","%",0.1,0.1,100,"关联题材共同计入主题风险"],
    ["trailing_stop_pct","移动保护回撤","%",0.1,0.1,30,"从跟踪高点计算的保护阈值"],
    ["max_holding_sessions","最长观察持仓","交易日",1,1,250,"达到期限后提示复查逻辑"],
    ["max_quote_age_seconds","行情最大延迟","秒",1,15,3600,"旧行情不能触发新信号"],
    ["slippage_pct","滑点预留","%",0.01,0,10,"计划使用的价格偏差预留"],
    ["fee_pct","费用预留","%",0.01,0,5,"计划使用的交易费用比例"],
    ["min_history_bars","最少历史日线","根",1,20,250,"历史数据不足时不确认策略"],
    ["min_theme_members","主线最少成员","只",1,2,100,"避免将单股强势当作板块扩散"],
    ["min_theme_up_ratio","主线上涨成员占比","%",1,50,100,"规则阈值，不代表胜率","ratio"],
    ["min_theme_change_pct","主线平均涨幅阈值","%",0.1,0,20,"主线强度的基础过滤条件"],
    ["entry_buffer_pct","突破确认缓冲","%",0.1,0,5,"为入场边界保留确认空间"],
    ["max_chase_pct","最大追价距离","%",0.1,0,10,"超出计划后不继续扩大追价"],
    ["max_stop_distance_pct","最大止损距离","%",0.1,0.1,20,"止损距离过大时阻断候选"]
  ];
  const h=value=>String(value==null?"":value).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
  const num=v=>v==null||v===""||typeof v==="boolean"?null:Number.isFinite(Number(v))?Number(v):null;
  const arr=v=>Array.isArray(v)?v:[];
  const fmt=(v,d=2)=>num(v)==null?"—":new Intl.NumberFormat("zh-CN",{minimumFractionDigits:d,maximumFractionDigits:d}).format(Number(v));
  const money=v=>num(v)==null?"—":"¥"+fmt(v);
  const pct=v=>num(v)==null?"—":(Number(v)>0?"+":"")+fmt(v)+"%";
  const tone=v=>num(v)==null?"":Number(v)>0?"up":Number(v)<0?"down":"";
  const mode=()=>state.dashboard?.mode||state.config?.mode||null;
  const isDemo=()=>mode()==="demo";
  const dataFailed=()=>Boolean(state.error||state.dashboard?.data_status?.last_error);
  const candidates=()=>arr(state.dashboard?.analysis?.candidates);
  const themes=()=>arr(state.dashboard?.analysis?.themes);
  const plans=()=>arr(state.dashboard?.plans);
  const positions=()=>arr(state.dashboard?.positions);
  const badge=(label,type="neutral")=>'<span class="badge '+h(type)+'">'+h(label)+'</span>';
  const statusBadge=s=>badge(statusNames[s]||s||"未知",({ready:"info",watch:"neutral",blocked:"warning",expired:"neutral",active:"info",triggered:"danger",cannot_sell:"danger",review:"warning"})[s]||"neutral");
  const messageText=v=>typeof v==="string"?v:v?.message||v?.reason||v?.detail||JSON.stringify(v);
  function dateTime(value,short=false){if(!value||Number.isNaN(Date.parse(value)))return"未提供";return new Intl.DateTimeFormat("zh-CN",{timeZone:"Asia/Shanghai",...(short?{}:{year:"numeric",month:"2-digit",day:"2-digit"}),hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false}).format(new Date(value));}
  function safeURL(value){try{const u=new URL(value);return["http:","https:"].includes(u.protocol)?u.href:null;}catch{return null;}}
  function empty(title,text,mark="◎",action=""){return'<div class="empty-state"><div class="empty-mark" aria-hidden="true">'+mark+'</div><h3>'+h(title)+'</h3><p>'+h(text)+'</p>'+action+'</div>';}
  function toast(message,type="info"){const el=document.createElement("div");el.className="toast "+type;el.innerHTML='<span aria-hidden="true">'+(type==="error"?"!":type==="success"?"✓":"i")+'</span><span>'+h(message)+'</span>';$("toast-region").append(el);setTimeout(()=>el.remove(),type==="error"?10000:5000);}
  function errorMessage(error){return error?.name==="AbortError"?"请求超时，结果尚未确认。请刷新记录核对后再决定是否重试。":error?.message||"请求未完成，请检查服务状态。";}
  async function request(path,options={}){
    const collectionLimit=Math.max(5,Math.min(600,num(state.config?.provider_options?.collection_timeout_seconds)??120));
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),path==="/api/refresh"?(collectionLimit+20)*1000:120000),headers={Accept:"application/json",...(options.body?{"Content-Type":"application/json"}:{})};
    const key=sessionStorage.getItem("hotstock_api_key");if(key)headers["X-API-Key"]=key;
    try{const response=await fetch(path,{...options,headers:{...headers,...options.headers},signal:controller.signal,cache:"no-store"});let data;
      try{data=await response.json();}catch{throw new Error("服务没有返回可读取的数据（"+response.status+"）。");}
      if(!response.ok){const detail=data?.detail??data?.error??data?.message;const text=Array.isArray(detail)?detail.map(i=>arr(i.loc).filter(p=>p!=="body").join(".")+"："+(i.msg||messageText(i))).join("；"):typeof detail==="string"?detail:detail?JSON.stringify(detail):"服务返回错误（"+response.status+"）";const error=new Error(text);error.status=response.status;throw error;}return data;
    }finally{clearTimeout(timer);}
  }
  async function busy(button,work){if(button?.disabled)return;if(button){button.disabled=true;button.setAttribute("aria-busy","true");}try{return await work();}catch(e){toast(errorMessage(e),"error");if([401,403].includes(e.status))openAuth();}finally{if(button){button.disabled=false;button.removeAttribute("aria-busy");}}}
  function go(view){if(!Object.hasOwn(viewNames,view))view="radar";document.querySelectorAll(".view").forEach(el=>{const active=el.id==="view-"+view;el.hidden=!active;el.classList.toggle("active",active);});document.querySelectorAll(".nav-item").forEach(el=>{const active=el.dataset.view===view;el.classList.toggle("active",active);if(active)el.setAttribute("aria-current","page");else el.removeAttribute("aria-current");});$("view-title").textContent=viewNames[view];document.title=viewNames[view]+" · 热点雷达";}
  function openDialog(title,eyebrow,html){$("dialog-title").textContent=title;$("dialog-eyebrow").textContent=eyebrow;$("dialog-body").innerHTML=html;if(!$("app-dialog").open)$("app-dialog").showModal();}
  function closeDialog(){$("app-dialog").close();state.authOpen=false;}
  function field(id,label,value,unit="",o={}){return'<div class="field'+(o.wide?" field-wide":"")+'"><label for="'+h(id)+'">'+h(label)+'</label><div class="'+(unit?"input-with-unit":"")+'"><input id="'+h(id)+'" name="'+h(id)+'" type="'+h(o.type||"number")+'"'+(o.type==="text"?"":' step="'+h(o.step??0.01)+'"')+(o.min!=null?' min="'+h(o.min)+'"':"")+(o.max!=null?' max="'+h(o.max)+'"':"")+(o.required?" required":"")+' value="'+h(value??"")+'">'+(unit?'<span>'+h(unit)+'</span>':"")+'</div>'+(o.help?'<small>'+h(o.help)+'</small>':"")+'</div>';}
  function readNumber(form,name,nullable=false){const v=new FormData(form).get(name);if(nullable&&(v==null||v===""))return null;const result=num(v);if(result==null)throw new Error("请填写有效数字："+name);return result;}
  function formError(form,error){let el=form.querySelector(".form-error");if(!el){el=document.createElement("p");el.className="form-error";el.setAttribute("role","alert");form.append(el);}el.textContent=errorMessage(error);el.scrollIntoView({block:"nearest"});}
  function quoteFor(symbol){return arr(state.dashboard?.quotes).find(q=>q.symbol===symbol)||null;}
  function qualityIssues(){const d=state.dashboard?.data_status||{},values=[...arr(state.dashboard?.analysis?.quality_issues),...arr(d.warnings)];if(d.error)values.unshift(messageText(d.error));if(d.last_error)values.unshift(messageText(d.last_error));if(d.stale===true||d.is_stale===true)values.unshift("行情已经超过时效要求");return[...new Set(values.map(messageText).filter(Boolean))];}
  function renderGlobal(){
    const m=mode(),d=state.dashboard,b=$("global-banner");
    $("sidebar-mode").textContent=modeNames[m]||"尚未载入数据";$("sidebar-mode-dot").className="status-dot "+(m==="live"&&!state.error?"":"amber");
    $("source-mode-badge").className="badge "+(m==="demo"?"demo":"neutral");$("source-mode-badge").textContent=modeNames[m]||"未连接";
    if(dataFailed()){$("connection-badge").className="badge danger";$("connection-badge").textContent="读取失败";b.className="notice notice-danger";b.innerHTML='<span class="notice-icon">!</span><div><strong>本次数据读取失败。</strong> '+h(state.error||state.dashboard?.data_status?.last_error)+(d?" 下方保留的是上次读取结果，不能视为已更新行情。":" 数据尚未就绪，不产生可执行信号。")+'</div>';}
    else if(d){$("connection-badge").className="badge neutral";$("connection-badge").textContent="服务已连接";
      if(m==="demo"){b.className="notice notice-warning";b.innerHTML='<span class="notice-icon">!</span><div><strong>DEMO 演示环境</strong> · 股票、行情、信号和成交均用于模拟演示，不代表当前真实市场。演示账户与其他模式独立。</div>';}
      else if(m==="historical"){b.className="notice notice-warning";b.innerHTML='<span class="notice-icon">!</span><div><strong>历史快照研究</strong> · 数据截至 '+h(dateTime(d.as_of))+'，历史条件不能作为当前实时买入信号。</div>';}
      else{const issues=qualityIssues();b.className="notice "+(issues.length?"notice-warning":"notice-info");b.innerHTML='<span class="notice-icon">'+(issues.length?"!":"i")+'</span><div><strong>'+(issues.length?"数据仍有待核验项":"实时源研究环境")+'</strong> · '+h(issues.length?issues.slice(0,2).join("；"):"行情截至 "+dateTime(d.as_of))+'。候选与止损用于研究和提醒，系统不自动下单。</div>';}
    }
    $("record-buy").innerHTML='<span aria-hidden="true">＋</span> '+(isDemo()?"记录演示买入":"记录已成交买入");$("record-sell").textContent=isDemo()?"记录演示卖出":"记录已成交卖出";
    $("metric-signals").textContent=dataFailed()?"—":d?String(candidates().filter(c=>c.status==="ready").length):"—";$("metric-signal-note").textContent=dataFailed()?"读取失败，暂停显示确认状态":isDemo()?"模拟规则确认，不可用于实盘":"条件确认仍需核对可成交性";
    if(state.lastLoaded)$("last-refreshed").textContent="页面读取于 "+dateTime(state.lastLoaded)+"（北京时间）";
  }
  function renderRadar(){
    const ts=themes(),cs=candidates();$("metric-themes").textContent=state.dashboard?ts.length:"—";$("metric-candidates").textContent=state.dashboard?cs.length:"—";$("metric-holdings").textContent=state.dashboard?positions().length:"—";$("metric-holding-note").textContent=isDemo()?"当前为隔离的演示账户":"按当前模式的成交记录统计";$("theme-count").textContent=ts.length?ts.length+" 条主线":"暂无主线";
    if(state.theme&&!ts.some(t=>t.id===state.theme))state.theme=null;
    $("themes-container").innerHTML=ts.length?ts.map((t,i)=>'<button class="theme-card'+(state.theme===t.id?" selected":"")+'" data-theme="'+h(t.id)+'" aria-pressed="'+(state.theme===t.id)+'"><div class="theme-top"><span class="theme-rank">THEME '+String(i+1).padStart(2,"0")+'</span>'+badge(({expansion:"扩散中",weakening:"走弱",mixed:"分化观察",blocked:"证据待核验"})[t.status]||t.status||"观察")+'</div><h3>'+h(t.name)+'</h3><p>'+h(fmt(t.member_count,0))+' 只成员 · 均涨幅 <span class="'+tone((t.avg_change_pct??t.observed_avg_change_pct))+'">'+h(pct((t.avg_change_pct??t.observed_avg_change_pct)))+'</span></p><div class="theme-stats"><span>热度排序分</span><strong class="theme-score">'+h(fmt(t.heat_score,0))+'</strong></div><div class="heat-track" aria-hidden="true"><span style="width:'+Math.max(0,Math.min(100,num(t.heat_score)||0))+'%"></span></div>'+(t.observed_only?'<div class="reference-label">参考快照 · 行情时点未核实</div>':"")+'</button>').join(""):empty("还没有主线数据","运行演示，或接入行情后开始分析。");
    renderCandidates();renderDataStatus();
  }
  function renderCandidates(){
    const search=$("candidate-search").value.trim().toLowerCase(),filter=$("candidate-status-filter").value;
    const list=candidates().filter(c=>(!state.theme||c.theme_id===state.theme)&&(filter==="all"||c.status===filter)&&(!search||[c.symbol,c.name,c.theme_name].join(" ").toLowerCase().includes(search)));
    $("candidate-count").textContent=list.length;$("candidate-subtitle").textContent=state.theme?"正在查看 "+(themes().find(t=>t.id===state.theme)?.name||"所选主线")+"；再次点击主线可显示全部":"每个候选都保留入选理由和触发条件";
    $("candidates-container").innerHTML=list.length?'<table><thead><tr><th>标的 / 主线</th><th>策略</th><th>观察价格区间</th><th>条件状态</th><th>操作</th></tr></thead><tbody>'+list.map(c=>'<tr><td><span class="stock-name">'+h(c.name)+'</span><span class="stock-code">'+h(c.symbol)+'</span><span class="cell-sub">'+h(c.theme_name||"主题待核实")+'</span></td><td>'+h(strategyNames[c.strategy]||c.strategy||"观察")+(c.mode==="demo"?'<span class="cell-sub">DEMO 模拟</span>':"")+'</td><td><span class="cell-value">'+h(fmt(c.entry_min))+' – '+h(fmt(c.entry_max))+'</span><span class="cell-sub">计划止损 '+h(fmt(c.stop_price))+'</span></td><td>'+statusBadge(c.status)+'<span class="cell-sub">'+h(c.blockers?.[0]||c.reasons?.[0]||"等待完整依据")+'</span></td><td><div class="row-actions"><button class="text-button" data-detail="'+h(c.id)+'">详情</button><button class="button button-secondary button-small" data-plan="'+h(c.id)+'"'+(["blocked","expired"].includes(c.status)||dataFailed()?" disabled":"")+'>计划</button></div></td></tr>').join("")+'</tbody></table>':empty(candidates().length?"没有符合筛选条件的候选":"先发现，再观察",candidates().length?"试试其他主线、名称或条件状态。":"数据分析完成后，候选及依据会显示在这里。","↗");
  }
  function renderDataStatus(){
    const d=state.dashboard,issues=qualityIssues();
    const rows=[["运行模式",badge(modeNames[mode()]||"未知",isDemo()?"demo":"neutral")],["数据来源",h(d?.source||"未提供")],["行情时间",h(dateTime(d?.as_of))],["接收时间",h(dateTime(d?.received_at))],["质量问题",badge(dataFailed()?"本次读取失败":issues.length?issues.length+" 项待核验":"未报告问题",dataFailed()||issues.length?"warning":"neutral")],["自动采集",h(d?.scheduler?.enabled===true?"已启用":state.config?.auto_refresh===true?"已配置，状态待核验":"未启用")],["生效采集间隔",h(num(d?.scheduler?.effective_refresh_seconds)==null?"待后端确认":fmt(d.scheduler.effective_refresh_seconds,0)+" 秒")]];
    $("radar-data-status").innerHTML=rows.map(r=>'<div class="status-row"><span>'+h(r[0])+'</span><span>'+r[1]+'</span></div>').join("");
    if($("source-summary"))$("source-summary").innerHTML='<dl>'+rows.map(r=>'<div class="source-detail-row"><dt>'+h(r[0])+'</dt><dd>'+r[1]+'</dd></div>').join("")+'</dl>'+(issues.length?'<div class="notice notice-warning"><div>'+issues.slice(0,5).map(i=>'<p>'+h(i)+'</p>').join("")+'</div></div>':"");
  }
  function renderPlans(){
    const list=plans();$("plan-count").textContent=list.length;
    $("plans-container").innerHTML=list.length?'<table><thead><tr><th>标的 / 策略</th><th>冻结进场区间</th><th>止损价格</th><th>计划股数 / 风险</th><th>状态</th><th>操作</th></tr></thead><tbody>'+list.map(p=>{
      const size=p.sizing||p.position_size||{},qty=size.quantity??p.quantity??p.planned_quantity;
      return'<tr><td><span class="stock-name">'+h(p.name||p.symbol)+'</span><span class="stock-code">'+h(p.symbol)+'</span><span class="cell-sub">'+h(strategyNames[p.strategy]||p.strategy||"")+" · "+h(modeNames[p.mode]||p.mode||"")+'</span></td><td><span class="cell-value">'+h(fmt(p.entry_min))+' – '+h(fmt(p.entry_max))+'</span><span class="cell-sub">创建于 '+h(dateTime(p.created_at))+'</span></td><td class="cell-value">'+h(fmt(p.stop_price))+'</td><td><span class="cell-value">'+(qty==null?"待测算":h(fmt(qty,0))+" 股")+'</span><span class="cell-sub">计划亏损 '+h(money(size.planned_loss??p.planned_loss))+'</span></td><td>'+statusBadge(p.status)+'<span class="cell-sub">'+h(size.reason||"")+'</span></td><td><div class="row-actions"><button class="text-button" data-plan-detail="'+h(p.id)+'">查看</button>'+(!["cancelled","closed"].includes(p.status)?'<button class="text-button" data-cancel-plan="'+h(p.id)+'">取消</button>':"")+'</div></td></tr>';
    }).join("")+'</tbody></table>':empty("第一笔计划，从风险开始","从候选创建计划，并保存当时的价格边界。","▤",'<button class="button button-secondary" data-click="new-plan">新建交易计划</button>');
  }
  function renderPortfolio(){
    const account=state.dashboard?.account||{},list=positions();$("portfolio-equity").textContent=money(account.equity);$("portfolio-cash").textContent=money(account.cash);
    const valid=list.every(p=>num(p.market_value)!=null&&p.price_is_stale!==true)&&!state.error,value=valid?list.reduce((s,p)=>s+Number(p.market_value),0):null;
    const pnl=valid&&list.every(p=>num(p.unrealized_pnl)!=null)?list.reduce((s,p)=>s+Number(p.unrealized_pnl),0):null;
    $("portfolio-value").textContent=state.dashboard?money(value):"—";$("portfolio-value-note").textContent=valid?"按已提供的持仓行情估算":"行情缺失或陈旧，暂不汇总估值";$("portfolio-pnl").textContent=state.dashboard?money(pnl):"—";$("portfolio-pnl").className=tone(pnl);$("holding-count").textContent=list.length;
    $("risk-alerts").innerHTML=arr(state.dashboard?.alerts).map(a=>'<div class="notice '+(a.level==="critical"?"notice-danger":a.level==="warning"?"notice-warning":"notice-info")+'"><span class="notice-icon">!</span><div><strong>'+h(a.name||a.symbol||"持仓提醒")+' · '+h(statusNames[a.status]||"风险观察")+'</strong> '+h(a.message)+'<p class="small">提醒时间 '+h(dateTime(a.triggered_at))+' · 可卖数量 '+(a.sellable_quantity==null?"待核实":h(fmt(a.sellable_quantity,0))+" 股")+' · 触发不代表已卖出</p></div></div>').join("");
    $("holdings-container").innerHTML=list.length?'<table><thead><tr><th>标的 / 主线</th><th>持仓 / 可卖</th><th>成本 / 参考价</th><th>持仓市值</th><th>浮动盈亏</th><th>操作</th></tr></thead><tbody>'+list.map(p=>'<tr><td><span class="stock-name">'+h(p.name||p.symbol)+'</span><span class="stock-code">'+h(p.symbol)+'</span><span class="cell-sub">'+h(p.theme_name||themes().find(t=>t.id===p.theme_id)?.name||p.theme_id||"未关联主题")+'</span></td><td><span class="cell-value">'+h(fmt(p.quantity,0))+' 股</span><span class="cell-sub">可卖 '+(p.sellable_quantity==null?"待核实":h(fmt(p.sellable_quantity,0))+" 股")+'</span></td><td><span class="cell-value">'+h(fmt(p.avg_cost))+' / '+h(fmt(p.price))+'</span><span class="cell-sub">'+(p.price_is_stale?badge("陈旧参考价","warning"):"行情 "+h(dateTime(p.price_time,true)))+'</span></td><td>'+h(p.price_is_stale?"—":money(p.market_value))+'</td><td><span class="cell-value '+(p.price_is_stale?"":tone(p.unrealized_pnl))+'">'+h(p.price_is_stale?"—":money(p.unrealized_pnl))+'</span><span class="cell-sub '+(p.price_is_stale?"":tone(p.unrealized_pct))+'">'+h(p.price_is_stale?"待有效行情":pct(p.unrealized_pct))+'</span></td><td><button class="text-button" data-holding-detail="'+h(p.symbol)+'">批次</button><button class="text-button" data-sell-symbol="'+h(p.symbol)+'">记录卖出</button></td></tr>').join("")+'</tbody></table>':empty("还没有持仓记录",isDemo()?"当前为演示账户，可记录模拟成交验证风控。":"记录已实际成交的买入后开始跟踪。","◇");
    $("trades-container").innerHTML=state.trades.length?'<table><thead><tr><th>成交时间</th><th>标的</th><th>方向</th><th>股数</th><th>成交价</th><th>成交额</th></tr></thead><tbody>'+state.trades.map(t=>'<tr><td>'+h(dateTime(t.executed_at))+'</td><td><span class="stock-name">'+h(t.name||t.symbol)+'</span><span class="stock-code">'+h(t.symbol)+'</span></td><td>'+badge(t.side==="buy"?"买入":"卖出",t.side==="buy"?"danger":"success")+'</td><td>'+h(fmt(t.quantity,0))+'</td><td>'+h(fmt(t.price))+'</td><td>'+h(money(num(t.price)!=null&&num(t.quantity)!=null?Number(t.price)*Number(t.quantity):null))+'</td></tr>').join("")+'</tbody></table>':empty("暂无成交记录",isDemo()?"演示成交只影响演示账户。":"仅记录已在外部账户成交的买卖。","◷");
  }
  function renderEvents(){
    $("event-count").textContent=state.events.length;$("events-container").innerHTML=state.events.length?state.events.map(e=>{
      const d=e.detail,summary=typeof d==="string"?d:d?.message||d?.reason||d?.summary;
      return'<article class="event-item"><time class="event-time">'+h(dateTime(e.created_at))+'</time><span class="event-marker" aria-hidden="true"></span><div class="event-content"><h3>'+h(e.title||e.kind||"系统事件")+' '+(e.mode==="demo"?badge("DEMO","demo"):"")+'</h3>'+(summary?'<p>'+h(typeof summary==="string"?summary:JSON.stringify(summary))+'</p>':"")+(d&&typeof d==="object"?'<details><summary>查看当时记录的数据</summary><pre>'+h(JSON.stringify(d,null,2))+'</pre></details>':"")+'</div></article>';
    }).join(""):empty("你的研究记录从这里开始","运行分析或保存计划后，会产生可追溯事件。","◷");
  }
  function renderSettings(){
    const c=state.config;if(!c)return;const o=c.provider_options||{};
    const providers=[{id:"demo",name:"内置演示"},...state.providers.filter(p=>p.id!=="demo")],chosen=c.provider||"demo";
    $("source-status").innerHTML='<div id="source-summary"></div><form id="provider-form" class="provider-form"><div class="form-grid"><div class="field field-wide"><label for="provider-select">选用数据源</label><select id="provider-select" name="provider">'+providers.map(p=>'<option value="'+h(p.id)+'"'+(p.id===chosen?" selected":"")+'>'+h(p.name||p.id)+'</option>').join("")+'</select><small>切换模式后，账户、计划与成交记录分别保存。</small></div><div id="provider-description" class="field-wide"></div>'+
      '<div class="field field-wide provider-endpoint"><label id="provider-url-label" for="provider-base-url">数据服务 URL</label><input id="provider-base-url" name="base_url" type="url" value="'+h(o.base_url||(chosen==="tushare"?"https://api.tushare.pro":""))+'" placeholder="https://your-data-service.example/bundle"><small id="provider-url-help">使用当前数据源自己的服务地址。</small></div>'+
      '<div class="field field-wide provider-token"><label for="provider-token">数据源令牌</label><input id="provider-token" name="token" type="password" autocomplete="new-password" placeholder="留空保留已保存令牌"><small>令牌不会回显，也不会写入导出文件。</small></div>'+
      '<div class="field field-wide provider-endpoint"><label class="check-label"><input id="provider-insecure-http" name="allow_insecure_http" type="checkbox"'+(o.allow_insecure_http===true?" checked":"")+'> 允许可信私网 HTTP 携带令牌</label><small>仅在确认接收服务及网络可信时勾选，例如私网 QMT 数据源。HTTP 不加密传输，公网地址应使用 HTTPS。</small></div>'+
      '<div class="field field-wide provider-live"><label for="provider-symbols">跟踪代码</label><textarea id="provider-symbols" name="symbols" placeholder="300001.SZ, 600001.SH">'+h(arr(o.symbols).join(", "))+'</textarea><small>逗号或换行分隔；实际覆盖以数据源返回为准。</small></div>'+
      '<div class="field field-wide provider-live"><label for="provider-themes">关注主线</label><input id="provider-themes" name="theme_names" type="text" value="'+h(arr(o.theme_names).join(", "))+'" placeholder="机器人, AI 漫剧"></div>'+
      field("provider-max-symbols","最多跟踪标的",o.max_symbols??12,"只",{step:1,min:1,max:100})+field("provider-max-themes","最多采集主线",o.max_themes??3,"条",{step:1,min:1,max:10})+
      field("provider-history-days","历史回看",o.history_days??60,"日",{step:1,min:30,max:500})+field("provider-timeout","单次请求超时",o.timeout_seconds??15,"秒",{step:1,min:2,max:60})+
      field("provider-collection-timeout","整轮采集总限时",o.collection_timeout_seconds??120,"秒",{step:1,min:5,max:600,help:"限制一次完整采集的总耗时"})+field("refresh-seconds","期望自动采集间隔",c.refresh_seconds??60,"秒",{step:1,min:60,max:86400})+
      '<div class="field field-wide"><small id="provider-interval-note"></small><small>后台采集下限：AKShare 600 秒、Tushare 1800 秒、HTTP 60 秒。生效间隔取配置值与数据源下限的较大值。</small></div>'+
      '<div class="field field-wide"><label class="check-label"><input id="auto-refresh" type="checkbox" name="auto_refresh"'+(c.auto_refresh?" checked":"")+'> 启用后台自动采集</label><small>网页刷新不替代行情采集。</small></div></div><div class="inline-actions"><span class="form-help">修改后保存，再检测连接。</span><button type="submit" class="button button-secondary">保存数据源设置</button></div></form>';
    $("provider-select").addEventListener("change",updateProviderDescription);$("provider-base-url").addEventListener("input",()=>{$("provider-base-url").dataset.edited="true";});$("provider-form").addEventListener("submit",saveProvider);updateProviderDescription();
    const a=c.account||{},r=c.risk||{};
    const riskInput=f=>field("risk-"+f[0],f[1],f[7]==="ratio"&&num(r[f[0]])!=null?Number(r[f[0]])*100:r[f[0]],f[2],{step:f[3],min:f[4],max:f[5],help:f[6],required:true});
    $("settings-fields").innerHTML='<div class="field-wide form-section-title"><h3>当前模式账户 · '+h(modeNames[c.mode]||c.mode||"未配置")+'</h3><p>资金值用于计划测算；未知时留空，不生成虚构仓位。</p></div>'+field("account-equity","账户权益",a.equity,"元",{min:0,help:"请使用经确认的账户总权益"})+field("account-cash","可用现金",a.cash,"元",{min:0,help:"请使用经确认的可用现金"})+'<div class="field"><span>账户记录时间</span><p class="form-help">'+h(dateTime(a.updated_at))+'</p><small>这是本软件账本，不表示已连接券商。</small></div><div class="field-wide form-section-title"><h3>仓位与退出规则</h3></div>'+riskFields.slice(0,6).map(riskInput).join("")+'<details class="field-wide advanced-settings"><summary>展开策略阈值与费用设置</summary><div class="form-grid">'+riskFields.slice(6).map(riskInput).join("")+'<div class="field field-wide"><label class="check-label"><input id="strict-data-quality" type="checkbox"'+(r.strict_data_quality!==false?" checked":"")+'> 启用严格数据质量检查</label><small>来源、时点或历史数据不足时，候选保持观察或阻断状态。</small></div></div></details>';
    renderDataStatus();
  }
  function updateProviderDescription(){
    const select=$("provider-select"),id=select?.value;if(!id)return;const meta=state.providers.find(p=>p.id===id);
    const changed=select.dataset.renderedProvider&&select.dataset.renderedProvider!==id;
    if(changed){
      $("provider-token").value="";
      const saved=id===state.config?.provider?state.config?.provider_options||{}:{};
      $("provider-base-url").value=saved.base_url||(id==="tushare"?"https://api.tushare.pro":"");
      $("provider-base-url").dataset.edited="";
      $("provider-insecure-http").checked=saved.allow_insecure_http===true;
    }
    select.dataset.renderedProvider=id;
    document.querySelectorAll(".provider-endpoint").forEach(el=>el.hidden=!["http","tushare"].includes(id));document.querySelectorAll(".provider-token").forEach(el=>el.hidden=!["http","tushare"].includes(id));document.querySelectorAll(".provider-live").forEach(el=>el.hidden=id==="demo");
    $("provider-url-label").textContent=id==="tushare"?"Tushare API URL":"数据快照 URL";
    $("provider-base-url").placeholder=id==="tushare"?"https://api.tushare.pro":"https://your-data-service.example/bundle";
    $("provider-url-help").textContent=id==="tushare"?"默认 https://api.tushare.pro，可改为你确认支持的 Tushare 服务地址。":"由当前数据源返回完整行情快照，地址不会自动沿用其他数据源。";
    $("provider-token").placeholder=id===state.config?.provider&&state.config?.provider_options?.has_token?"已配置该数据源令牌；留空保留":"填写该数据源专属令牌；已保存的令牌可留空保留";
    const minimum=({akshare:600,tushare:1800,http:60,demo:60})[id]||60,effective=state.dashboard?.scheduler?.effective_refresh_seconds;
    $("provider-interval-note").textContent="当前选择最低间隔 "+minimum+" 秒。"+(id===state.config?.provider&&num(effective)!=null?"后端生效配置："+fmt(effective,0)+" 秒。":"保存后由后端确认生效间隔。");
    const limits=id==="demo"?["内置虚构行情，用于体验功能。不会产生真实下单，不影响其他模式账户。"]:arr(meta?.limitations),url=safeURL(meta?.docs_url);
    $("provider-description").innerHTML=(limits.length?'<div class="body-note">'+limits.map(t=>'<p>'+h(messageText(t))+'</p>').join("")+'</div>':"")+(meta?.install?'<p class="form-help">安装要求：'+h(meta.install)+'</p>':"")+(url?'<a class="text-button" href="'+h(url)+'" target="_blank" rel="noopener noreferrer">查看数据源说明 ↗</a>':"");
  }
  async function saveProvider(event){
    event.preventDefault();const form=event.currentTarget;if(!form.reportValidity())return;form.querySelector(".form-error")?.remove();const button=form.querySelector('button[type="submit"]');button.disabled=true;
    try{const f=new FormData(form),provider=String(f.get("provider")),o={};
      if(provider!=="demo"){
        o.max_symbols=readNumber(form,"provider-max-symbols");o.max_themes=readNumber(form,"provider-max-themes");o.history_days=readNumber(form,"provider-history-days");o.timeout_seconds=readNumber(form,"provider-timeout");o.collection_timeout_seconds=readNumber(form,"provider-collection-timeout");
        o.symbols=String(f.get("symbols")||"").split(/[\s,，;；]+/).map(x=>x.trim().toUpperCase()).filter(Boolean);o.theme_names=String(f.get("theme_names")||"").split(/[,\n，;；]+/).map(x=>x.trim()).filter(Boolean);
        if(["http","tushare"].includes(provider)){
          const url=String(f.get("base_url")||"").trim();
          const initialURL=provider==="tushare"?"https://api.tushare.pro":"";
          if(provider===state.config?.provider||$("provider-base-url").dataset.edited==="true"||url!==initialURL)o.base_url=url;
          o.allow_insecure_http=f.has("allow_insecure_http");
          const token=String(f.get("token")||"").trim();if(token)o.token=token;
        }
      }
      await request("/api/config",{method:"PUT",body:JSON.stringify({provider,provider_options:o,auto_refresh:f.has("auto_refresh"),refresh_seconds:readNumber(form,"refresh-seconds")})});toast("数据源设置已保存，请更新行情检查采集结果。","success");await loadAll();
    }catch(e){formError(form,e);}finally{button.disabled=false;}
  }
  async function saveRisk(event){
    event.preventDefault();const form=event.currentTarget;if(!form.reportValidity())return;form.querySelector(".form-error")?.remove();const button=form.querySelector('button[type="submit"]');button.disabled=true;$("settings-save-status").textContent="";
    try{const risk={};riskFields.forEach(f=>{risk[f[0]]=readNumber(form,"risk-"+f[0])/(f[7]==="ratio"?100:1);});risk.strict_data_quality=$("strict-data-quality").checked;const account={equity:readNumber(form,"account-equity",true),cash:readNumber(form,"account-cash",true)};await request("/api/config",{method:"PUT",body:JSON.stringify({risk,account})});await loadAll();$("settings-save-status").textContent="已保存当前模式的账户与风险配置。";toast("账户与风险配置已保存。","success");}catch(e){formError(form,e);}finally{button.disabled=false;}
  }
  function candidateDetail(id){
    const c=candidates().find(x=>String(x.id)===String(id));if(!c)return;const q=quoteFor(c.symbol),evidence=arr(c.evidence),allowed=!["blocked","expired"].includes(c.status)&&!dataFailed();
    openDialog(c.name+" · "+c.symbol,"CANDIDATE & EVIDENCE",
      '<div class="detail-hero"><div><div class="detail-label">'+h(q?"行情参考价":"观察进场区间")+'</div><div class="detail-price">'+h(q?fmt(q.price):fmt(c.entry_min)+" – "+fmt(c.entry_max))+'</div>'+(q?'<span class="'+tone(q.prev_close&&q.price?(q.price/q.prev_close-1)*100:null)+'">'+h(pct(q.prev_close&&q.price?(q.price/q.prev_close-1)*100:null))+'</span>':"")+'</div><div class="tag-list">'+statusBadge(c.status)+badge(strategyNames[c.strategy]||c.strategy||"观察")+(c.mode==="demo"?badge("DEMO","demo"):"")+'</div></div>'+
      (c.mode==="demo"?'<div class="notice notice-warning"><div>这是模拟候选。价格与规则仅用于演示，不能用于真实交易。</div></div>':"")+
      '<div class="detail-grid"><div><span>所属主线</span><strong>'+h(c.theme_name||"待核实")+'</strong></div><div><span>计划止损价格</span><strong>'+h(fmt(c.stop_price))+'</strong></div><div><span>观察进场区间</span><strong>'+h(fmt(c.entry_min))+' – '+h(fmt(c.entry_max))+'</strong></div><div><span>信号有效期限</span><strong>'+h(dateTime(c.expires_at))+'</strong></div><div><span>行情时间</span><strong>'+h(dateTime(c.quote_time))+'</strong></div><div><span>数据来源</span><strong>'+h(c.source||state.dashboard?.source||"未提供")+'</strong></div></div>'+
      '<div class="detail-section"><h3>入选依据</h3><ul class="checks-list">'+arr(c.reasons).map(r=>'<li><span class="check-pass">✓</span><span>'+h(r)+'</span></li>').join("")+'</ul>'+(arr(c.reasons).length?"":'<p>当前未提供完整入选依据。</p>')+'</div>'+
      (arr(c.blockers).length?'<div class="detail-section"><h3>尚未满足的条件</h3><ul class="checks-list">'+c.blockers.map(r=>'<li><span class="check-fail">!</span><span>'+h(r)+'</span></li>').join("")+'</ul></div>':"")+
      '<div class="detail-section"><h3>主题证据与原始来源</h3>'+(evidence.length?'<ul class="evidence-list">'+evidence.map(e=>{const url=safeURL(e.url);return'<li><strong>'+h(e.title||"主题证据")+'</strong><p>'+h(e.summary||"")+'</p><div class="evidence-meta"><span>发布 '+h(dateTime(e.published_at))+'</span><span>首次观察 '+h(dateTime(e.observed_at))+'</span><span>'+h(({verified:"已核验",unverified:"待核验",simulated:"模拟资料"})[e.confidence]||"核验状态未知")+'</span></div>'+(url?'<a href="'+h(url)+'" target="_blank" rel="noopener noreferrer">查看原始来源 ↗</a>':"")+'</li>';}).join("")+'</ul>':'<p>本候选尚未提供可核验原文，不能把主题关联视为已证实的收益关系。</p>')+'</div>'+
      '<div class="form-bottom"><p>评分与信号用于条件排序，不代表获利概率。止损触发不等于能够按指定价格卖出。</p><button id="detail-create-plan" class="button button-primary"'+(!allowed?" disabled":"")+'>创建观察计划</button></div>');
    $("detail-create-plan").addEventListener("click",()=>openPlan(c.id));
  }
  function planPreview(c){
    if(!c)return'<p class="form-help">请先选择一个候选。</p>';
    return'<div class="detail-grid"><div><span>冻结进场区间</span><strong>'+h(fmt(c.entry_min))+' – '+h(fmt(c.entry_max))+'</strong></div><div><span>计划止损</span><strong>'+h(fmt(c.stop_price))+'</strong></div><div><span>主线 / 策略</span><strong>'+h(c.theme_name||"待核实")+' · '+h(strategyNames[c.strategy]||c.strategy)+'</strong></div><div><span>条件状态</span><strong>'+h(statusNames[c.status]||c.status)+'</strong></div></div>';
  }
  function openPlan(id){
    const list=candidates().filter(c=>!["blocked","expired"].includes(c.status));
    if(dataFailed()){toast("请先刷新数据，确认候选状态。","error");return;}if(!list.length){toast("暂无可创建计划的候选，请查看数据与阻断原因。");location.hash="radar";return;}
    openDialog("新建观察计划","FREEZE YOUR TRADE PLAN",'<form id="plan-form"><div class="notice '+(isDemo()?"notice-warning":"notice-info")+'"><div>'+(isDemo()?"本计划属于演示模式，模拟账户与真实数据模式隔离。":"计划用于跟踪条件，保存不会创建券商委托。")+'</div></div><div class="field"><label for="plan-candidate">选择候选</label><select id="plan-candidate" name="candidate_id" required>'+list.map(c=>'<option value="'+h(c.id)+'"'+(String(c.id)===String(id)?" selected":"")+'>'+h(c.name)+" · "+h(c.symbol)+" · "+h(strategyNames[c.strategy]||c.strategy)+'</option>').join("")+'</select></div><div id="plan-preview" class="detail-section"></div><p class="form-help">创建时按账户与风险配置测算计划股数。价格边界会保存，创建本身不会下单。</p><div class="form-bottom"><p>条件失效时应重新评估，不能无限提高追价区间。</p><button type="submit" class="button button-primary">保存观察计划</button></div></form>');
    const update=()=>{$("plan-preview").innerHTML=planPreview(list.find(c=>String(c.id)===$("plan-candidate").value));};update();$("plan-candidate").addEventListener("change",update);
    $("plan-form").addEventListener("submit",async e=>{e.preventDefault();const form=e.currentTarget;if(!form.reportValidity())return;form.querySelector(".form-error")?.remove();const b=form.querySelector('button[type="submit"]');b.disabled=true;try{await request("/api/plans",{method:"POST",body:JSON.stringify({candidate_id:$("plan-candidate").value})});closeDialog();await loadAll();location.hash="plans";toast("计划已保存，尚未发生下单或成交。","success");}catch(error){formError(form,error);}finally{b.disabled=false;}});
  }
  function planDetail(id){
    const p=plans().find(x=>String(x.id)===String(id));if(!p)return;const s=p.sizing||p.position_size||{};
    openDialog(p.name||p.symbol,"SAVED TRADE PLAN",'<div class="tag-list">'+statusBadge(p.status)+badge(modeNames[p.mode]||p.mode||modeNames[mode()])+'</div><div class="detail-section">'+planPreview(p)+'</div><div class="detail-section"><h3>计划仓位与预算</h3><div class="detail-grid"><div><span>计划股数</span><strong>'+h(fmt(s.quantity??p.quantity??p.planned_quantity,0))+'</strong></div><div><span>计划金额</span><strong>'+h(money(s.notional??p.notional))+'</strong></div><div><span>计划亏损</span><strong>'+h(money(s.planned_loss??p.planned_loss))+'</strong></div><div><span>风险预算</span><strong>'+h(money(s.risk_budget??p.risk_budget))+'</strong></div></div><p class="form-help">'+h(s.reason||"")+'</p>'+arr(s.warnings).map(w=>'<p class="form-help">'+h(w)+'</p>').join("")+'</div><div class="detail-section"><h3>记录时间</h3><p>创建：'+h(dateTime(p.created_at))+'<br>到期：'+h(dateTime(p.expires_at))+'</p></div><div class="form-bottom"><p>只有记录实际成交，才会更新持仓。</p><button id="plan-record-buy" class="button button-primary">记录'+(isDemo()?"演示":"已成交")+'买入</button></div>');
    $("plan-record-buy").addEventListener("click",()=>openTrade("buy",p.symbol,p.id));
  }
  async function cancelPlan(id,b){await busy(b,async()=>{await request("/api/plans/"+encodeURIComponent(id),{method:"PATCH",body:JSON.stringify({status:"cancelled"})});await loadAll();toast("计划已取消，已有成交与持仓不受影响。","success");});}
  function tradeDateInput(){const now=isDemo()&&state.dashboard?.as_of?new Date(state.dashboard.as_of):new Date();return new Intl.DateTimeFormat("sv-SE",{timeZone:"Asia/Shanghai",year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false}).format(now).replace(" ","T");}
  function openTrade(side,symbol="",planId=""){
    const p=positions().find(x=>x.symbol===symbol),plan=plans().find(x=>String(x.id)===String(planId)),c=candidates().find(x=>x.symbol===symbol),q=quoteFor(symbol),name=p?.name||plan?.name||c?.name||q?.name||"";
    openDialog((isDemo()?"记录演示":"记录已成交")+(side==="buy"?"买入":"卖出"),"MANUAL FILL RECORD",
      '<form id="trade-form"><div class="notice '+(isDemo()?"notice-warning":"notice-info")+'"><div>'+(isDemo()?"你正在操作隔离的演示账本。这是模拟成交，不影响真实账户。":"请仅记录已经在外部账户实际成交的买卖，本表单不会发送交易委托。")+'</div></div><div class="form-grid">'+
      field("trade-symbol","股票代码",symbol,"",{type:"text",required:true,help:"包含交易所后缀，例如 300001.SZ"})+field("trade-name","股票名称",name,"",{type:"text",required:true})+
      field("trade-quantity","实际成交股数","","股",{step:1,min:1,required:true,help:side==="sell"?"可卖数量："+(p?.sellable_quantity==null?"待核实":fmt(p.sellable_quantity,0)+" 股"):"填写这笔交易实际成交的股数"})+
      field("trade-price","实际成交价格","","元",{step:0.001,min:0.001,required:true,help:"以实际成交回报为准"})+
      '<div class="field field-wide"><label for="trade-time">成交时间（北京时间）</label><input id="trade-time" name="executed_at" type="datetime-local" step="1" required value="'+h(tradeDateInput())+'"></div>'+
      '<div class="field field-wide"><label for="trade-plan">关联计划（可选）</label><select id="trade-plan" name="plan_id"><option value="">不关联计划</option>'+plans().filter(x=>!symbol||x.symbol===symbol).map(x=>'<option value="'+h(x.id)+'"'+(String(x.id)===String(planId)?" selected":"")+'>'+h(x.name||x.symbol)+" · "+h(x.symbol)+'</option>').join("")+'</select></div>'+
      '<div class="field field-wide"><label for="trade-theme">关联主线（可选）</label><select id="trade-theme" name="theme_id"><option value="">未关联主线</option>'+themes().map(t=>'<option value="'+h(t.id)+'"'+(t.id===(p?.theme_id||plan?.theme_id||c?.theme_id)?" selected":"")+'>'+h(t.name)+'</option>').join("")+'</select></div>'+
      '<div class="field field-wide"><label for="trade-notes">备注</label><textarea id="trade-notes" name="notes" placeholder="成交依据或交易记录编号"></textarea></div>'+
      '<div class="field field-wide"><label class="check-label"><input id="trade-confirmed" name="confirmed" type="checkbox" required> '+(isDemo()?"我确认保存这笔模拟成交记录":"我确认这笔买卖已经在外部账户实际成交")+'</label></div></div>'+
      '<div class="form-bottom"><p>保存后更新当前模式的现金与持仓。卖出需满足实际可卖数量和批次约束。</p><button type="submit" class="button button-primary">保存成交记录</button></div></form>');
    $("trade-symbol").addEventListener("change",()=>{const code=$("trade-symbol").value.trim().toUpperCase();$("trade-symbol").value=code;const known=positions().find(x=>x.symbol===code)||quoteFor(code)||candidates().find(x=>x.symbol===code);if(known)$("trade-name").value=known.name||"";});
    $("trade-plan").addEventListener("change",()=>{const chosen=plans().find(x=>String(x.id)===$("trade-plan").value);if(chosen){$("trade-symbol").value=chosen.symbol;$("trade-name").value=chosen.name||chosen.symbol;if(chosen.theme_id)$("trade-theme").value=chosen.theme_id;}});
    $("trade-form").addEventListener("submit",async e=>{
      e.preventDefault();const form=e.currentTarget;if(!form.reportValidity())return;form.querySelector(".form-error")?.remove();const b=form.querySelector('button[type="submit"]');b.disabled=true;
      try{const f=new FormData(form),raw=String(f.get("executed_at")),payload={symbol:String(f.get("trade-symbol")).trim().toUpperCase(),name:String(f.get("trade-name")).trim(),side,quantity:readNumber(form,"trade-quantity"),price:readNumber(form,"trade-price"),executed_at:raw+(raw.length===16?":00":"")+"+08:00",confirmed:f.has("confirmed"),notes:String(f.get("notes")||"")};
        if(f.get("plan_id"))payload.plan_id=String(f.get("plan_id"));if(f.get("theme_id"))payload.theme_id=String(f.get("theme_id"));
        await request("/api/trades",{method:"POST",body:JSON.stringify(payload)});closeDialog();await loadAll();location.hash="portfolio";toast("成交记录已保存，该操作没有发送交易委托。","success");
      }catch(error){formError(form,error);}finally{b.disabled=false;}
    });
  }
  function holdingDetail(symbol){
    const p=positions().find(x=>x.symbol===symbol);if(!p)return;
    openDialog((p.name||p.symbol)+" · 持仓批次","LOTS & SELLABLE QUANTITY",'<div class="notice notice-info"><div>可卖数量按买入批次和交易日历核验。数量未知时保持待核实，止损触发不会自动标记卖出。</div></div><div class="detail-grid"><div><span>总持仓</span><strong>'+h(fmt(p.quantity,0))+' 股</strong></div><div><span>当前可卖</span><strong>'+(p.sellable_quantity==null?"待核实":h(fmt(p.sellable_quantity,0))+" 股")+'</strong></div><div><span>加权成本</span><strong>'+h(fmt(p.avg_cost))+'</strong></div><div><span>参考行情时间</span><strong>'+h(dateTime(p.price_time))+'</strong></div></div><div class="detail-section"><h3>买入批次</h3>'+(arr(p.lots).length?'<ul class="evidence-list">'+p.lots.map(l=>'<li><strong>买入 '+h(dateTime(l.bought_at))+'</strong><p>原始数量 '+h(fmt(l.quantity,0))+' 股 · 剩余 '+h(fmt(l.remaining??l.quantity,0))+' 股</p><p>已核实可卖：'+(l.sellable_confirmed==null?"未单独确认":h(fmt(l.sellable_confirmed,0))+" 股")+'</p></li>').join("")+'</ul>':'<p>本持仓尚未提供批次明细。</p>')+'</div>');
  }
  function openAuth(){
    if(state.authOpen)return;state.authOpen=true;
    openDialog("访问当前服务","ACCESS KEY",'<form id="auth-form"><p class="body-note">如果服务启用了访问密钥，请填写运行服务时配置的密钥。密钥只保留在当前浏览器标签页的会话中。</p><div class="field"><label for="api-key-input">服务访问密钥</label><input id="api-key-input" type="password" name="api_key" autocomplete="off" placeholder="输入访问密钥"><small>留空保存可清除当前标签页的已保存密钥。</small></div><div class="form-bottom"><p>数据源令牌与服务访问密钥分别配置。</p><button type="submit" class="button button-primary">保存并连接</button></div></form>');
    $("auth-form").addEventListener("submit",async e=>{e.preventDefault();const key=$("api-key-input").value.trim();if(key)sessionStorage.setItem("hotstock_api_key",key);else sessionStorage.removeItem("hotstock_api_key");closeDialog();await loadAll();});
  }
  async function loadAll(){
    if(state.loading)return;state.loading=true;
    try{
      const results=await Promise.allSettled(["/api/dashboard","/api/config","/api/providers","/api/events?limit=100","/api/trades"].map(path=>request(path)));
      if(results[0].status==="fulfilled"){state.dashboard=results[0].value;state.error=null;state.lastLoaded=new Date().toISOString();}else state.error=errorMessage(results[0].reason);
      if(results[1].status==="fulfilled")state.config=results[1].value;
      if(results[2].status==="fulfilled")state.providers=arr(results[2].value.providers);
      if(results[3].status==="fulfilled")state.events=arr(results[3].value.items);
      if(results[4].status==="fulfilled")state.trades=arr(results[4].value.items);
      renderGlobal();renderRadar();renderPlans();renderPortfolio();renderEvents();renderSettings();renderNotifications();
      const labels=["行情看板","配置","数据源","事件记录","成交记录"],partial=results.map((r,i)=>r.status==="rejected"&&i!==0?labels[i]+"："+errorMessage(r.reason):null).filter(Boolean);
      $("load-error").hidden=!partial.length;$("load-error").classList.toggle("hidden",!partial.length);$("load-error").textContent=partial.length?"部分内容未能读取。"+partial.join("；"):"";
      if(results.some(r=>r.status==="rejected"&&[401,403].includes(r.reason?.status)))openAuth();
    }finally{state.loading=false;}
  }
  async function pollDashboard(){
    if(state.loading||document.hidden||!state.dashboard)return;
    try{state.dashboard=await request("/api/dashboard");state.error=null;state.lastLoaded=new Date().toISOString();renderGlobal();renderRadar();renderPlans();renderPortfolio();}
    catch(e){state.error=errorMessage(e);renderGlobal();renderDataStatus();if([401,403].includes(e.status))openAuth();}
  }
  async function runRefresh(provider,button){
    await busy(button,async()=>{try{const r=await request("/api/refresh",{method:"POST",body:JSON.stringify(provider?{provider}:{})});await loadAll();toast(r.mode==="demo"?"演示数据已加载，全部样例均为模拟。":"采集完成，请查看数据时点与质量检查。",arr(r.warnings).length?"info":"success");}catch(e){state.error=errorMessage(e);renderGlobal();renderDataStatus();await loadAll();throw e;}});
  }
  function downloadJSON(value,filename){const blob=new Blob([JSON.stringify(value,null,2)],{type:"application/json;charset=utf-8"}),url=URL.createObjectURL(blob),link=document.createElement("a");link.href=url;link.download=filename;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),30000);}
  function downloadTemplate(){
    const at="2026-10-20T10:30:00+08:00";
    downloadJSON({schema_version:1,source:"manual-demo-template",mode:"demo",as_of:at,received_at:at,quotes:[{symbol:"DEMO901",name:"模拟机器人样例",price:20,prev_close:19,open:19.1,high:20.2,low:19,volume:1000000,amount:20000000,quote_time:at,received_at:at,time_quality:"simulated",is_suspended:false,limit_up:22.8,limit_down:15.2,board:"CHINEXT",min_buy_qty:100,buy_qty_step:100}],bars:{DEMO901:[]},themes:[{id:"robotics-example",name:"机器人（模拟）",members:["DEMO901"],known_at:"2026-10-19T18:00:00+08:00",membership_quality:"simulated",evidence:[]}],calendar:["2026-10-19","2026-10-20","2026-10-21"],calendar_confirmed:false,news:[],capabilities:{quotes:true,themes:true,history:false,calendar:false,evidence:false},warnings:["这是格式示例，缺少足够历史数据与真实证据，不可用于实盘。"]},"hotstock-bundle-template.json");
    toast("已下载格式示例，模拟数据的历史与证据字段需要补全。");
  }
  async function importSnapshot(){
    const file=$("snapshot-file").files?.[0];if(!file)return;
    await busy($("import-snapshot"),async()=>{$("import-status").textContent="正在校验并导入…";try{
      if(file.size>8*1024*1024)throw new Error("文件超过 8 MiB，请缩小快照后重试。");let data;try{data=JSON.parse(await file.text());}catch{throw new Error("无法读取 JSON，请检查文件格式。");}
      await request("/api/import",{method:"POST",body:JSON.stringify(data)});await loadAll();$("import-status").textContent="导入完成，请查看模式、质量检查与来源时间。";toast("数据快照已导入。","success");
    }catch(e){$("import-status").textContent="导入未完成："+errorMessage(e);throw e;}});
  }
  function renderNotifications(){
    if(!$("notification-settings")||!state.config)return;
    const n=state.config.notifications||{};
    $("notification-settings").innerHTML='<form id="notification-form"><p class="body-note">将非演示模式的新风险提醒发送到你自己的通用 JSON Webhook 接收器。企业微信等平台需要自行适配其消息格式，不能直接保证兼容。</p><div class="form-grid">'+
      '<div class="field field-wide"><label class="check-label"><input id="notification-enabled" name="enabled" type="checkbox"'+(n.enabled?" checked":"")+'> 启用后台风险通知</label><small>关闭网页后，由运行中的本地服务继续监控；服务停止后无法通知。</small></div>'+
      '<div class="field field-wide"><label for="notification-url">Webhook URL</label><input id="notification-url" name="webhook_url" type="url" placeholder="'+h(n.has_webhook_url?"已配置 "+(n.webhook_host||"接收端")+"；留空保留":"https://your-receiver.example/alerts")+'" autocomplete="off"><small>已保存地址不回显，避免暴露路径内的访问凭据。</small></div>'+
      '<div class="field field-wide"><label for="notification-token">通知访问令牌（可选）</label><input id="notification-token" name="token" type="password" autocomplete="new-password" placeholder="'+(n.has_token?"已配置；留空保留":"作为 Bearer Token 发送")+'"></div></div>'+
      '<div class="form-bottom"><p>只有点击“发送测试通知”才主动测试；保存配置不会发送测试消息。</p><div class="heading-actions"><button id="test-notification" type="button" class="button button-secondary">发送测试通知</button><button type="submit" class="button button-primary">保存通知设置</button></div></div></form>';
    $("notification-form").addEventListener("submit",async e=>{e.preventDefault();const form=e.currentTarget;if(!form.reportValidity())return;form.querySelector(".form-error")?.remove();const b=form.querySelector('button[type="submit"]');b.disabled=true;
      try{const f=new FormData(form),n={enabled:f.has("enabled")},url=String(f.get("webhook_url")||"").trim(),token=String(f.get("token")||"").trim();if(url)n.webhook_url=url;if(token)n.token=token;await request("/api/config",{method:"PUT",body:JSON.stringify({notifications:n})});await loadAll();toast("通知配置已保存，没有发送测试消息。","success");}catch(error){formError(form,error);}finally{b.disabled=false;}});
    $("test-notification").addEventListener("click",()=>busy($("test-notification"),async()=>{if($("notification-url").value.trim()||$("notification-token").value.trim())throw new Error("请先保存新通知配置，再发送测试。");await request("/api/notifications/test",{method:"POST",body:"{}"});toast("接收端已响应测试通知，请在接收端确认消息。","success");const data=await request("/api/events?limit=100");state.events=arr(data.items);renderEvents();}));
  }
  function bind(){
    document.querySelectorAll("[data-view]").forEach(b=>b.addEventListener("click",()=>{location.hash=b.dataset.view;}));document.querySelectorAll("[data-go]").forEach(b=>b.addEventListener("click",()=>{location.hash=b.dataset.go;}));window.addEventListener("hashchange",()=>go(location.hash.slice(1)));
    $("local-date").textContent=new Intl.DateTimeFormat("zh-CN",{timeZone:"Asia/Shanghai",year:"numeric",month:"2-digit",day:"2-digit",weekday:"short"}).format(new Date());
    $("close-dialog").addEventListener("click",closeDialog);$("app-dialog").addEventListener("close",()=>{state.authOpen=false;});
    $("app-dialog").addEventListener("click",e=>{if(e.target===$("app-dialog")){const r=$("app-dialog").getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)closeDialog();}});
    $("manage-api-key").addEventListener("click",openAuth);$("refresh-all").addEventListener("click",()=>busy($("refresh-all"),loadAll));
    $("run-demo").addEventListener("click",()=>runRefresh("demo",$("run-demo")));$("refresh-market").addEventListener("click",()=>runRefresh(null,$("refresh-market")));$("settings-refresh").addEventListener("click",()=>runRefresh(null,$("settings-refresh")));
    $("detect-source").addEventListener("click",()=>{if($("provider-select")&&$("provider-select").value!==state.config?.provider){toast("请先保存所选数据源，再检测连接。");return;}runRefresh(null,$("detect-source"));});
    $("new-plan").addEventListener("click",()=>openPlan());$("record-buy").addEventListener("click",()=>openTrade("buy"));$("record-sell").addEventListener("click",()=>openTrade("sell"));
    $("candidate-search").addEventListener("input",renderCandidates);$("candidate-status-filter").addEventListener("change",renderCandidates);$("risk-settings-form").addEventListener("submit",saveRisk);
    $("snapshot-file").addEventListener("change",()=>{const f=$("snapshot-file").files?.[0];$("selected-file-name").textContent=f?f.name:"选择 JSON 文件";$("import-snapshot").disabled=!f;$("import-status").textContent="";});
    $("import-snapshot").addEventListener("click",importSnapshot);$("download-template").addEventListener("click",downloadTemplate);
    $("refresh-logs").addEventListener("click",()=>busy($("refresh-logs"),async()=>{const d=await request("/api/events?limit=100");state.events=arr(d.items);renderEvents();toast("事件记录已刷新。");}));
    $("export-data").addEventListener("click",()=>busy($("export-data"),async()=>{const d=await request("/api/export");downloadJSON(d,"hotstock-"+(mode()||"data")+"-"+new Date().toISOString().slice(0,10)+".json");toast("当前模式的记录已导出，配置密钥不包含在导出中。","success");}));
    document.addEventListener("click",e=>{const b=e.target.closest("button");if(!b)return;if(b.dataset.theme){state.theme=state.theme===b.dataset.theme?null:b.dataset.theme;renderRadar();}if(b.dataset.detail)candidateDetail(b.dataset.detail);if(b.dataset.plan)openPlan(b.dataset.plan);if(b.dataset.planDetail)planDetail(b.dataset.planDetail);if(b.dataset.cancelPlan)cancelPlan(b.dataset.cancelPlan,b);if(b.dataset.sellSymbol)openTrade("sell",b.dataset.sellSymbol);if(b.dataset.holdingDetail)holdingDetail(b.dataset.holdingDetail);if(b.dataset.click==="new-plan")openPlan();});
    setInterval(pollDashboard,30000);document.addEventListener("visibilitychange",()=>{if(!document.hidden)pollDashboard();});go(location.hash.slice(1));loadAll();
  }
  bind();
})();
