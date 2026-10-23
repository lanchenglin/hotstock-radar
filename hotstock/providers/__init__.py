"""Independent read-only market data providers. No demo fallback or orders."""
from __future__ import annotations

from .common import ProviderError, validate_import_bundle

__all__ = ["ProviderError", "fetch_bundle", "describe_providers", "validate_import_bundle"]


def fetch_bundle(provider: str, options: dict | None = None) -> dict:
    options = options or {}
    if not isinstance(options, dict):
        raise ProviderError("数据源配置必须是对象。")
    if provider == "akshare":
        from .akshare_provider import fetch_akshare
        return fetch_akshare(options)
    if provider == "tushare":
        from .tushare_provider import fetch_tushare
        return fetch_tushare(options)
    if provider == "http":
        from .http_provider import fetch_http
        return fetch_http(options)
    raise ProviderError("不支持的数据源；已实现 akshare、tushare、http，也可导入标准 JSON。采集失败不会切换到模拟数据。")


def describe_providers() -> list[dict]:
    return [
        {"id": "tencent_baostock", "name": "腾讯＋BaoStock 共享只读行情", "account_required": False,
         "install": "复用现有行情服务根地址＋MARKET_MCP_TOKEN；题材在研究中心采集", "implemented": True,
         "capabilities": {"quotes": True, "history": True, "themes": False, "calendar": False},
         "limitations": ["仅访问行情 REST，不共享旧软件业务数据", "题材与日历由内置研究任务补充",
                          "缺少交易单位/涨跌停价时严格入场阻断；不是自动交易", "不自动切换 AmazingData"],
         "docs_url": "docs/DEPLOYMENT_V02.md"},
        {
            "id": "akshare", "name": "AKShare 公开行情与概念", "account_required": False,
            "install": "python -m pip install akshare", "implemented": True,
            "capabilities": {"quotes": True, "themes": True, "history": True, "calendar": True, "evidence": False},
            "limitations": [
                "数据源网络、字段与服务状态会变化，不能保证实时或持续可用。",
                "快照缺少可靠行情时间、当日涨跌停价、交易单位与停牌状态，严格模式不能直接给就绪信号。",
                "概念成分仅当前快照；历史只对配置数量的股票拉取。",
                "可选新闻是未经核实线索；公告原文与公司业务证据尚未接入。",
            ],
            "docs_url": "https://akshare.akfamily.xyz/data/stock/stock.html",
        },
        {
            "id": "tushare", "name": "Tushare REST 盘后数据", "account_required": True,
            "install": "无需额外 Python 包；独立配置 Token、symbols 与接口权限", "implemented": True,
            "capabilities": {"quotes": True, "themes": True, "history": True, "calendar": True, "evidence": False},
            "limitations": [
                "仅已完成日线和交易日历，不是盘中实时行情。",
                "不同接口权限独立；基础信息、同花顺主题接口可能需要更高积分，失败会明确显示。",
                "概念成员仅本次观察快照，不能事后用于历史候选池。",
                "HTTPS 端点需部署时验证；不会自动降级为携带 Token 的 HTTP。",
            ],
            "docs_url": "https://tushare.pro/document/1?doc_id=40",
        },
        {
            "id": "http", "name": "独立标准 HTTP 数据包", "account_required": False,
            "install": "填写返回 MarketBundle v1 JSON 的完整 URL；可选独立 Bearer Token", "implemented": True,
            "capabilities": {"quotes": True, "themes": True, "history": True, "calendar": True, "evidence": True},
            "limitations": [
                "能力取决于外部服务实际提供的数据，本列表表示契约支持，不代表服务已经接通。",
                "必须是本项目标准 JSON；不能直接填写券商网站或普通 AKTools API 地址。",
                "只共享行情数据，不连接其他软件的账户、持仓、策略、数据库或下单接口。",
                "已提供可部署的只读 MiniQMT 桥，尚未实机联调；PTrade 桥未实现。",
            ],
            "docs_url": "docs/DATA_SOURCES.md",
        },
    ]
