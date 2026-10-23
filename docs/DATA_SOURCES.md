# 数据源与独立接入说明

核实日期：2026-10-20。接口权限、更新窗口和软件版本仍可能变化，以部署时对应供应商的官方说明和实际返回为准。

本软件的数据库、账户台账、策略参数、交易计划、提醒与日志均独立。允许复用的边界是**只读行情数据服务**。HTTP 适配器不读取 GP 或其他软件的数据库、账户、持仓，也不调用其交易接口。

## 1. 现在能接哪些源

| 数据源 | 安装和账户条件 | 本版已实现 | 适合用途及缺口 |
| --- | --- | --- | --- |
| AKShare | 在本软件 Python 环境安装 `akshare`；所调用的公开接口不要求注册 AKShare 账户或配置 Token | 东方财富 A 股快照、概念目录、概念成分、个股日 K；新浪交易日历；可选个股聚合新闻 | 可用于热点观察和研究。快照接口未提供可靠行情时刻、权威涨跌停价、交易单位与停牌状态；未知字段不补造，因此严格模式会阻断直接就绪信号。 |
| Tushare Pro REST | 注册 Tushare，配置本软件独立 Token；每种接口分别受权限/积分约束；本适配器使用标准库 HTTP，不要求安装 Tushare SDK | `daily`、`trade_cal`；名称使用 `stock_basic`；指定主题时尝试 `ths_index` / `ths_member` | 本版是盘后历史模式，不是盘中实时模式。主题权限失败会显示原因；未接新闻公告、逐笔或分钟实时接口。 |
| 标准 JSON 导入 | 不需要新增账号；数据文件本身必须来自用户有权使用的数据源 | MarketBundle v1 全量数据包导入及结构校验 | 可带行情、日 K、主题时点、日历、新闻证据。文件里缺失的事实继续保持未知。 |
| 标准 HTTP 数据服务 | 已有返回 MarketBundle v1 的服务；可选独立 Bearer Token | GET 读取、大小限制、时点保留、错误报告、禁止自动重定向 | 可对接独立数据中心或共享只读数据源。不能直接填写普通财经网页、AKTools 单接口或券商客户端地址。 |
| BaoStock | 官方基础服务说明仍为无需注册，可通过 pip 安装；2026-09-21 起另有支持 API_KEY 的 VIP 节点 | **本版没有实现适配器** | 适合作为后续历史行情/日历补充。官方日 K、分钟 K 是盘后入库，不能当盘中行情。 |
| 迅投 QMT / XtQuant | 普通 MiniQMT 路径要求先启动相应客户端；权限与服务依赖对应券商/迅投产品；专业行情连接另有不同授权和运行环境 | **已实现只读 MiniQMT 桥，尚未实机联调** | 已提供 bridges/qmt_bridge.py、配置示例、独立 Bearer Token 和部署说明；需 Windows 终端实机验证。主题与买入步长需人工确认。 |
| PTrade | 券商开通平台权限，并使用券商指定终端/策略运行环境；不是在任意服务器 pip 安装即可取得账户行情 | **本版没有实现 PTrade 桥** | 后续按用户真实券商版本开发和验收；本软件不提供自动下单。 |
| 巨潮/交易所公告 | 公开公告阅读与面向机器的数据服务是不同入口；数据服务账户和权限以官方为准 | **本版没有实现公告原文持续采集及业务关系验证** | 标准包可导入带来源的证据；网页新闻不能自动升级为已核实公司公告。 |

`GET /api/providers` 的 capabilities 表示适配器能够处理哪些类别；每次 MarketBundle 内的 capabilities 和 warnings 才表示**这一轮实际拿到了什么**。数据源可调用、传输成功、字段完整、行情足够新鲜、策略达到条件是不同状态。

## 2. AKShare 接入

安装可选依赖：

```bash
python -m pip install akshare
```

数据源选 `akshare`。配置示例：

