# MiniQMT 只读数据桥

这是可以部署的桥程序和接口实现；开发环境没有用户券商终端，**尚未完成 Windows MiniQMT 实机联调**。已用合成 SDK 数据验证字段转换、未知值处理、历史过滤、主题时点、Bearer 认证及只读 HTTP 路由。

桥只在安装有兼容 XtQuant SDK、已启动并登录 MiniQMT 的机器上运行。本版按 Windows 常见券商客户端路线部署。主软件可留在 Linux、WSL 或另一台 Windows，通过 HTTP 读取 `/bundle`。

## 1. 安装

在项目根目录用 PowerShell：

```powershell
py -3.11 -m venv .venv-qmt
.\.venv-qmt\Scripts\python.exe -m pip install -r requirements-qmt.txt
Copy-Item bridges\bridge_config.example.json bridges\bridge_config.json
```

按券商或迅投提供的版本安装匹配的 XtQuant SDK 到 `.venv-qmt`，不要盲目替换已适配终端的 SDK。版本及原生接口说明：[官方 SDK 下载](https://dict.thinktrader.net/nativeApi/download_xtquant.html)、[快速开始](https://dict.thinktrader.net/nativeApi/start_now.html)。

启动 MiniQMT 客户端并确认行情已连接，再验证行情模块可以导入：

```powershell
.\.venv-qmt\Scripts\python.exe -c "from xtquant import xtdata; print('xtdata import OK')"
```

导入成功只证明 SDK 已安装，不证明行情已连接。`/health` 同样只报告桥进程状态；只有成功取得 `/bundle` 并核查源端时间后才算行情读通。

## 2. 配置观察范围和证据

编辑 `bridges/bridge_config.json`：

- `symbols`：本轮要读取的沪深 A 股代码，最多 100 只；本版没有验收北交所。
- `history_days`：自然日范围，默认 120；至少需要 20 根有效已完成日 K。`download_history=true` 时首次请求会补历史，同一桥进程同一日已准备过的股票不会重复全量下载。
- `themes`：人工确认的观察主题和成员。示例中的股票只是配置格式示例，**没有核实题材归属**。`confirmed=false` 或没有已核实来源的主题，输出 `membership_quality=unknown`，严格模式会阻断。
- `security_rules`：为每只股票配置经券商/交易所规则确认的买入步长和来源。程序不把合约乘数 `VolumeMultiple` 当成买入步长。

主题确认后的结构示意：

```json
{
  "id": "my-verified-theme",
  "name": "自己核实的主题",
  "members": ["300001.SZ"],
  "confirmed": true,
  "evidence": [
    {
      "title": "自己实际核实的公告或业务来源标题",
      "url": "https://www.cninfo.com.cn/实际来源地址",
      "published_at": null,
      "kind": "filing",
      "summary": "明确写出该来源支持什么业务关系",
      "symbols": ["300001.SZ"],
      "verified": true
    }
  ]
}
```

上例中来源地址和标题是格式占位，必须换成实际来源；不能直接把占位数据设为真实证据。`published_at` 不清楚时保持 null。证据 `symbols` 必须明确列出它实际覆盖的股票；只有主题所有成员都有已核实证据覆盖，桥才标记已确认关系，不能把一家公司的公告扩展为整个主题的证据。

桥会在首次读到主题配置时写入相邻文件 `bridge_config.json.observed.json`。每次内容变化产生新的首次观察时间；配置里手填的 `known_at` 被忽略。它只表达“从何时开始采用这份人工确认的成员关系”，不能用于更早的历史回放。保留观察记录文件，不能为了回测效果修改其时点。

交易单位配置项如下，其中数值必须自行核实，示例不提供通用猜测：

```json
{
  "confirmed": false,
  "min_buy_qty": null,
  "buy_qty_step": null,
  "source_url": null
}
```

最小限价买入量优先读取官方 `MinLimitOrderVolume`；缺少该字段才使用已确认配置的 `min_buy_qty`。步长只读取已确认配置。字段缺少时继续为 null，主软件不应给出猜测仓位。

## 3. 启动桥

生成独立 Token，并设为当前 PowerShell 环境变量：

```powershell
$env:HOTSTOCK_QMT_CONFIG = (Resolve-Path bridges\bridge_config.json).Path
$env:HOTSTOCK_QMT_TOKEN = [guid]::NewGuid().ToString('N')
.\.venv-qmt\Scripts\python.exe -m uvicorn bridges.qmt_bridge:create_app --factory --host 127.0.0.1 --port 8819
```

Token 保存在自己的配置/密码管理工具中，不提交到仓库。Windows 服务或计划任务需独立配置这两个环境变量。以上 Token 自动生成于当前 PowerShell 会话；如需在主软件填写，可在自己机器上查看该变量。

主软件同机时，设置 HTTP 数据源地址 `http://127.0.0.1:8819/bundle`，token 填相同的独立值。

跨机时把 `--host` 改为 `0.0.0.0` 并限制 Windows 防火墙只允许主软件机器访问。通过 HTTPS 反向代理或受保护网络连接；若明确使用可信私网明文 HTTP，主软件需显式设置 `allow_insecure_http=true`，系统不会自行降级。

主软件数据源配置示例：

```json
{
  "provider": "http",
  "provider_options": {
    "base_url": "http://127.0.0.1:8819/bundle",
    "token": "填写独立桥 Token",
    "timeout_seconds": 60
  }
}
```

首次补历史可能超过客户端等待时间。桥正在准备时后续请求会提示繁忙；查看 MiniQMT 下载状态，准备完成后重试。没有数据时返回 502，未授权返回 401；不会返回演示数据充当成功。

## 4. 官方字段如何转换

| 标准字段 | 读取内容 | 处理 |
| --- | --- | --- |
| `quote_time` | `get_full_tick(...)[symbol].time` | 按官方示例中的 Unix 毫秒转换为 `+08:00`；缺失/单位不对时为 null |
| `price/prev_close` | `lastPrice/lastClose` | 不拿接收时间制造新价格 |
| `volume/amount` | `volume/amount` | 沪深股票 volume 由手转股乘 100；金额保留元 |
| `limit_up/limit_down` | `get_instrument_detail(..., iscomplete=True)` 的 `UpStopPrice/DownStopPrice` | 必须报价日期为当天且前收与合约资料一致；不符合则 null |
| `min_buy_qty` | `MinLimitOrderVolume` | 正整数时采用；缺失时可使用已确认配置 |
| `buy_qty_step` | 本桥明确确认的规则配置 | 不从 `VolumeMultiple` 推断 |
| `is_suspended` | 当日日 K 的 `suspendFlag`；或 `IsTrading=true` | 当日日 K 为 1 才指为停牌；可交易真值表明未停牌，其余情况不猜状态 |
| `bars` | `get_market_data_ex`，`period=1d`，`dividend_type=none`，`fill_data=false` | 过滤当前日、非返回交易日、停牌填充记录和异常价格；不足 20 根提示阻断 |
| `calendar` | `get_trading_dates` | 只保留实际返回的历史至当日日期，不生成未来工作日；沪深返回不一致则失败 |

官方依据：[XtData 原生接口与字段](https://dict.thinktrader.net/nativeApi/xtdata.html)、[股票数据原生 Python 示例](https://dict.thinktrader.net/dictionary/stock.html)、[SDK 更新记录与成交量单位说明](https://dict.thinktrader.net/nativeApi/download_xtquant.html)。

本桥既不做订单处理，也不加载交易模块；只暴露 `GET /health` 和带 Bearer 认证的 `GET /bundle`。价格过时、无确认主题、缺少交易单位、日历异常或历史不足时，主软件应继续阻断对应信号。即使所有数据通过，也只是满足软件数据条件，不代表可以保证涨幅或止损成交。

## 5. 上机验收

1. 日历与终端实际交易日期对应；没有自动填未来工作日。
2. 盘中看 `quote_time` 是否持续前进；断开终端后不能仍被判断为新行情。
3. 抽查价格、前收、涨跌停价、成交额、成交量与终端一致。
4. 抽查最小买入量和配置步长，不能把股票代码前缀作为唯一依据。
5. 检查每只股票至少 20 根已完成日 K，并核验除权等结构变化。
6. 初始未确认主题应被阻断；人工核查后，首次观察时间需持久保存。
7. 将主软件切到该 HTTP 源，验证 401、终端离线、缺失历史和过期行情均会保留错误说明。

这些是部署验收步骤，目前只有合成 SDK 与 HTTP 契约测试完成；券商账户、终端版本、权限和网络尚需用户机器实测。
