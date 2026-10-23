# 热点雷达：部署手册

适用版本 0.1.0。主程序是独立 Python 服务、中文网页和 SQLite。所有命令在解压后的 `hotstock-radar` 根目录执行；不要在其他交易软件目录里安装或复用它们的数据库。

## 一、部署选择

| 你要做什么 | 必须准备 | 暂时无需准备 |
| --- | --- | --- |
| 先运行完整演示流程 | Windows / Linux / WSL，Python 3.11+，浏览器 | 行情账号、AI Key、Node.js、GPU、Redis、MySQL |
| 采集公开热点和日线 | 主程序环境额外安装 `requirements-data.txt`，允许访问数据网站 | AKShare 账号、单独部署 AKTools |
| 盘后历史研究 | Tushare 注册账号、Token、对应接口权限 | Tushare SDK、券商终端 |
| 用 MiniQMT 做盘中监控 | 可用的券商/迅投 MiniQMT 权限、Windows 客户端、兼容 XtQuant SDK、本项目只读桥 | 在 Linux 主服务安装券商交易模块 |
| 复用已有数据服务 | 服务输出 MarketBundle v1，必要时一个独立只读 Token | 共享其他应用的持仓、账户、策略或数据库 |
| 服务持续运行及手机提醒 | 一台保持运行的机器；需要外部推送时配置 JSON Webhook 接收器 | 大模型账号 |

详细官方来源、权限与当前实测结果见 [DATA_SOURCES.md](DATA_SOURCES.md)。当前交付不要求把这些源全部安装一遍。

## 二、最快启动：本机演示

### Windows

安装 Python 3.11 或以上并加入 PATH。开发环境使用 Python 3.12，Windows 尚需本机验收。双击 `start-windows.cmd`，或者用 PowerShell：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py
```

浏览器打开 <http://127.0.0.1:8765>。按 `Ctrl+C` 停止服务。以后可直接执行最后一行，无需每次重新安装依赖。

### Linux / WSL

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python run.py
```

也可运行 `bash start-linux.sh`。系统如未安装 Python 的 venv 组件，先使用该系统的软件包管理器安装对应组件。首次依赖下载需要联网。

### 首次确认

1. 页面显示“演示”，股票代码为 `DEMO...`，而不是把历史价格当作实时行情。
2. 主线与候选有模拟内容，可打开原因和证据详情，创建冻结计划。
3. 初始持仓与成交为空，模拟资金为 20 万；真实和历史工作区的资金初始未知。
4. 演示时钟固定为 **2026-10-20 10:30，中国时区**。录入演示成交请使用这个时点以前、模拟日历覆盖内的日期；当日新买不能当日卖。
5. 主服务关闭时后台采集与通知也会停止。只关闭网页不会停止仍在运行的服务。

## 三、先接公开数据

在主程序虚拟环境安装可选依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-data.txt
```

```bash
.venv/bin/python -m pip install -r requirements-data.txt
```

在“数据源与设置”选择 AKShare。先使用少量股票或最多 3 个主题，保存后手动采集一次，再考虑启用定时采集。`theme_names` 留空时读取当前概念目录并选择有限的热门主题；具体覆盖规则见数据源说明。

本次开发对 AKShare 1.19.1 做过一次实际采集，公开行情接口返回 HTTPError，没有取得真实报价。因此不要用“安装成功”代替“数据源可用”。部署机器要单独看采集状态、源数据时间及覆盖缺口。

AKShare 当前使用的快照接口缺少可靠源端报价时刻和若干证券交易字段。页面可以显示“参考快照”，严格模式会阻断及时入场与价格止损信号。不要通过把采集时间填成行情时间来解除阻断。

## 四、Tushare 盘后接口

注册并获得本软件独立使用的 Tushare Token，确认 `daily`、`trade_cal` 以及所需主题接口的权限。当前适配器通过 REST 调用，不必安装 Tushare SDK。

可以在设置页填写 Token，或使用环境变量。环境变量优先于页面保存的同类 Token：

```powershell
$env:HOTSTOCK_TUSHARE_TOKEN = '在本机填写自己的Token'
.\.venv\Scripts\python.exe run.py
```

```bash
export HOTSTOCK_TUSHARE_TOKEN='在本机填写自己的Token'
.venv/bin/python run.py
```

设置 Tushare 数据源、少量标准股票代码，保存后刷新。返回工作区应为“历史研究”；本版 Tushare 路径不承担盘中监控。名称/主题权限不足会明确显示缺少能力。

默认端点为 `https://api.tushare.pro`，真实 Token 业务请求尚未在开发环境验证。不要把 HTTPS 失败解释为 Token 必然无效，也不要改用不明第三方代理。关于官方示例 HTTP、显式明文设置和接口权限，请按 DATA_SOURCES.md 处理。

