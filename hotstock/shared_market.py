"""Read-only adapter for stock_t_alert's Tencent + BaoStock REST contract.

Only /ready, /api/quote and /api/bars are accessed. No old application's
plans, accounts, credentials, control API or private SDK is imported.
"""
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import time
from urllib.parse import urlencode, urlsplit

from .providers.common import ProviderError, blank_bundle, board_of, endpoint_url, symbol_of, validate_import_bundle
from .providers.http_provider import request_json
from .timeutil import SHANGHAI, parse_time


class SharedMarket:
    def __init__(self):
        self.history_cache = {}

    def fetch(self, options, metadata, now, priority=(), request_fn=None, clock_fn=None):
        get = request_fn or request_json
        clock = clock_fn or (lambda: datetime.now(SHANGHAI))
        token = options.get("token", "")
        if not isinstance(token, str) or not token or len(token) > 4096 or any(c in token for c in "\r\n"):
            raise ProviderError("腾讯＋BaoStock 服务需要独立的 Bearer Token。")
        base = endpoint_url(options.get("base_url"), credentials=True,
                            allow_insecure=options.get("allow_insecure_http") is True).rstrip("/")
        if urlsplit(base).path not in {"", "/"}:
            raise ProviderError("请填行情服务根地址，不要填 /mcp/ 或 /api/quote。")
        timeout = int(options.get("timeout_seconds", 10))
        deadline = time.monotonic() + int(options.get("collection_timeout_seconds", 120))
        headers = {"Authorization": "Bearer " + token}
        warnings = []

        def call(path, params=None):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProviderError("本轮行情预算用尽；未采集的证券保持缺失。")
            return get(base + path + ("?" + urlencode(params) if params else ""),
                       timeout=max(0.1, min(timeout, remaining)), headers=headers)

        if call("/ready").get("status") != "ready":
            raise ProviderError("共享行情服务未就绪，未切换任何备用源。")
        ordered = list(priority) + options.get("symbols", []) + metadata.get("ranked_symbols", [])
        if not ordered:
            ordered = [s for t in metadata.get("themes", []) for s in t.get("members", [])]
        symbols = list(dict.fromkeys(symbol_of(s) for s in ordered))
        cap = min(100, int(options.get("max_symbols", 30)))
        selected = symbols[:cap]
        if not selected:
            raise ProviderError("先采集题材，或配置需要监控的股票代码。")
        if len(symbols) > cap:
            warnings.append(f"本轮仅采集 {cap}/{len(symbols)} 只候选；未缩小题材成员分母。")
        bundle = blank_bundle("stock_t_alert/tencent+baostock", "live", now.isoformat())
        bundle["received_at"] = now.isoformat()
        bundle["themes"] = deepcopy(metadata.get("themes", []))
        for theme in bundle["themes"]:
            if not theme.get("known_at") or (now - parse_time(theme["known_at"])).total_seconds() > 86400:
                theme["membership_quality"] = "unknown"
        bundle["calendar"] = list(metadata.get("calendar", []))
        bundle["calendar_confirmed"] = bool(metadata.get("calendar_confirmed") and bundle["calendar"])
        if not metadata.get("observed_at") or (now - parse_time(metadata["observed_at"])).total_seconds() > 86400:
            warnings.append("题材数据缺失或超过24小时；不能据此确认当前板块。")
            for theme in bundle["themes"]:
                theme["membership_quality"] = "unknown"
        history_limit = min(12, cap)
        for symbol in selected:
            try:
                raw = call("/api/quote", {"symbol": symbol})
                if raw.get("symbol") != symbol or raw.get("source") != "tencent":
                    raise ProviderError("报价证券或来源不符合腾讯通道契约，已拒绝。")
                stamp = parse_time(raw["datetime"])
                received = clock()
                if stamp > received:
                    raise ProviderError("行情时间在未来，已拒绝。")
                price, prev = float(raw["last"]), float(raw["previous_close"])
                q = {"symbol": symbol, "name": str(raw.get("name") or symbol), "price": price,
                     "prev_close": prev, "quote_time": stamp.isoformat(), "received_at": received.isoformat(),
                     "time_quality": "unknown" if raw.get("stale") is not False else "provider",
                     "board": board_of(symbol), "is_suspended": None, "limit_up": None,
                     "limit_down": None, "min_buy_qty": None, "buy_qty_step": None,
                     "upstream_stale": raw.get("stale"), "source": "tencent"}
                for field in ("open", "high", "low", "volume", "amount"):
                    q[field] = raw.get(field)
                bundle["quotes"].append(q)
                if raw.get("stale") is not False:
                    warnings.append(f"{symbol} 上游报价陈旧或未声明新鲜度。")
            except (ProviderError, KeyError, TypeError, ValueError):
                warnings.append(f"{symbol} 报价不可用或契约不匹配，未用旧价冒充。")
                continue
            if len(bundle["bars"]) >= history_limit:
                continue
            key = (hashlib.sha256((base + token).encode()).hexdigest(), symbol, now.date().isoformat(), options.get("history_days", 90))
            cached = self.history_cache.get(key)
            try:
                if cached and time.monotonic() - cached[0] < 1800:
                    bars = deepcopy(cached[1])
                else:
                    raw_bars = call("/api/bars", {"symbol": symbol, "interval": "1d",
                        "start": (now.date() - timedelta(days=int(options.get("history_days", 90)))).isoformat(),
                        "end": (now.date() - timedelta(days=1)).isoformat(), "limit": 500})
                    meta = raw_bars.get("meta", {})
                    if (meta.get("interval") != "1d" or meta.get("volume_unit") != "share"
                            or meta.get("amount_unit") != "CNY" or meta.get("price_adjustment") != "qfq"
                            or meta.get("stale") is not False
                            or not set(meta.get("sources", [])) <= {"tencent", "baostock"}
                            or not meta.get("sources")):
                        raise ProviderError("日线来源、单位、新鲜度或复权口径不符合契约。")
                    bars = []
                    for row in raw_bars["bars"]:
                        date_ = parse_time(row["datetime"]).astimezone(SHANGHAI).date()
                        if date_ >= now.date():
                            raise ProviderError("拒绝当日未收盘或未来日线。")
                        bars.append({"date": date_.isoformat(), **{k: row.get(k) for k in
                                     ("open", "high", "low", "close", "volume", "amount")},
                                     "price_adjustment": "qfq", "complete": True})
                    if len({b["date"] for b in bars}) != len(bars):
                        raise ProviderError("上游日线日期重复。")
                    self.history_cache[key] = (time.monotonic(), deepcopy(bars))
                bundle["bars"][symbol] = bars
            except (ProviderError, KeyError, TypeError, ValueError):
                warnings.append(f"{symbol} 历史不足或复权/单位校验失败；相关策略暂停。")
            if time.monotonic() >= deadline:
                warnings.append("本轮总时限已到，后续证券未采集。")
                break
        # Bounded per-process cache; never persisted with the token or endpoint.
        if len(self.history_cache) > 200:
            self.history_cache.clear()
        if not bundle["quotes"]:
            raise ProviderError("未取得任何合格的腾讯报价；没有演示数据兜底。")
        warnings.extend(["此通道不提供权威涨跌停价、交易单位和停牌状态，严格就绪条件保持阻断；仅研究与价格风险提醒。",
                         "历史日线为上游声明的前复权口径，仅用于当前观察；不用于重建历史时点或保证成交。"])
        bundle["warnings"] = warnings[:200]
        bundle["capabilities"] = {"quotes": True, "history": bool(bundle["bars"]), "themes": bool(bundle["themes"]),
                                  "calendar": bundle["calendar_confirmed"], "evidence": False,
                                  "daily_adjustment": "qfq", "full_market_scan": False,
                                  "execution_metadata": False, "order_routing": False}
        bundle["as_of"] = bundle["received_at"] = clock().isoformat()
        return validate_import_bundle(bundle)
