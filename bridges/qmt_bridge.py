"""Read-only MiniQMT bridge; exposes no trading/account API.

Run with: python -m uvicorn bridges.qmt_bridge:create_app --factory ...
Requires a compatible locally running MiniQMT on the bridge machine.
"""
from __future__ import annotations

import hashlib
import hmac
import importlib
import json
import os
from pathlib import Path
import threading
from datetime import datetime, timedelta

from hotstock.providers.common import (
    CN_TZ, ProviderError, blank_bundle, board_of, bounded_int, date_text,
    frame_records, iso_now, list_option, now_cn, number, symbol_of,
    timestamp, validate_import_bundle,
)


def epoch_millis(value) -> datetime | None:
    numeric = number(value)
    if numeric is None or numeric < 946684800000 or numeric > 4102444800000:
        return None
    try:
        return datetime.fromtimestamp(numeric / 1000, CN_TZ)
    except (ValueError, OverflowError, OSError):
        return None


def positive_int(value):
    n = number(value)
    return int(n) if n is not None and n > 0 and n.is_integer() else None


class QmtCollector:
    def __init__(self, config_path: str | Path, *, xtdata=None, clock=now_cn):
        self.config_path = Path(config_path)
        self.xt = xtdata
        self.clock = clock
        self.lock = threading.Lock()
        self.prepared = set()
        self.state_path = self.config_path.with_suffix(self.config_path.suffix + ".observed.json")

    def _config(self):
        try:
            raw = self.config_path.read_text(encoding="utf-8-sig")
            if len(raw.encode("utf-8")) > 1024 * 1024:
                raise ProviderError("QMT 桥配置超过 1 MiB。")
            config = json.loads(raw)
        except (OSError, ValueError):
            raise ProviderError("无法读取 QMT 桥 JSON 配置。") from None
        if not isinstance(config, dict):
            raise ProviderError("QMT 桥配置必须为对象。")
        symbols = [symbol_of(s) for s in list_option(config, "symbols", 100)]
        if not symbols:
            raise ProviderError("QMT 桥需明确配置 symbols。")
        if any(s.endswith(".BJ") for s in symbols):
            raise ProviderError("本版 QMT 桥只验收沪深股票；北交所交易日历与字段需单独适配。")
        config["symbols"] = symbols
        if not isinstance(config.get("themes", []), list):
            raise ProviderError("QMT 桥 themes 必须为数组。")
        return config

    def _sdk(self):
        if self.xt is None:
            try:
                self.xt = importlib.import_module("xtquant.xtdata")
            except Exception:
                raise ProviderError("无法加载 XtQuant 行情模块；请在 MiniQMT 所在机器安装券商/迅投匹配的 SDK 并启动终端。") from None
        return self.xt

    def _call(self, method, *args, **kwargs):
        try:
            return getattr(self._sdk(), method)(*args, **kwargs)
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"MiniQMT {method} 读取失败（{type(exc).__name__}）；请检查终端连接、SDK 版本和数据权限。") from None

    def _themes(self, config, now):
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8")) if self.state_path.exists() else {}
        except (OSError, ValueError):
            raise ProviderError("主题首次观察记录无法读取；不能重写或回填历史时点。") from None
        if not isinstance(state, dict):
            raise ProviderError("主题首次观察记录格式错误。")
        themes, warnings = [], []
        changed = False
        for raw in config.get("themes", []):
            if not isinstance(raw, dict) or not raw.get("id") or not raw.get("name"):
                raise ProviderError("每个 QMT 人工主题都需要 id、name 和 members。")
            members = [symbol_of(s) for s in list_option(raw, "members", 100)]
            if not members or any(s not in config["symbols"] for s in members):
                raise ProviderError("主题成员必须非空并全部存在于桥 symbols 配置。")
            # Ignore supplied known_at: changes become known when this bridge
            # first observes their exact configuration content.
            canonical = {k: v for k, v in raw.items() if k != "known_at"}
            digest = hashlib.sha256(json.dumps(canonical, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
            key = f"{raw['id']}:{digest}"
            if key not in state:
                state[key] = now.isoformat()
                changed = True
            known_at = state[key]
            timestamp(known_at, "theme first observation")
            verified = raw.get("confirmed") is True
            evidence = []
            for item in raw.get("evidence", []):
                if not isinstance(item, dict):
                    raise ProviderError("主题 evidence 项必须为对象。")
                evidence_symbols = [symbol_of(s) for s in list_option(item, "symbols", 100)]
                if any(s not in members for s in evidence_symbols):
                    raise ProviderError("主题证据 symbols 必须属于该主题；不能自动扩展证据覆盖范围。")
                evidence.append({
                    "title": str(item.get("title") or "人工配置证据"),
                    "url": item.get("url"), "published_at": item.get("published_at"),
                    "observed_at": known_at, "kind": str(item.get("kind") or "manual_review"),
                    "summary": str(item.get("summary") or ""), "symbols": evidence_symbols,
                    "confidence": "verified" if verified and item.get("verified") is True else "unverified",
                })
            covered = {s for e in evidence if e["confidence"] == "verified" and e["url"] for s in e["symbols"]}
            has_verified_evidence = set(members).issubset(covered)
            themes.append({"id": str(raw["id"]), "name": str(raw["name"]), "members": members,
                "known_at": known_at, "membership_quality": "point_in_time" if verified and has_verified_evidence else "unknown",
                "evidence": evidence})
            if not verified or not has_verified_evidence:
                warnings.append(f"主题 {raw['name']} 的人工成员/证据尚未确认，membership_quality=unknown，严格模式应阻断。")
        if changed:
            try:
                temporary = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
                temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
                temporary.replace(self.state_path)
            except OSError:
                raise ProviderError("无法保存主题首次观察时点；本轮不输出可能回填的主题关系。") from None
        return themes, warnings

    def bundle(self) -> dict:
        if not self.lock.acquire(blocking=False):
            raise ProviderError("QMT 桥正在准备行情/历史，请稍后重试。")
        try:
            return self._bundle()
        finally:
            self.lock.release()

    def _bundle(self):
        config, now = self._config(), self.clock()
        symbols = config["symbols"]
        days = bounded_int(config, "history_days", 120, 40, 1500)
        minimum = bounded_int(config, "min_history_bars", 20, 20, 500)
        start, end = (now.date() - timedelta(days=days)).strftime("%Y%m%d"), now.strftime("%Y%m%d")
        result = blank_bundle("miniqmt-readonly", "live", now.isoformat())
        themes, warnings = self._themes(config, now)
        result["themes"], result["warnings"] = themes, warnings
        # Pull actual historical trading dates only. Future days are not synthesized.
        calendar_sets = []
        for market in sorted({s.split(".")[1] for s in symbols}):
            raw_dates = self._call("get_trading_dates", market, start_time=(now.date() - timedelta(days=max(days, 365))).strftime("%Y%m%d"), end_time=end, count=-1)
            if not isinstance(raw_dates, (list, tuple)):
                raise ProviderError("MiniQMT 交易日列表格式异常。")
            parsed = {dt.date().isoformat() for item in raw_dates if (dt := epoch_millis(item)) is not None and dt.date() <= now.date()}
            if not parsed:
                raise ProviderError("MiniQMT 未返回可靠交易日历，不能确认日 K 或可卖日。")
            calendar_sets.append(parsed)
        if any(s != calendar_sets[0] for s in calendar_sets[1:]):
            raise ProviderError("沪深交易日历返回不一致，需核验后再输出。")
        result["calendar"] = sorted(calendar_sets[0])
        # A current trading day explicitly returned by the API establishes today.
        result["calendar_confirmed"] = now.date().isoformat() in result["calendar"]
        if not result["calendar_confirmed"]:
            result["warnings"].append("当前日期未出现在终端交易日列表；休市或日历缺失，未来交易日未推算。")
        for symbol in symbols:
            cache_key = (symbol, now.date().isoformat(), start)
            if config.get("download_history", True) is True and cache_key not in self.prepared:
                self._call("download_history_data", symbol, period="1d", start_time=start, end_time=end)
                self.prepared.add(cache_key)
        historical = self._call("get_market_data_ex", field_list=["time", "open", "high", "low", "close", "volume", "amount", "suspendFlag"],
            stock_list=symbols, period="1d", start_time=start, end_time=end, count=-1, dividend_type="none", fill_data=False)
        if not isinstance(historical, dict):
            raise ProviderError("MiniQMT 日 K 返回格式异常。")
        # get_full_tick starts full-quote retrieval in supported modern XtQuant;
        # first-call empty results fail visibly and can be retried after warmup.
        ticks = self._call("get_full_tick", symbols)
        if not isinstance(ticks, dict):
            raise ProviderError("MiniQMT 快照返回格式异常。")
        units = config.get("security_rules", {})
        if not isinstance(units, dict):
            raise ProviderError("security_rules 必须是对象。")
        for symbol in symbols:
            tick = ticks.get(symbol)
            if not isinstance(tick, dict) or not number(tick.get("lastPrice")) or number(tick.get("lastPrice")) <= 0:
                result["warnings"].append(f"{symbol} 没有有效最新价；没有用旧缓存或模拟值填充。")
                continue
            qtime = epoch_millis(tick.get("time"))
            details = self._call("get_instrument_detail", symbol, iscomplete=True) or {}
            if not isinstance(details, dict):
                details = {}
            # The documented contract says today's stop prices, but reject them
            # when quote day/previous-close consistency does not support today.
            prev = number(tick.get("lastClose"))
            detail_prev = number(details.get("PreClose"))
            current_detail = bool(qtime and qtime.date() == now.date() and prev and detail_prev and abs(prev - detail_prev) <= 0.011)
            upper = number(details.get("UpStopPrice")) if current_detail else None
            lower = number(details.get("DownStopPrice")) if current_detail else None
            upper = upper if upper and upper > 0 else None
            lower = lower if lower and lower > 0 else None
            rule = units.get(symbol, {})
            if not isinstance(rule, dict):
                raise ProviderError("每只股票的 security_rules 必须为对象。")
            confirmed_rule = rule.get("confirmed") is True and isinstance(rule.get("source_url"), str) and rule["source_url"].startswith(("https://", "http://"))
            minimum_buy = positive_int(details.get("MinLimitOrderVolume"))
            step = positive_int(rule.get("buy_qty_step")) if confirmed_rule else None
            if minimum_buy is None and confirmed_rule:
                minimum_buy = positive_int(rule.get("min_buy_qty"))
            volume = number(tick.get("volume"))
            quote = {"symbol": symbol, "name": str(details.get("InstrumentName") or symbol),
                "price": number(tick["lastPrice"]), "prev_close": prev, "open": number(tick.get("open")),
                "high": number(tick.get("high")), "low": number(tick.get("low")),
                "volume": volume * 100 if volume is not None else None, "amount": number(tick.get("amount")),
                "quote_time": qtime.isoformat(timespec="milliseconds") if qtime else None,
                "received_at": self.clock().isoformat(), "time_quality": "provider" if qtime else "unknown",
                "source_time_precision": "milliseconds", "is_suspended": False if details.get("IsTrading") is True else None,
                "limit_up": upper, "limit_down": lower, "board": board_of(symbol),
                "min_buy_qty": minimum_buy, "buy_qty_step": step}
            bar_rows = frame_records(historical[symbol], "MiniQMT 日 K") if symbol in historical else []
            bars = {}
            for row in bar_rows:
                dt = epoch_millis(row.get("time"))
                if dt is None:
                    continue
                if dt.date() == now.date() and row.get("suspendFlag") in (0, 1, -1):
                    quote["is_suspended"] = row["suspendFlag"] == 1
                if dt.date() >= now.date() or dt.date().isoformat() not in result["calendar"] or row.get("suspendFlag") == 1:
                    continue
                prices = {k: number(row.get(k)) for k in ("open", "high", "low", "close")}
                if any(v is None or v <= 0 for v in prices.values()):
                    continue
                v = number(row.get("volume"))
                bars[dt.date().isoformat()] = {"date": dt.date().isoformat(), **prices,
                    "volume": v * 100 if v is not None else None, "amount": number(row.get("amount"))}
            result["bars"][symbol] = [bars[day] for day in sorted(bars)]
            if len(bars) < minimum:
                result["warnings"].append(f"{symbol} 仅有 {len(bars)} 根已完成日 K，少于 {minimum}，该股应阻断信号。")
            if step is None or minimum_buy is None:
                result["warnings"].append(f"{symbol} 买入单位未完整确认；VolumeMultiple 不是买入步长，未作替代。")
            if not upper or not lower:
                result["warnings"].append(f"{symbol} 当日涨跌停价缺失或与快照日期/前收不一致。")
            result["quotes"].append(quote)
        if not result["quotes"]:
            raise ProviderError("MiniQMT 未返回任何有效快照；首次初始化请稍后重试，并检查终端行情连接。")
        result["received_at"] = self.clock().isoformat()
        result["as_of"] = result["received_at"]
        result["capabilities"] = {"quotes": True, "themes": bool(themes), "history": any(result["bars"].values()),
            "calendar": bool(result["calendar"]), "evidence": any(e.get("confidence") == "verified" for t in themes for e in t["evidence"])}
        result["warnings"].append("该桥只读行情；不读取券商账户，不产生委托。主题由本桥人工配置；未自动发现全市场主题。")
        return validate_import_bundle(result)


def create_app(config_path=None, token=None, collector=None):
    from fastapi import FastAPI, Header, HTTPException
    path = config_path or os.environ.get("HOTSTOCK_QMT_CONFIG", "bridges/bridge_config.json")
    secret = token if token is not None else os.environ.get("HOTSTOCK_QMT_TOKEN", "")
    if not isinstance(secret, str) or len(secret) < 16 or "\n" in secret or "\r" in secret:
        raise ProviderError("请设置至少 16 字符的独立 HOTSTOCK_QMT_TOKEN。")
    source = collector or QmtCollector(path)
    app = FastAPI(title="Hotstock MiniQMT 只读数据桥", docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    def health():
        return {"status": "running", "service": "miniqmt-readonly-bridge", "terminal_connected": "not_probed", "trading": False}

    @app.get("/bundle")
    def market_bundle(authorization: str | None = Header(default=None)):
        expected = "Bearer " + secret
        if not authorization or not hmac.compare_digest(authorization, expected):
            raise HTTPException(status_code=401, detail="数据桥 Token 不正确。")
        try:
            return source.bundle()
        except ProviderError as error:
            raise HTTPException(status_code=502, detail=str(error)) from None

    return app
