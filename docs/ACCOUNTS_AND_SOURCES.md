# 账号、接口、部署清单（0.2）

## 必需的两个服务

| 服务 | 准备 | 备注 |
|---|---|---|
| 已有腾讯＋BaoStock数据服务 | 可达根地址、MARKET_MCP_TOKEN | 已经运行就复用；不要同时新开旧系统 monitor |
| 本仓库追涨软件 | Python环境或Docker；自行生成HOTSTOCK_API_KEY | 独立数据库；题材和消息采集在软件内 |

腾讯和BaoStock账号不是本适配器的配置项。本软件访问的是你自己部署的只读HTTP服务，令牌由你自己管理；无权把免费公开接口当作供应商承诺的稳定行情。

## 地址与密钥必须区分

- `MARKET_DATA_URL`：如 `http://行情主机:实际映射端口`，只填根地址，不填 `/mcp/`。
- `MARKET_MCP_TOKEN`：现有行情服务的 Bearer；环境变量优先于网页保存值。
- `HOTSTOCK_API_KEY`：访问追涨软件网页API的密钥；不是行情令牌。
- AKShare：安装 `requirements-data.txt`，当前公开采集路径不填登录账号或API Key；仍需遵守来源使用条件。
- AI可选：`HOTSTOCK_LLM_ENABLED=1`、`HOTSTOCK_LLM_BASE_URL`（兼容Chat Completions的根路径，一般含/v1）、`HOTSTOCK_LLM_API_KEY`、`HOTSTOCK_LLM_MODEL`。不启用则不用准备。
- 手机推送可选：主界面配置通用JSON Webhook接收器；企业微信/钉钉原生格式不保证直接兼容。

不用预先开通：AmazingData、MiniQMT、Tushare、社交平台Cookie。不用另外部署：AKTools、舆情服务器、Redis、MySQL、GPU推理。

## 本版公共研究接口

| 内容 | AKShare接口 | 限制 |
|---|---|---|
| 概念目录/成分 | stock_board_concept_name_em / stock_board_concept_cons_em | 当前快照，不是历史成员库或已核实业务关系 |
| 涨停池 | stock_zt_pool_em | 近期指定日期；不保证完整历史 |
| 日历 | tool_trade_date_hist_sina | 必须检查覆盖当前日期，不能自行按周一至周五补齐 |
| 财联社快讯 | stock_info_global_cls | 近期窗口，官方文档描述最近20条，轮询仍可能漏采 |
| 个股新闻 | stock_news_em | 有限返回窗口，查询关键词不等于真实关联 |
| 公告目录 | stock_zh_a_disclosure_report_cninfo | 仅标题、时间、链接，PDF正文未接入 |

## 核验来源

2026-10-23 核对官方文档；接口可用性需本机验证，不能以文档存在代替实时请求成功。

- AKShare股票接口文档：https://akshare.akfamily.xyz/data/stock/stock.html
- 项目使用与风险说明：https://akshare.akfamily.xyz/introduction.html
- 现有共享服务契约：私有 `lanchenglin/stock_t_alert` 的 `providers/README.md`、`providers/common/market_data_common/models.py`、`service.py`。本仓库未复制其私有凭据或SDK。

不把全部第三方新闻正文公开再分发。私有研究仍应遵守来源授权与访问频率；更高覆盖、时效或商用分发需求须另行取得授权。
