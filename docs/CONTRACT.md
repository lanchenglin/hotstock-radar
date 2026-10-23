# Internal v1 integration contract

All engine/provider inputs and outputs are JSON-serializable dictionaries. Pydantic validation occurs at the HTTP boundary. `schema_version` is `1`. Times use ISO 8601 with explicit offsets. Trading timezone is Asia/Shanghai. No automatic trading.

## MarketBundle

```json
{
  "schema_version": 1,
  "source": "demo",
  "mode": "demo",
  "as_of": "2026-10-20T10:30:00+08:00",
  "received_at": "2026-10-20T10:30:00+08:00",
  "quotes": [{"symbol":"300001.SZ","name":"示例股","price":20.0,"prev_close":19.0,"open":19.1,"high":20.2,"low":19.0,"volume":1000000,"amount":20000000,"quote_time":"2026-10-20T10:30:00+08:00","received_at":"2026-10-20T10:30:00+08:00","time_quality":"simulated","is_suspended":false,"limit_up":22.8,"limit_down":15.2,"board":"CHINEXT","min_buy_qty":100,"buy_qty_step":100}],
  "bars": {"300001.SZ":[{"date":"2026-10-19","open":18.9,"high":19.2,"low":18.8,"close":19.0,"volume":1000000,"amount":19000000}]},
  "themes": [{"id":"robotics","name":"机器人","members":["300001.SZ"],"known_at":"2026-10-19T18:00:00+08:00","membership_quality":"simulated","evidence":[]}],
  "calendar":["2026-10-19","2026-10-20","2026-10-21"],
  "calendar_confirmed":false,
  "news":[],
  "capabilities":{"quotes":true,"themes":true,"history":true,"calendar":true,"evidence":false},
  "warnings":[]
}
```

mode = demo | live | historical. time_quality = exchange | provider | unknown | simulated. Theme membership_quality = point_in_time | current_snapshot | unknown | simulated. `quote_time` and `known_at` may be null. Evidence entries contain title, url, published_at (nullable), observed_at, kind, summary, symbols, confidence (verified|unverified|simulated). Missing market timestamp MUST NOT be replaced by fetch time. Unknown exchange upper/lower limits must remain null. Historical bars must not contain today's unfinished session for signal calculation. Current snapshot membership is only valid after known_at, never for earlier historical replay. Demo invented symbols use names explicitly prefixed 模拟. Demo calendar is explicitly simulated.

## Provider interface (agent providers owns)

`hotstock.providers.fetch_bundle(provider: str, options: dict) -> dict`

Supported providers akshare, tushare, http. Raise ProviderError with sanitized human-readable text; no fallback to demo. Optional modules lazy-imported. Options: token, base_url, timeout_seconds (default 15), symbols (list), history_days (default 60), max_symbols (default 12), theme_names (list). HTTP accepts exactly the MarketBundle JSON with schema_version=1. `describe_providers() -> list[dict]` returns id,name,account_required,install,capabilities,limitations,docs_url. `validate_import_bundle(data:dict)->dict` validates structural contract; root also boundary-validates. Provider errors, latency and missing capabilities explicit.

## Engine (agent engine owns)

`analyze_market(bundle: dict, settings: dict, now: str) -> dict`

Returns {themes:[...], candidates:[...], quality_issues:[...], summary:{...}}. Themes: id,name,member_count,up_ratio,avg_change_pct,heat_score,status,reasons. Candidates: id,symbol,name,theme_id,theme_name,strategy (`expansion`|`pullback`),status (`ready`|`watch`|`blocked`|`expired`),can_execute (false for all demo; live informational readiness only, no order),entry_min,entry_max,stop_price,reasons:[str],blockers:[str],created_at,expires_at,mode,source,quote_time. Return data quality issues as strings. No fake win probabilities. Multiple strategies per symbol are allowed.

`size_position(plan: dict, account: dict, settings: dict, security: dict, holdings: list | None = None) -> dict`

