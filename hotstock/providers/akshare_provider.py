"""AKShare public-data adapter. Missing market timestamps remain missing."""
from __future__ import annotations

import importlib
import json
import subprocess
import sys
from datetime import timedelta

from .common import (
    ProviderError, blank_bundle, board_of, bounded_int, date_text,
    frame_records, iso_now, list_option, now_cn, number, symbol_of,
    validate_import_bundle, MAX_RESPONSE_BYTES,
)


def fetch_akshare(options: dict) -> dict:
    """Isolate optional SDK/network calls so a hanging upstream can be stopped."""
    timeout = bounded_int(options, "timeout_seconds", 15, 1, 120)
    total = bounded_int(options, "collection_timeout_seconds", 120, 5, 600)
    safe_options = {k: v for k, v in options.items() if k in {
        "symbols", "theme_names", "history_days", "max_symbols", "max_themes",
        "include_news", "timeout_seconds", "collection_timeout_seconds",
    }}
    safe_options["timeout_seconds"] = timeout
    try:
        process = subprocess.run(
            [sys.executable, "-m", "hotstock.providers.worker"],
            input=json.dumps(safe_options, ensure_ascii=False), text=True,
            capture_output=True, timeout=total, check=False, encoding="utf-8",
        )
    except subprocess.TimeoutExpired:
        raise ProviderError(f"AKShare 本轮采集超过 {total} 秒，已停止采集进程；旧数据不会被标为新行情。") from None
    except (OSError, ValueError):
        raise ProviderError("无法启动 AKShare 采集进程，请检查 Python 环境。") from None
    if len(process.stdout.encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise ProviderError("AKShare 返回的数据包超过 8 MiB。")
    try:
        response = json.loads(process.stdout)
    except (ValueError, TypeError):
        raise ProviderError("AKShare 采集进程未返回有效数据；请检查可选依赖与网络。") from None
    if not isinstance(response, dict) or not response.get("ok"):
        message = response.get("error") if isinstance(response, dict) else None
        raise ProviderError(message if isinstance(message, str) else "AKShare 采集失败。")
    return validate_import_bundle(response.get("bundle"))


def _invoke(ak, function: str, **kwargs) -> list[dict]:
    try:
        target = getattr(ak, function)
        return frame_records(target(**kwargs), function)
    except ProviderError:
        raise
    except AttributeError:
        raise ProviderError(f"当前 AKShare 缺少 {function}，请按项目文档安装兼容版本。") from None
    except Exception as error:
        # The upstream exception may include proxy URLs or query credentials.
        raise ProviderError(f"AKShare {function} 采集失败（{type(error).__name__}），请检查上游可达性。") from None


def _quote(row: dict, received_at: str) -> dict | None:
    price, prev = number(row.get("最新价")), number(row.get("昨收"))
    if price is None or price <= 0:
        return None
    try:
        symbol = symbol_of(row.get("代码"))
    except ProviderError:
        return None
    volume = number(row.get("成交量"))
    return {
        "symbol": symbol, "name": str(row.get("名称") or symbol),
        "price": price, "prev_close": prev,
        "open": number(row.get("今开")), "high": number(row.get("最高")),
        "low": number(row.get("最低")),
        "volume": volume * 100 if volume is not None else None,
        "amount": number(row.get("成交额")),
        "quote_time": None, "received_at": received_at, "time_quality": "unknown",
        "is_suspended": None, "limit_up": None, "limit_down": None,
        "board": board_of(symbol), "min_buy_qty": None, "buy_qty_step": None,
    }


def _bars(rows: list[dict], before_date: str) -> list[dict]:
    result = {}
    for row in rows:
        day = date_text(row.get("日期"))
        if day >= before_date:
            continue
        values = {key: number(row.get(chinese)) for key, chinese in {
            "open": "开盘", "high": "最高", "low": "最低", "close": "收盘",
            "volume": "成交量", "amount": "成交额",
        }.items()}
        if any(values[k] is None or values[k] <= 0 for k in ("open", "high", "low", "close")):
            continue
        if values["volume"] is not None:
            values["volume"] *= 100
        result[day] = {"date": day, **values}
    return [result[day] for day in sorted(result)]


def _ranked_symbols(quotes: dict[str, dict], members: list[str]) -> list[str]:
    def change(symbol):
        quote = quotes.get(symbol)
        if not quote or not quote.get("prev_close"):
            return float("-inf")
        return quote["price"] / quote["prev_close"] - 1
    return sorted((s for s in members if s in quotes), key=change, reverse=True)


def collect_akshare(options: dict, ak_module=None) -> dict:
    """Actual SDK calls; run in the worker in production, injectable in tests."""
    if ak_module is None:
        try:
            ak_module = importlib.import_module("akshare")
        except ImportError:
            raise ProviderError("尚未安装 AKShare 可选依赖；在本软件环境执行 pip install akshare 后再采集。") from None
    history_days = bounded_int(options, "history_days", 60, 25, 1500)
    max_symbols = bounded_int(options, "max_symbols", 12, 1, 100)
    max_themes = bounded_int(options, "max_themes", 3, 1, 10)
    requested_symbols = [symbol_of(s) for s in list_option(options, "symbols", 100)]
    theme_names = list_option(options, "theme_names", 10)
    if len(requested_symbols) > max_symbols:
        raise ProviderError("指定 symbols 数量超过 max_symbols；请增加上限或减少股票。")
    now = now_cn()
    bundle = blank_bundle("akshare/eastmoney+sina", "live")
    bundle["warnings"].extend([
        "AKShare 公开快照不含交易所行情时间，quote_time 保持 null；采集成功不等于行情实时。",
        "概念成员为当前网页快照，不代表历史成员，也不构成公司业务关联的已核实证据。",
        "源接口未提供权威当日涨跌停价、停牌状态和买入单位；这些字段保持未知。",
        "历史价格采用不复权口径；除权、拆并股等造成的跳变须另行核验。",
    ])
    rows = _invoke(ak_module, "stock_zh_a_spot_em")
    received = iso_now()
    quotes = {}
    for row in rows:
        quote = _quote(row, received)
        if quote:
            quotes[quote["symbol"]] = quote
    if not quotes:
        raise ProviderError("AKShare 没有返回有效 A 股价格，本轮采集失败。")
    catalog = _invoke(ak_module, "stock_board_concept_name_em")
    valid_catalog = [row for row in catalog if row.get("板块名称") and row.get("板块代码")]
    if not valid_catalog:
        raise ProviderError("AKShare 概念目录为空；不能构建热门主题观察池。")
    if theme_names:
        selected = [r for r in valid_catalog if str(r["板块名称"]) in theme_names or str(r["板块代码"]) in theme_names]
        matched = {str(r["板块名称"]) for r in selected} | {str(r["板块代码"]) for r in selected}
        missing = [name for name in theme_names if name not in matched]
        if missing:
            raise ProviderError("未找到指定概念：" + "、".join(missing) + "。请使用数据源原始名称或 BK 代码。")
    else:
        selected = sorted(valid_catalog, key=lambda r: number(r.get("涨跌幅")) if number(r.get("涨跌幅")) is not None else float("-inf"), reverse=True)[:max_themes]
        bundle["warnings"].append(f"本轮默认观察概念目录当前涨幅较前的 {len(selected)} 个主题；不是全市场所有热点。")
    theme_rankings = []
    member_union = set(requested_symbols)
    for row in selected:
        code, name = str(row["板块代码"]), str(row["板块名称"])
        try:
            members_raw = _invoke(ak_module, "stock_board_concept_cons_em", symbol=code)
            members = []
            for member in members_raw:
                try:
                    members.append(symbol_of(member.get("代码")))
                except ProviderError:
                    continue
            members = list(dict.fromkeys(members))
            if not members:
                raise ProviderError("概念成分为空。")
        except ProviderError as error:
            bundle["warnings"].append(f"主题 {name} 未采集成功：{error}")
            continue
        known_at = iso_now()
        bundle["themes"].append({
            "id": f"em:{code}", "name": name, "members": members,
            "known_at": known_at, "membership_quality": "current_snapshot", "evidence": [],
        })
        member_union.update(members)
        theme_rankings.append(_ranked_symbols(quotes, members))
    if not bundle["themes"]:
        raise ProviderError("所选概念成分全部采集失败，本轮不生成主题数据。")
    bundle["quotes"] = [q for symbol, q in quotes.items() if symbol in member_union]
    unknown_symbols = [s for s in requested_symbols if s not in quotes]
    if unknown_symbols:
        bundle["warnings"].append("指定股票缺少有效快照：" + "、".join(unknown_symbols))
    if requested_symbols:
        candidates = [s for s in requested_symbols if s in quotes]
    else:
        # Round robin keeps one large theme from consuming all history coverage.
        candidates = []
        for index in range(max((len(x) for x in theme_rankings), default=0)):
            for ranking in theme_rankings:
                if index < len(ranking) and ranking[index] not in candidates:
                    candidates.append(ranking[index])
                if len(candidates) >= max_symbols:
                    break
            if len(candidates) >= max_symbols:
                break
    start = now.date() - timedelta(days=history_days)
    end = now.date() - timedelta(days=1)
    for symbol in candidates:
        try:
            history = _invoke(ak_module, "stock_zh_a_hist", symbol=symbol.split(".")[0], period="daily", start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"), adjust="")
            bars = _bars(history, now.date().isoformat())
            bundle["bars"][symbol] = bars
            if len(bars) < 20:
                bundle["warnings"].append(f"{symbol} 仅有 {len(bars)} 根已完成日 K；历史不足时策略应阻断。")
        except ProviderError as error:
            bundle["warnings"].append(f"{symbol} 历史数据失败：{error}")
    bundle["warnings"].append(f"日 K 拉取范围为最近 {history_days} 个自然日，覆盖 {len(bundle['bars'])}/{len(candidates)} 只指定或按主题轮询选取的股票；其他成分未补历史，不应生成就绪信号。")
    try:
        calendar_rows = _invoke(ak_module, "tool_trade_date_hist_sina")
        calendar = sorted({date_text(r.get("trade_date")) for r in calendar_rows})
        bundle["calendar"] = [day for day in calendar if day >= (now.date() - timedelta(days=max(history_days, 365))).isoformat()]
        bundle["calendar_confirmed"] = bool(calendar and calendar[-1] >= now.date().isoformat())
        if not bundle["calendar_confirmed"]:
            bundle["warnings"].append("新浪交易日历未覆盖当前日期；不能推断后续交易日或可卖日期。")
    except ProviderError as error:
        bundle["warnings"].append(f"交易日历采集失败：{error}")
    if options.get("include_news") is True:
        for symbol in candidates[:3]:
            try:
                news_rows = _invoke(ak_module, "stock_news_em", symbol=symbol.split(".")[0])
                for row in news_rows[:10]:
                    url = row.get("新闻链接")
                    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                        continue
                    # The news interface does not establish an exchange timestamp
                    # or an explicit source timezone. Preserve its source string.
                    bundle["news"].append({
                        "title": str(row.get("新闻标题") or "未命名新闻"), "url": url,
                        "published_at": None, "observed_at": iso_now(), "kind": "news",
                        "summary": str(row.get("新闻内容") or "")[:2000], "symbols": [symbol],
                        "confidence": "unverified", "source_published_at": str(row.get("发布时间") or ""),
                    })
            except ProviderError as error:
                bundle["warnings"].append(f"{symbol} 新闻采集失败：{error}")
        bundle["warnings"].append("新闻仅为网页聚合线索，发布时间时区未确认；published_at 保持 null。未接入公告原文核验。")
    else:
        bundle["warnings"].append("未启用可选个股新闻；公告原文与业务关联证据尚未自动核验。")
    bundle["received_at"] = iso_now()
    bundle["as_of"] = bundle["received_at"]
    bundle["capabilities"] = {
        "quotes": bool(bundle["quotes"]), "themes": bool(bundle["themes"]),
        "history": any(bundle["bars"].values()), "calendar": bool(bundle["calendar"]), "evidence": False,
    }
    return validate_import_bundle(bundle)
