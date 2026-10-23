"""Tushare REST daily/calendar adapter. Authentication and permissions required."""
from __future__ import annotations

from datetime import datetime, time, timedelta
from time import monotonic

from .common import (
    CN_TZ, ProviderError, blank_bundle, board_of, bounded_int, date_text,
    endpoint_url, iso_now, list_option, now_cn, number, symbol_of,
    validate_import_bundle,
)
from .http_provider import request_json


class TushareClient:
    def __init__(self, options: dict):
        token = options.get("token")
        if not isinstance(token, str) or not token.strip() or len(token) > 4096:
            raise ProviderError("Tushare 需要本软件独立配置 Token；请先在 Tushare 官网申请账户并取得相应接口权限。")
        self.token = token.strip()
        self.timeout = bounded_int(options, "timeout_seconds", 15, 1, 120)
        self.deadline = monotonic() + bounded_int(options, "collection_timeout_seconds", 120, 5, 600)
        self.url = endpoint_url(
            options.get("base_url") or "https://api.tushare.pro",
            credentials=True, allow_insecure=options.get("allow_insecure_http") is True,
        )

    def call(self, api_name: str, params: dict, fields: str) -> list[dict]:
        remaining = self.deadline - monotonic()
        if remaining <= 0:
            raise ProviderError("Tushare 本轮总采集时间已用尽；请减少股票数量或调整 collection_timeout_seconds。")
        response = request_json(self.url, timeout=min(self.timeout, remaining), body={
            "api_name": api_name, "token": self.token, "params": params, "fields": fields,
        })
        code = response.get("code")
        if code != 0 or isinstance(code, bool):
            if code == 2002:
                raise ProviderError(f"Tushare {api_name} 权限不足（2002）；请核实该 Token 的接口权限/积分。")
            safe_code = code if isinstance(code, int) else "未知"
            raise ProviderError(f"Tushare {api_name} 请求失败，错误码 {safe_code}；请检查 Token、参数、频率与供应商状态。")
        data = response.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("fields"), list) or not isinstance(data.get("items"), list):
            raise ProviderError(f"Tushare {api_name} 返回格式异常。")
        keys, items = data["fields"], data["items"]
        if any(not isinstance(k, str) for k in keys) or len(set(keys)) != len(keys):
            raise ProviderError(f"Tushare {api_name} 返回重复或无效字段。")
        if any(not isinstance(row, list) or len(row) != len(keys) for row in items):
            raise ProviderError(f"Tushare {api_name} 字段与数据列不匹配。")
        return [dict(zip(keys, row)) for row in items]


def _history_row(row: dict) -> dict | None:
    day = date_text(row.get("trade_date"))
    prices = {k: number(row.get(k)) for k in ("open", "high", "low", "close")}
    if any(value is None or value <= 0 for value in prices.values()):
        return None
    volume, amount = number(row.get("vol")), number(row.get("amount"))
    return {"date": day, **prices, "volume": volume * 100 if volume is not None else None,
            "amount": amount * 1000 if amount is not None else None}