## 五、盘中行情：MiniQMT 只读桥

桥程序、配置示例和完整部署命令见 [bridges/README.md](../bridges/README.md)。常见部署是：Windows 行情机运行 MiniQMT 和只读桥，独立主服务在 Windows 或 Linux 运行，通过 `/bundle` 读取。

桥使用 `xtquant.xtdata` 行情接口，不使用交易模块。它需要券商/迅投实际授予的行情能力与匹配终端版本，不能仅靠安装第三方 Python 包获得权限。

部署步骤概览：

1. 在 Windows 行情机安装并登录可用的 MiniQMT；按券商提供的兼容版本安装 XtQuant。
2. 创建独立 `.venv-qmt`，安装 `requirements-qmt.txt`，复制并编辑 `bridge_config.example.json`。
3. 配置观察股票，核实题材证据和每只证券的买入数量递增规则。示例的未确认字段应继续为未知，不能直接勾成已核实。
4. 设置独立 `HOTSTOCK_QMT_CONFIG`、`HOTSTOCK_QMT_TOKEN`，启动桥的 8819 端口。
5. 主程序选择标准 HTTP 源，URL 填 `/bundle` 完整路径，Token 填桥 Token。
6. 抽查终端价格、行情时点、涨跌停价、交易日历、20 根完整前序日K和交易单位。只有这些检查通过，才能评估盘中数据条件。

同机默认地址是 `http://127.0.0.1:8819/bundle`。跨机器时，`127.0.0.1` 永远指当前机器自身，必须改成行情机地址。Docker 内同样如此：容器中的 `127.0.0.1` 不能指向 Windows 宿主机。用部署环境支持的宿主机地址或受保护的网络地址，并配置可达路由。携带 Token 的远程明文 HTTP 需要显式配置 `allow_insecure_http=true`；使用 HTTPS 或受保护网络时按实际端点填写。

本桥已经通过合成 SDK 和 HTTP 契约检查，未完成真实 Windows MiniQMT 联调。`/health` 仅代表桥进程存在；不能代替 `/bundle` 行情验证。

## 六、访问、定时采集与通知

### 本机与局域网

本机默认监听 `127.0.0.1:8765`。允许局域网访问时先设置本程序访问密钥：

```powershell
$env:HOTSTOCK_API_KEY = [guid]::NewGuid().ToString('N')
.\.venv\Scripts\python.exe run.py --host 0.0.0.0
```

或在 Linux 设置自己生成的长随机密钥后运行：

```bash
export HOTSTOCK_API_KEY='自己的长随机访问密钥'
.venv/bin/python run.py --host 0.0.0.0
```

访问端在网页“访问密钥”入口填写。`HOTSTOCK_API_KEY` 是本程序访问密钥，`HOTSTOCK_SHARED_DATA_TOKEN` 是主程序读 HTTP 数据源的凭据，`HOTSTOCK_QMT_TOKEN` 是桥接受的凭据；它们有不同用途。HTTP 源使用 QMT 桥时，读源 Token 与桥接受的 Token 必须相同。

### 定时采集

设置页保存“自动更新”后，后台才会定期采集。当前最低实际间隔为 AKShare 600 秒、Tushare 1800 秒、标准 HTTP 60 秒；实际取该下限与设置值中的较大值。监控线程约每 5 秒检查已接收的数据，但这不会把采集频率或上游数据频率变成 5 秒。本版属于分钟级观察，不用于秒级打板执行。

HTTP 源做盘中监控时可设 60 秒采集、180 秒行情失效窗口作为部署起点；这些是工程配置示例，需结合上游更新频率与实际延迟核实。公开采集源默认低频研究，不能承担实时退出保证。

### Webhook

本版发送通用 JSON 格式，默认关闭。你需要一个能接收该格式的 HTTP 接收器；企业微信、飞书、Telegram 等原生机器人格式通常需要接收器转换，不能假设任意机器人 URL 都直接兼容。

设置并保存 URL/可选 Bearer Token 后，明确点击“发送测试通知”才测试。真实数据工作区产生新的风险事件且通知已启用时，后台会发出请求。演示工作区不会自动推送风险事件。

接收端 HTTP 成功只说明接收端接受了请求，不证明手机已收到。首次部署请在自己的手机确认完整送达链路。Token 与 URL 不会回显，也不写入普通 JSON 导出；设置页空输入保留已有值。