```json
{
  "symbols": ["300001.SZ", "600001.SH"],
  "theme_names": [],
  "history_days": 90,
  "max_symbols": 12,
  "max_themes": 3,
  "timeout_seconds": 15,
  "collection_timeout_seconds": 120,
  "include_news": false
}
```

这里的代码只演示配置格式，不是股票推荐。`theme_names` 须填东方财富返回的准确概念名称或 BK 代码；不做模糊字符串匹配，避免把同名/近名板块误认为同一主题。

### 本轮采集的具体范围

- `theme_names` 为空时，先读取概念目录，按目录当前涨幅选择前 `max_themes` 个主题。这是有限观察池，不代表已经完整扫描全市场所有机会。
- 对选中主题获取完整成分；返回有关成分的有效报价。成分获取失败会留下警告，全部失败则本轮失败。
- 有 `symbols` 时，只给这些指定股票拉历史；没有时，在各主题当前较强成分间轮询，最多取 `max_symbols` 只。这个选择是拉取覆盖策略，不能称为已验证荐股排名。
- `history_days` 是**自然日数**，不是 K 线根数。节假日、停牌和新上市会减少有效历史，需检查实际根数。
- 日 K 请求截至前一个自然日，并再次过滤当前日期，避免把今天尚未结束的数据用作前序历史。实际非交易日由返回数据决定，不用工作日近似交易日。
- 日 K 使用不复权价格，报价与原始日 K 单位一致；涉及除权等结构跳变时，应另行核验，不能直接把跳变当成突破。
- `include_news=true` 仅对最多前三只拉取近期聚合新闻；不保证穷尽，也不代表公告核实完成。

### 时点与缺失值

`stock_zh_a_spot_em` 输出没有可靠的源端行情时间字段，所以：

```json
{
  "quote_time": null,
  "time_quality": "unknown",
  "limit_up": null,
  "limit_down": null,
  "is_suspended": null,
  "min_buy_qty": null,
  "buy_qty_step": null
}
```

`received_at` 是本软件拿到数据的时间；它不会被填入 `quote_time`。`as_of` 在 AKShare 包中表示这一组观测完成的时点，不证明其中报价是那个时刻产生的。

主题 `known_at` 是这次成功拿到成员关系的时间，`membership_quality=current_snapshot`。这份成员表不能用于早于 `known_at` 的历史回放。业务关联证据为空时，不能把“网页上有这个概念标签”解释成“公告已证实受益”。

新浪日历只使用实际返回日期；未覆盖当前日期时 `calendar_confirmed=false`。未来可卖日不能凭周一至周五推算。

AKShare 在独立短期子进程执行：单次 HTTP 默认 15 秒，本轮默认最多 120 秒。超时会终止子进程。不会改动主 Web 服务的 HTTP 行为，不会失败后自动装入模拟数据。上游异常只显示接口名和异常类型，不显示可能带代理地址/凭据的完整异常内容。

## 3. Tushare 接入

本软件独立保存 Token；不从其他软件拷贝或读取账户配置。

```json
{
  "token": "在设置页单独填写，勿提交到 Git",
  "base_url": "https://api.tushare.pro",
  "symbols": ["300001.SZ"],
  "theme_names": [],
  "history_days": 90,
  "max_symbols": 12,
  "timeout_seconds": 15,
  "collection_timeout_seconds": 120
}
```

### 权限不是一项总开关