def fetch_tushare(options: dict, client=None) -> dict:
    client = client or TushareClient(options)
    history_days = bounded_int(options, "history_days", 60, 25, 1500)
    max_symbols = bounded_int(options, "max_symbols", 12, 1, 100)
    symbols = [symbol_of(s) for s in list_option(options, "symbols", 100)]
    theme_names = list_option(options, "theme_names", 10)
    if len(symbols) > max_symbols:
        raise ProviderError("指定 symbols 数量超过 max_symbols。")
    if not symbols and not theme_names:
        raise ProviderError("Tushare 首次使用请指定 symbols；如需主题关联，请另填具有权限的 theme_names。")
    now = now_cn()
    bundle = blank_bundle("tushare", "historical")
    bundle["warnings"].extend([
        "Tushare 本适配器使用 daily 盘后日线；没有获取盘中实时行情，mode=historical。",
        "日线 quote_time 的 15:00 是交易日对应的会话结束边界，source_time_precision=session；不是源端逐笔行情时间。",
        "日线为不复权口径；除权、拆并股等价格跳变须另行核验。",
        "本适配器未提供权威当日涨跌停价、停牌状态和买入单位，这些字段保持未知。",
        "公告、新闻、实时分钟线及业务关联核验需要其他权限/数据源，本适配器尚未实现。",
    ])
    start = now.date() - timedelta(days=history_days)
    calendar_rows = client.call("trade_cal", {
        "exchange": "SSE", "start_date": (now.date() - timedelta(days=max(history_days, 365))).strftime("%Y%m%d"),
        "end_date": (now.date() + timedelta(days=45)).strftime("%Y%m%d"),
    }, "exchange,cal_date,is_open")
    # Explicit closure rows establish calendar coverage; do not guess weekends.
    dates_seen = sorted({date_text(row.get("cal_date")) for row in calendar_rows})
    calendar = sorted({date_text(row.get("cal_date")) for row in calendar_rows if str(row.get("is_open")) == "1"})
    if not calendar:
        raise ProviderError("Tushare 未返回可用交易日历，不能推算已完成的行情日期。")
    bundle["calendar"] = calendar
    bundle["calendar_confirmed"] = bool(dates_seen and dates_seen[0] <= now.date().isoformat() <= dates_seen[-1])
    if not bundle["calendar_confirmed"]:
        bundle["warnings"].append("Tushare 日历未覆盖当前日期，可卖日计算应保持未知。")
    # Daily ingestion has a publication window; be conservative before 17:30.
    latest_allowed = now.date() if now.time().replace(tzinfo=None) >= time(17, 30) else now.date() - timedelta(days=1)
    complete_dates = [day for day in calendar if day <= latest_allowed.isoformat()]
    if not complete_dates:
        raise ProviderError("交易日历中没有可用的已完成交易日。")
    last_day = complete_dates[-1]
    name_map = {}
    if theme_names:
        try:
            catalog = client.call("ths_index", {"exchange": "A", "type": "N"}, "ts_code,name")
            selected = [r for r in catalog if r.get("name") in theme_names or r.get("ts_code") in theme_names]
            matched = {r.get("name") for r in selected} | {r.get("ts_code") for r in selected}
            missing = [name for name in theme_names if name not in matched]
            if missing:
                bundle["warnings"].append("Tushare 未找到主题：" + "、".join(missing))
            for theme_row in selected:
                code = str(theme_row["ts_code"])
                try:
                    member_rows = client.call("ths_member", {"ts_code": code}, "ts_code,con_code,con_name,in_date,out_date,is_new")
                    members = []
                    for row in member_rows:
                        # Date fields do not establish when the provider first
                        # published this relationship. Keep current-snapshot semantics.
                        if row.get("is_new") is not None and str(row["is_new"]) != "Y":
                            continue
                        try:
                            symbol = symbol_of(row.get("con_code"))
                        except ProviderError:
                            continue
                        members.append(symbol)
                        name_map[symbol] = str(row.get("con_name") or symbol)
                    members = list(dict.fromkeys(members))
                    if members:
                        bundle["themes"].append({"id": f"ths:{code}", "name": str(theme_row["name"]),
                            "members": members, "known_at": iso_now(), "membership_quality": "current_snapshot", "evidence": []})
                except ProviderError as error:
                    bundle["warnings"].append(f"Tushare 主题成分失败：{error}")
        except ProviderError as error:
            bundle["warnings"].append(f"Tushare 概念源不可用：{error}")
        bundle["warnings"].append("当前概念成分的 known_at 是本次观察时间，不能用于早于此时的历史选股重放。")
    if not symbols:
        symbols = list(dict.fromkeys(s for theme in bundle["themes"] for s in theme["members"]))[:max_symbols]
        bundle["warnings"].append(f"未指定股票，本轮仅拉取主题成分顺序中前 {len(symbols)} 只的日线；该顺序不是推荐排名。")
    if not symbols:
        raise ProviderError("Tushare 没有可拉取的股票；主题权限不可用时请显式指定 symbols。")
    try:
        basic_rows = client.call("stock_basic", {"exchange": "", "list_status": "L"}, "ts_code,name")
        name_map.update({str(r["ts_code"]): str(r["name"]) for r in basic_rows if r.get("ts_code") and r.get("name")})
    except ProviderError as error:
        bundle["warnings"].append(f"股票名称接口不可用，将以代码显示：{error}")
    snapshot_dates = []
    for symbol in symbols:
        try:
            rows = client.call("daily", {"ts_code": symbol, "start_date": start.strftime("%Y%m%d"), "end_date": last_day.replace("-", "")},
                "ts_code,trade_date,open,high,low,close,pre_close,vol,amount")
            by_day = {}
            for row in rows:
                if row.get("ts_code") != symbol:
                    continue
                bar = _history_row(row)
                if bar and bar["date"] <= last_day:
                    by_day[bar["date"]] = (bar, row)
            if not by_day:
                bundle["warnings"].append(f"{symbol} 没有有效日线，可能停牌、权限受限或尚未入库。")
                continue
            latest = max(by_day)
            last_bar, raw = by_day[latest]
            snapshot_dates.append(latest)
            bundle["bars"][symbol] = [by_day[day][0] for day in sorted(by_day) if day < latest]
            quote_time = datetime.combine(datetime.fromisoformat(latest).date(), time(15), CN_TZ).isoformat()
            bundle["quotes"].append({"symbol": symbol, "name": name_map.get(symbol, symbol),
                "price": last_bar["close"], "prev_close": number(raw.get("pre_close")),
                "open": last_bar["open"], "high": last_bar["high"], "low": last_bar["low"],
                "volume": last_bar["volume"], "amount": last_bar["amount"],
                "quote_time": quote_time, "received_at": iso_now(), "time_quality": "provider",
                "source_time_precision": "session",
                "is_suspended": None, "limit_up": None, "limit_down": None, "board": board_of(symbol),
                "min_buy_qty": None, "buy_qty_step": None})
            if latest != last_day:
                bundle["warnings"].append(f"{symbol} 最新日线停留在 {latest}，早于请求交易日 {last_day}；不认为它是当日新行情。")
            if len(bundle["bars"][symbol]) < 20:
                bundle["warnings"].append(f"{symbol} 仅有 {len(bundle['bars'][symbol])} 根前序已完成日 K，历史不足时策略应阻断。")
        except ProviderError as error:
            bundle["warnings"].append(f"{symbol} 日线采集失败：{error}")
    if not bundle["quotes"]:
        raise ProviderError("Tushare 所有股票日线均未成功返回；请检查权限、日期与网络。")
    if not bundle["themes"]:
        bundle["warnings"].append("没有可用主题成分，主题选股信号应阻断；可改用完整标准 JSON/HTTP 数据包。")
    bundle["as_of"] = datetime.combine(datetime.fromisoformat(max(snapshot_dates)).date(), time(15), CN_TZ).isoformat()
    bundle["received_at"] = iso_now()
    bundle["capabilities"] = {"quotes": True, "themes": bool(bundle["themes"]), "history": any(bundle["bars"].values()), "calendar": True, "evidence": False}
    return validate_import_bundle(bundle)