## 七、持续运行与 Docker

### Linux 持续服务

可使用已有 systemd、supervisor 或其他进程管理器。固定工作目录为项目目录，启动命令使用虚拟环境 Python 的绝对路径和 `run.py` 的绝对路径，并显式指定数据目录：

```text
/实际项目路径/.venv/bin/python /实际项目路径/run.py --host 127.0.0.1 --data-dir /实际持久数据路径
```

进程管理器中单独设置环境变量；不要以多个 Uvicorn worker 运行同一个数据目录，避免重复采集和通知。Windows 可用任务计划程序在登录后启动，MiniQMT 客户端需在对应可用会话持续运行。

### Docker Compose（提供配置，当前未做实际 Docker 构建验收）

安装你环境支持的 Docker 与 Compose，复制配置并编辑：

```bash
cp .env.example .env
docker compose up -d --build
docker compose logs --tail=100 hotstock-radar
```

启动前在 `.env` 将 `HOTSTOCK_API_KEY` 替换成自己的长随机密钥。需要在镜像内安装 AKShare 时设置 `WITH_PUBLIC_DATA=1`。Compose 默认仅发布到宿主机 `127.0.0.1:8765`，数据在持久卷 `radar-data`。

直接 Python 启动不会自动读取 `.env`，应使用操作系统环境变量。停止容器不要使用会删除数据卷的命令；升级前先备份。

## 八、备份、迁移与新历史研究

本机数据库在 `data/hotstock.sqlite3`，可用 `--data-dir` 或 `HOTSTOCK_DATA_DIR` 改路径。程序提供 SQLite 一致性备份：

```bash
.venv/bin/python scripts/backup.py --data-dir data --output-dir backups
```

Windows 将解释器改为 `.venv\Scripts\python.exe`。脚本允许主服务运行时备份，并核查数据库完整性。数据库备份包含配置和账户记录，应按私人数据保管；普通 JSON 导出用于审阅与追溯，不是完整恢复文件。

恢复时先停止服务，保留原数据目录副本，创建新的空数据目录，把备份复制为该目录的 `hotstock.sqlite3`，再用 `--data-dir 新目录` 启动。不要把不同时间的 SQLite 主文件和 WAL 文件混合。

有计划、成交或已记录高点的演示/历史工作区不允许把快照时钟回退，以免未来持仓和高点污染更早研究。研究另一个历史窗口时使用新的空数据目录，例如 `--data-dir data-replay-20261001`。本版是快照研究和事件追溯，还没有完整的收益回测引擎。

## 九、可直接交给部署 AI 的任务说明

> 在当前机器部署解压后的 hotstock-radar 0.1.0，使用独立虚拟环境、独立端口与独立数据目录，不读取其他交易软件的数据库或账号。先安装基础依赖并启动默认演示，核对 /api/health 与 /api/dashboard、演示标识、五页导航及计划和成交流程。保留数据源缺失和错误的原始原因，不用模拟数据替换真实源失败。仅安装本次选择的数据源依赖；需要 Tushare Token、券商权限、兼容终端或私网地址时，明确指出缺什么。不要把安装成功、HTTP 200、示例数据或合成 SDK 测试当作真实行情验收。使用账号时在用户本机配置环境变量或设置页，不把凭据写入聊天、提交源码或部署日志。完成后报告启动方式、数据目录、实际读通的源、源端时点、仍未验证的项目及备份恢复办法。

## 十、常见问题

| 现象 | 处理 |
| --- | --- |
| 页面有数据却全是“数据阻塞” | 检查行情时间、交易参数、题材证据、完整日K与确认日历，不能靠调大阈值掩盖字段缺失。 |
| 切换真实源后没有演示持仓 | 三个工作区隔离；在当前模式单独登记资金和实际成交。 |
| AKShare 返回 HTTPError | 查看当前网络与上游状态、官方接口变更；不要高频循环重试。 |
| Tushare 返回权限错误 | 核对出错接口所需权限，不以一个接口可用推断全部可用。 |
| QMT 桥 /health 正常但 /bundle 失败 | 检查真实终端连接、兼容 SDK、历史下载、股票范围与 Token。 |
| 昨日行情反复读取仍过期 | 这是预期行为；更新采集时间不等于更新源端行情。 |
| 当日买入的记录不能卖 | 软件按逐批次 T+1 校验；核对真实券商可卖记录。 |
| 买入股数显示未知 | 手工资金或持仓估值/题材/交易单位缺失；先补齐事实。 |
| 止损提醒出现但持仓没减少 | 提醒不代表成交；外部完成实际成交后再明确记录。 |