Returns quantity (int|null),notional,planned_loss,risk_budget,reason,warnings. account = {equity:float|null,cash:float|null,updated_at:str|null}; security uses min_buy_qty,buy_qty_step,price. Holdings [{symbol,theme_id,quantity,market_value}]. Cash/equity missing => quantity null. Budget = equity*risk_per_trade_pct/100. Allow fees/slippage reserve; cap by cash, max_position_pct and max_theme_pct.

`sellable_quantity(lots: list, now: str, calendar: list, calendar_confirmed: bool = True) -> int | None`

Lots [{quantity:int,bought_at:str,remaining:int(optional),sellable_confirmed:int|null}]. Never assume new shares sellable on trade date. Unknown calendar => None unless explicit individually confirmed quantity is provided and dated safely.

`evaluate_position(position: dict, plan: dict | None, quote: dict | None, theme: dict | None, settings: dict, now: str, calendar: list, calendar_confirmed: bool = True) -> list[dict]`

position={symbol,name,quantity,avg_cost,lots:[...],highest_price,opened_at,theme_id}. Alerts contain code,level(info|warning|critical),message,triggered_at,sellable_quantity,status (triggered|cannot_sell|review). Stale price can create DATA_STALE alert but not a fresh price-triggered stop. No 'sold' unless actual recorded trade. Hard stop, trailing protection, theme weakening, max holding sessions, unknown availability addressed.

Settings keys: risk_per_trade_pct=0.5,max_position_pct=10,max_theme_pct=25,slippage_pct=0.3,fee_pct=0.05,max_quote_age_seconds=180,min_history_bars=20,min_theme_members=2,min_theme_up_ratio=0.6,min_theme_change_pct=1.0,entry_buffer_pct=0.3,max_chase_pct=1.5,max_stop_distance_pct=8.0,trailing_stop_pct=6.0,max_holding_sessions=5,strict_data_quality=true.

## HTTP API (root owns)

All `/api/*` except health require `X-API-Key` if HOTSTOCK_API_KEY configured. UI stores key in sessionStorage after user entry, no secret embedded in HTML.

- GET /api/health -> {status,version,service,mode}
- GET /api/dashboard -> {mode,source,as_of,received_at,analysis:{themes,candidates,quality_issues,summary},account:{equity,cash,updated_at},positions:[...],plans:[...],alerts:[...],data_status:{...},scheduler:{...}}
- GET /api/config -> {provider,mode,refresh_seconds,auto_refresh,risk:{...},account:{equity,cash,updated_at},provider_options:{...redacted...}}
- PUT /api/config accepts partial top-level config or account/risk merge. Never returns secrets. Account settings scoped by mode.
- GET /api/providers -> {providers:[...],status:{...}}
- POST /api/refresh {provider?:"demo"|"akshare"|"tushare"|"http"} -> {ok,mode,source,as_of,warnings,summary}; errors HTTP 422/502. Busy -> 409. This is read-only collection.
- POST /api/import accepts MarketBundle JSON -> same refresh response. Max body 8MiB. Cannot import executable instructions.
- GET /api/plans -> {items:[...]}
- POST /api/plans {candidate_id:string} -> plan saved with id. Plan prices frozen; duplicate candidate refuses/returns existing. Demo plan stays demo. Blocked candidates cannot be activated; watch allowed as watch plan.
- PATCH /api/plans/{id} {status:"cancelled"} -> updated plan (only cancellation supported)
- POST /api/trades {symbol,name,side:"buy"|"sell",quantity,price,executed_at,plan_id?:string,theme_id?:string,confirmed:true,notes?:string} -> {trade,positions,account}; manually record externally executed trade, never place orders. FIFO cost and remaining quantity. No oversell, future timestamp, T+1-invalid simulated sale. Modes isolated.
- GET /api/events?limit=100 -> {items:[{id,kind,title,detail,created_at,mode}]}
- GET /api/export -> JSON snapshot/config without tokens, plans,trades,events for current mode

UI global mode always visible. No data after collection failure may be labeled fresh. Data cards show exact last valid as_of and error. Backend serves / and /static/*.
