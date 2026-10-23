# 热点雷达 Hotstock Radar 0.2.0

独立的 A 股热点研究、追涨计划与持仓风险提醒软件。**不自动交易，不保证收益，不与 GP 或 stock_t_alert 共用账户、持仓、策略、业务数据库。**

本版采用两服务架构：复用已有的 **腾讯＋BaoStock 只读行情服务**；本仓库运行追涨软件，内置题材、涨停观察、消息催化与事件复核。无需另装舆情服务器、Redis、MySQL、GPU 或 MiniQMT。

## 先看这几份文档

| 文档 | 内容 |
|---|---|
| [主要开发大纲](docs/PROJECT_OUTLINE.md) | 产品边界、完整模块、数据流、实现与待办、验收原则 |
| [本版部署与 AI 部署指令](docs/DEPLOYMENT_V02.md) | 两服务接线、Windows/Linux/Docker、端口、鉴权、检查步骤 |
| [账号及接口准备清单](docs/ACCOUNTS_AND_SOURCES.md) | 要部署什么、要哪些地址/Token、不用购买什么 |
| [本版验收记录](docs/TEST_REPORT_V02.md) | 实际测试结果，模拟与真实联调的区别 |
| [数据包契约](docs/CONTRACT.md) | 旧标准 HTTP/JSON 导入格式，仍支持 |

`docs/AI_DEPLOY.md`、`docs/DATA_SOURCES.md`、`docs/VALIDATION.md` 保留 0.1 基线说明；本版默认方案和验收范围以以上 V02 文档为准。旧 MiniQMT 桥是可选兼容路径，不是当前部署要求。

## 快速启动

Python 3.11+，建议创建虚拟环境：

```bash
python -m venv .venv
# Linux: source .venv/bin/activate
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-data.txt
python run.py
```

打开 `http://127.0.0.1:8765`。首次启动为 **DEMO**，所有模拟证券和价格均明确标注。消息及共享行情配置位于 `http://127.0.0.1:8765/research`，主页右下角也有入口。

运行演示与单元测试不需要 AKShare；仅装 `requirements.txt` 即可。真实题材和消息采集需要 `requirements-data.txt`，无需公开来源账号。原生运行不会自动读取 `.env`；环境变量需由 shell、服务管理器或 IDE 注入。

### Docker

```bash
cp .env.example .env
# 编辑 .env：设置自己生成的 HOTSTOCK_API_KEY、已有 MARKET_MCP_TOKEN 和行情根地址。
docker compose up -d --build
```

浏览器仍访问 `127.0.0.1:8765`；本机映射默认不暴露到公网。Docker 容器内的 `127.0.0.1` 不是另一台行情机。复用原 Compose 内部网络见部署文档。

## 操作顺序

1. 在研究中心保存腾讯＋BaoStock 根地址、Bearer Token，并切换真实工作区。Token 可由 `MARKET_MCP_TOKEN` 环境变量提供，界面不回显。
2. 保存研究股票和可选题材名。点击采集题材；查看每个源的状态、成员数、日历、涨停池日期。
3. 点击采集一次行情；检查源端时间与陈旧标记。再按需要打开自动行情刷新及研究定时采集。
4. 点击消息采集。查看首次发现、发布/发生时间、来源、风险词、版本；人工复核时必须留下理由。
5. 回主界面查看候选和计划。人工在券商完成成交后才录入；消息不得代替成交回报。

## 已实现

- 腾讯＋BaoStock HTTP 接入：仅调用 `/ready`、`/api/quote`、`/api/bars`；保留源时间、单位和口径；日线缓存和总时限；不自动切换 AmazingData。
- 内置题材目录/成分、交易日历、指定日期涨停池采集；完整保留题材成员分母，限制报价查询预算。
- 财联社快讯、东方财富个股新闻、巨潮公告目录；独立可终止子进程，任务状态、轮换股票、分源失败记录。
- 消息去重、保守同标题归并、原文版本、首次发现时间、手工导入、人工复核、事件关联；历史查询不使用未来版本。
- 风险词只是待核实线索。待核实风险可暂停相关新增计划和界面入场状态，不改原止损、不改持仓；持仓消息风险可发送通用 Webhook。
- 可选 AI 摘要：显式启用并手动调用；只发当前文章，原文摘录校验失败则拒绝；不控制策略。
- 继承基线的两种研究策略、冻结价格边界、T+1 批次账本、仓位预算、价格风险提醒、JSON 导出与 SQLite 备份。

## 不应误解的边界

**当前腾讯＋BaoStock 契约不提供权威的当日涨跌停价、最小交易单位和停牌状态。严格“可执行入场”会保持阻断，不通过猜测这些字段来制造可买信号。** 报价监控和研究页面可以使用；形成完整交易计划前需要补齐可信证券元数据。仅采集到少量题材成员时也会显示覆盖不足，而不是冒充全板块走强。

公告目前只有目录和链接，未自动读取 PDF 正文；未实现全社交平台采集、自动核实公司产业关系、完整历史收益回测、自动下单。部分新闻没有股票代码，保留为一般线索，不因查询关键词就强行关联个股。题材标签不等于业务证据。

免费公开接口可能失效、限流或漏采。研究工作区无消息不等于无风险。软件不提供可保证成交的止损：T+1、跳空、跌停和通知延迟可能使实际损失超过计划预算。

## 测试

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q
node --check hotstock/static/research.js
```

本版新增逻辑使用离线桩测试，不借用用户供应商凭据；真实行情机、公开接口和 AI 服务需按部署文档实机验收。