2026-10-20 核查的官方文档显示：`trade_cal` 与 `stock_basic` 标注 2000 积分；`ths_index` 与 `ths_member` 标注 6000 积分。基础日线、主题、新闻、分钟数据等分别有说明。账户已经注册或 `daily` 能调用，不代表主题接口有权限。数值可能调整，部署时以官方文档与实际返回为准。[Tushare 日历](https://tushare.pro/document/2?doc_id=26)、[股票基础信息](https://tushare.pro/document/2?doc_id=25)、[主题目录](https://tushare.pro/document/2?doc_id=259)、[主题成分](https://tushare.pro/document/2?doc_id=261)。

本适配器对权限错误（例如 `2002`）作明确报告。可选股票名称或主题请求失败时，可继续返回已成功取得的历史数据，并将缺少的能力标记出来；全部日线失败或无法取得交易日历时整轮失败。

### REST 端点与 Token

官方 REST 说明在核查时仍给出 `http://api.tushare.pro` 示例。本项目默认尝试 HTTPS，并要求部署者验证供应商支持；没有在本开发环境用真实 Token 验证 HTTPS 业务调用。它不会在 HTTPS 失败后静默改为 HTTP。若部署者确认所用端点仅支持 HTTP，可显式设置 `allow_insecure_http=true`；这意味着明文传输，应该由独立服务、受保护的网络或可信代理承担传输保护。供应商 Token 不应发给不明代理。[官方 REST 文档](https://tushare.pro/document/1?doc_id=40)。

### 本版历史口径

- 调用 `trade_cal` 获得开闭市日期；在北京时间 17:30 前保守选择上一已完成交易日，之后尝试当天日线。接口尚未入库时仍显示真实返回的旧日期。
- `daily` 返回的成交量按“手”乘 100 统一为“股”，成交额按“千元”乘 1000 统一为“元”。AKShare 已是元的成交额不重复换算。[官方 daily 字段说明](https://tushare.pro/document/2?doc_id=27)。
- 最新日线转为历史快照；`quote_time` 的 15:00 表示该日会话结束边界，额外标记 `source_time_precision=session`。这不是来源提供的逐笔时刻，更不是当前盘中行情。
- 前序 `bars` 不包含用于历史快照的最后一根日线，防止把待判断样本同时放进突破基线。
- 始终 `mode=historical`。历史观察结果不能被激活成当前可执行状态。
- `ths_member` 官方明确为最新成员，且文档中的纳入/剔除日期标记暂无。本版即使将来收到这些字段也不把它们当成关系首次公开的时间；统一保留当前快照语义。[官方主题成分说明](https://tushare.pro/document/2?doc_id=261)。

## 4. 独立 HTTP 共享数据契约

接口只需满足：

```text
GET https://your-data-service.example/v1/market-bundle
Accept: application/json
Authorization: Bearer <可选独立数据源 Token>
```

响应是 [CONTRACT.md](CONTRACT.md) 中的 **MarketBundle 对象本身**，不额外包在 `{data: ...}` 或 `{result: ...}` 中。包含：

| 字段 | 要求 |
| --- | --- |
| `schema_version` | 整数 1 |
| `source` / `mode` | 真实来源标识；`live`、`historical` 或 `demo`；不得把模拟标记换成真实 |
| `as_of` / `received_at` | 带明确时区的 ISO 8601 字符串；两个时点各有原始语义 |
| `quotes` | 标准股票代码、价格和来源行情时刻；缺失行情时刻用 null + unknown |
| `bars` | 股票代码到已完成日 K 数组；时间有序、不重复、价格单位一致、根数足够 |
| `themes` | 主题 id、成员表、known_at、membership_quality、可溯源 evidence |
| `calendar` / `calendar_confirmed` | 实际交易日序列，以及是否经数据源确认；不得伪造节假日开闭市 |
| `news` | 带 URL、发布时间（允许 null）、首次观察时间、可信度的新闻或公告条目 |
| `capabilities` | quotes/themes/history/calendar/evidence 五类实际覆盖的布尔值 |
| `warnings` | 缺失、失败、覆盖范围、时间精度等问题的文本数组 |

日历、历史和证据可以为空，但缺失能力必须标明；软件将因此限制信号。主题名相同但 id 或来源不同，不应不经核对直接拼接成员。

HTTP `base_url` 是返回数据包的**完整路径**。支持私网服务。没有凭据的私网 HTTP 可直接配置；携带 Token 的远程 HTTP 需要显式 `allow_insecure_http=true`，回环测试地址除外。URL 不允许嵌入用户名/密码、查询参数或片段；Token 用单独字段。所有重定向都被拒绝，因此不会把授权头跟随到其他地址。

传输成功后保留原包的 `source/as_of/received_at/quote_time`，只增加本次 HTTP 读取时间说明。不能通过不停获取同一个缓存文件，把昨天的价格洗成今天的行情。

### QMT 桥与 PTrade 的接入边界

如果以后由一台装有券商终端的机器采集数据，桥应作为独立服务输出上述契约。本版已经提供只读 MiniQMT 桥实现，见 [bridges/README.md](../bridges/README.md)。已完成合成 SDK 契约测试，**尚未在真实券商终端运行或验收**。PTrade 桥尚未实现。

QMT 普通 MiniQMT 路径，官方说明要求先启动 MiniQMT 客户端，再由 XtQuant 请求行情；可用于读取的内容包括 K 线、合约资料、板块分类等。不要把这一要求扩展成“所有迅投行情产品都只能运行 Windows”：专业行情连接和授权方式另有差异，需要按产品核对。[XtQuant 快速开始](https://dict.thinktrader.net/nativeApi/start_now.html)、[XtData 行情模块](https://dict.thinktrader.net/nativeApi/xtdata.html)。

PTrade 是券商权限平台。以湘财官方说明为例，需要签署使用协议并由券商确认符合开通要求。不能声称安装一个第三方 Python 包即可使用任意券商 PTrade。[湘财 PTrade 官方说明](https://www.xcsc.com/main/privateplacementservice/gsssystem/ptrade/index.shtml)。

只读桥只需行情模块。自动下单、撤单、券商会话登录、持仓同步均不在本次实现范围。

## 5. 为什么暂未直接采用 BaoStock 和公告抓取作主源

BaoStock 官方介绍仍提供无需注册的基础接入，同时 2026-09-21 的 v0.9.4 增加 API_KEY/VIP 节点。其官方更新时间说明为日 K 约 17:30、分钟 K 约 20:00 入库，因此它可以补历史研究，不能满足盘中追涨时对新鲜报价的要求。本版没有实现 BaoStock 适配器，也不会在 AKShare 失败时悄悄切过去。[BaoStock 官方平台说明](https://www.baostock.com/mainContent)。

巨潮公开说明其为深交所信息披露官方网站，并提供独立的数据服务入口。公告原文可作为关系判断的证据，但“公开网页可阅读”不等于本软件已经获得或完成连续批量 API 接入。后续应保存原文 URL、公告日期、首次观察时间、关联证券代码和核验结果，遇到撤回/澄清还需修订证据状态。[巨潮官网](https://www.cninfo.com.cn/)、[深证信数据服务](https://webapi.cninfo.com.cn/)、[深交所规则入口](https://www.szse.cn/lawrules/)。

## 6. 质量验收与当前测试边界

已实现并通过的自动检查包括：行情时间不被采集时间替代；模拟标记不能装入真实包；NaN/Infinity、重复行情、重复 K 线、未来 K 线和无时区时间被拒绝；量/金额单位转换；当日日 K 过滤；概念快照时间保留；短历史发出明确问题；上游错误去敏；采集超时；使用真实回环 HTTP 服务验证标准 JSON 读取、登录 HTML 拒绝及携带授权头时拒绝重定向。

这些测试验证本软件行为，不证明外部供应商在用户网络长期可达，也不证明策略收益。本次开发用 AKShare 1.19.1 实际做过一次有界采集：stock_zh_a_spot_em 约 8.26 秒返回 HTTPError，未取得行情且未回退模拟；没有使用真实 Tushare Token 调用。真实 AKShare/Tushare 连通性仍应在部署机器上单独验收，失败要保留上一份数据的原始时点并显示错误；绝不以演示行情覆盖失败。

官方 AKShare 同时说明其公开数据接口以学术研究为用途，并需要持续维护上游网站变更。部署与后续商用应遵守各数据提供方条件；开源 SDK 许可本身不等于行情数据再分发授权。[AKShare 项目说明](https://akshare.akfamily.xyz/introduction.html)。
