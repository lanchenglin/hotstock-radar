# 0.2 两服务部署与 AI 部署任务

## 1. 范围

只部署热点雷达；复用已存在的腾讯＋BaoStock行情服务。本次部署不停止、不重建、不修改 stock_t_alert 的 monitor/web/数据库，不复制其 .env 或SDK到公开仓库。服务地址必须由实际环境确认，不猜IP或已映射端口。

前提：行情服务的 `/ready` 返回ready，并能用已有 Bearer 读取 `/api/quote` 和 `/api/bars`。本仓库未在用户行情机上进行真实联调。

## 2. 原生运行

Linux / WSL：

```bash
git clone https://github.com/lanchenglin/hotstock-radar.git
cd hotstock-radar
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-data.txt
export HOTSTOCK_API_KEY='替换成自己生成的长随机值'
export MARKET_DATA_URL='http://实际行情服务主机:实际端口'
# 用安全环境配置方式设置，不把实际Token写进仓库或公开日志
export MARKET_MCP_TOKEN='实际行情服务令牌'
python run.py --host 127.0.0.1 --port 8765
```

Windows PowerShell：

```powershell
git clone https://github.com/lanchenglin/hotstock-radar.git
cd hotstock-radar
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-data.txt
$env:HOTSTOCK_API_KEY='替换成自己生成的长随机值'
$env:MARKET_DATA_URL='http://实际行情服务主机:实际端口'
$env:MARKET_MCP_TOKEN='实际行情服务令牌'
.\.venv\Scripts\python.exe run.py
```

原生 `run.py` 不自动读取 `.env`，必须通过环境变量或网页设置。主界面和研究中心各有访问密钥输入入口。默认DEMO，不会自动访问真实供应商；`/research` 保存行情配置后才切换live。

## 3. Docker

```bash
cp .env.example .env
# 在本机编辑 .env，配置本软件访问Key与行情令牌。不要将 .env 加入Git。
docker compose up -d --build
docker compose logs --tail 80 hotstock-radar
```

默认安装公开研究依赖（WITH_PUBLIC_DATA=1）；默认端口只映射到宿主机127.0.0.1:8765，数据库使用独立radar-data卷。需要局域网访问时修改映射的监听IP，保留HOTSTOCK_API_KEY并设置防火墙；不要直接开放到公网。

Docker里的127.0.0.1指本容器，不是行情容器。接入方式二选一：

- 使用行情服务已发布的宿主机/内网地址，确认可达。
- 加入已有行情服务的Docker网络：设置 `MARKET_NETWORK` 为实际已有网络名，`MARKET_DATA_URL=http://tencent-baostock-mcp:207`，再运行：

```bash
docker compose -f compose.yaml -f compose.shared-network.yaml up -d --build
```

该覆盖文件仅把新软件加入已有网络，不改现有数据服务。`207`是原数据服务内部端口，不代表宿主机也发布207。

内网HTTP携带令牌需在研究中心显式勾选允许。公网使用HTTPS反向代理及访问控制。两个程序可以共用行情服务，但服务的负载余量需实机测试。

## 4. 首次配置顺序

访问 `http://127.0.0.1:8765/research`，输入HOTSTOCK_API_KEY。设置根地址和令牌，保存并切换真实工作区。仅配置令牌环境变量时，网页留空即可，环境值优先。

配置少量明确的沪深股票，例如 `300115.SZ`，或先运行题材采集自动建立候选。点击「采集题材/日历/涨停池」；任务返回202只表示已接受，必须检查任务状态从running结束，逐个看source状态。

点击一次行情采集；确认：source为tencent、quote_time是源端时间、旧价被标陈旧、history的单位和前复权说明没有冲突。不要只看网页有数字就算成功。

点击消息采集；核对公告标题和原文链接、时间、股票代码、任务覆盖股票清单。若公司代码未出现在普通新闻文本中，该文保留为一般线索，不强行关联。

自动行情刷新在主界面的数据设置启用，最低60秒；研究任务独立在研究中心启用，题材最低900秒、新闻最低300秒。不要通过增加任务频率来冲击公开来源。任务互不持有价格风控锁。

## 5. AI与通知（可选）

AI开关默认0。使用兼容Chat Completions接口时配置 `HOTSTOCK_LLM_ENABLED=1`、`HOTSTOCK_LLM_BASE_URL`、`HOTSTOCK_LLM_MODEL` 及可选Key，重启主服务。远程携带Key必须HTTPS；本机环回地址允许HTTP。点击单条事件的摘要按钮才请求模型。模型内容可能有错，摘录校验并不等于事实核验。

通知复用本软件已有通用Webhook机制；不改变原stock_t_alert的通知配置。先点击测试、核对接收端真实送达，再启用。当前没有可靠外部消息队列，不能保证网络故障时送达，网页风险列表仍需查看。

## 6. 验收与回滚

运行 `python -m pytest -q`（先安装requirements-dev）。真实部署还应验证以下情况：正确Token成功、错误Token失败；断开行情网络时旧快照保留但不能当新信号；公开采集失败显示error/partial；陈旧消息源显示覆盖不足；同一新闻重采不增加版本；人工复核有轨迹；新风险版本再次待核实；价格止损与手工账本不被新闻修改。

重要限制：当前共享报价不提供权威涨跌停价、停牌状态和买入单位，严格入场会阻断；只验收研究和风险提醒，不称“已可自动实盘”。行情覆盖抽样不足同样保持阻断。

数据库备份见 `scripts/backup.py --help`，保留数据目录/卷及权限。回滚代码前先备份数据库。关闭研究定时任务不关闭价格监控，不删除既有消息或持仓。禁止用 `docker compose down -v` 删除真实数据卷。

## 7. 可直接交给部署 AI 的指令

> 在独立目录部署 lanchenglin/hotstock-radar 的 main。先读 README、PROJECT_OUTLINE、ACCOUNTS_AND_SOURCES 和本文件。保留现有 stock_t_alert，不读写它的账户、持仓、策略数据库，不启动第二套旧monitor。仅使用用户已有腾讯＋BaoStock行情根地址和Bearer。所有真实密钥只放本机环境/配置，不提交Git、不打印。先跑测试和DEMO，再切live做小股票池契约验收，核对quote_time、陈旧标记、来源、日线口径、题材成员分母及逐源采集状态。不要把任务202当采集成功，不把空消息当没有风险，不关闭严格质量门槛。把真实可用项、失败项、未验证项及回滚/备份位置分别记录。若缺真实地址或Token，仅完成演示安装并明确剩余配置，不编造。默认不开自动下单，本软件也没有下单接口。
