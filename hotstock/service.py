import hashlib
import os
import threading
import time
import uuid
from copy import deepcopy
from datetime import datetime, timedelta
from urllib.parse import urlsplit

import httpx

from .defaults import default_account, default_config
from .demo import demo_bundle
from .models import Bundle
from .storage import Store
from .timeutil import SHANGHAI, now_iso, parse_time


class ServiceError(ValueError):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status


def identifier():
    return f"{time.time_ns()}_{uuid.uuid4().hex[:8]}"


def public_config(config):
    result = deepcopy(config)
    opts = result.get("provider_options", {})
    for key in ("token", "api_key", "password"):
        if key in opts:
            opts[f"has_{key}"] = bool(opts.pop(key))
    # API credential may be set through environment, so absence in the DB is not absence of a credential.
    env_name = "HOTSTOCK_TUSHARE_TOKEN" if result.get("provider") == "tushare" else "HOTSTOCK_SHARED_DATA_TOKEN" if result.get("provider") == "http" else None
    opts["has_token"] = bool(opts.get("has_token") or (env_name and os.environ.get(env_name)))
    note = result.get("notifications", {})
    note["has_token"] = bool(note.pop("token", ""))
    # Webhook paths and query parameters can themselves contain credentials.
    webhook = note.pop("webhook_url", "")
    note["has_webhook_url"] = bool(webhook)
    note["webhook_host"] = urlsplit(webhook).netloc if webhook else ""
    return result


class MarketService:
    def __init__(self, db_path):
        self.store = Store(db_path)
        self.mutation_lock = threading.RLock()
        self.refresh_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.next_refresh = 0.0
        self.worker = None
        if self.store.get("config") is None:
            self.store.put("config", default_config())
        for mode in ("demo", "live", "historical"):
            if self.store.get(f"account:{mode}") is None:
                self.store.put(f"account:{mode}", default_account(mode))

    @property
    def config(self):
        return self.store.get("config", default_config())

    def account(self, mode=None):
        mode = mode or self.config["mode"]
        return self.store.get(f"account:{mode}", default_account(mode))

    def event(self, kind, title, detail, mode=None, db=None):
        mode = mode or self.config["mode"]
        item = {"id": identifier(), "kind": kind, "title": title,
                "detail": detail, "created_at": now_iso(), "mode": mode}
        self.store.record("event", item, mode, db)
        return item

    def initialize(self):
        if self.config["mode"] == "demo" and not self.store.latest_snapshot("demo"):
            self.import_bundle(demo_bundle(), origin="demo")

    def clean_error(self, exc):
        text = str(exc)[:1000]
        conf = self.config
        secrets = [conf.get("provider_options", {}).get(k, "") for k in ("token", "api_key", "password")]
        secrets += [conf.get("notifications", {}).get("token", ""), conf.get("notifications", {}).get("webhook_url", "")]
        secrets += [os.environ.get(k, "") for k in ("HOTSTOCK_TUSHARE_TOKEN", "HOTSTOCK_SHARED_DATA_TOKEN", "HOTSTOCK_API_KEY")]
        for value in secrets:
            if value:
                text = text.replace(str(value), "[REDACTED]")
        return text

    def select_provider(self, conf, selected):
        previous = conf["provider"]
        if previous != selected:
            self.store.put(f"provider_options:{previous}", conf["provider_options"])
            conf["provider_options"] = self.store.get(f"provider_options:{selected}", default_config()["provider_options"])
            conf["provider"] = selected
        return conf

    def update_config(self, patch):
        if self.refresh_lock.locked():
            raise ServiceError("采集进行中，请完成后再修改数据源配置", 409)
        with self.mutation_lock:
            conf = self.config
            if "provider" in patch and patch["provider"] is not None:
                self.select_provider(conf, patch["provider"])
                conf["mode"] = "demo" if patch["provider"] == "demo" else "historical" if patch["provider"] == "tushare" else "live"
            if patch.get("mode") is not None:
                conf["mode"] = patch["mode"]
                if conf["mode"] == "demo":
                    self.select_provider(conf, "demo")
                elif conf["provider"] == "demo":
                    self.select_provider(conf, "http")
            for name in ("auto_refresh", "refresh_seconds"):
                if patch.get(name) is not None:
                    conf[name] = patch[name]
            if patch.get("risk") is not None:
                conf["risk"].update({k: v for k, v in patch["risk"].items() if v is not None})
            if conf["risk"]["max_theme_pct"] < conf["risk"]["max_position_pct"]:
                raise ServiceError("同题材仓位上限不能小于单只股票上限")
            if patch.get("provider_options") is not None:
                allowed = {"token", "base_url", "timeout_seconds", "collection_timeout_seconds", "symbols", "history_days", "max_symbols", "max_themes", "theme_names", "include_news", "include_themes", "trade_date", "allow_insecure_http"}
                updates = {k: v for k, v in patch["provider_options"].items() if k in allowed}
                for key in ("symbols", "theme_names"):
                    if key in updates and (not isinstance(updates[key], list) or len(updates[key]) > 100 or any(not isinstance(s, str) or len(s) > 100 for s in updates[key])):
                        raise ServiceError(f"{key} 必须为不超过100项的字符串列表")
                for key, low, high in (("timeout_seconds", 2, 60), ("collection_timeout_seconds", 5, 600), ("history_days", 30, 500), ("max_symbols", 1, 100), ("max_themes", 1, 10)):
                    if key in updates and (not isinstance(updates[key], int) or isinstance(updates[key], bool) or not low <= updates[key] <= high):
                        raise ServiceError(f"{key} 必须是 {low} 到 {high} 之间的整数")
                for key in ("include_news", "include_themes", "allow_insecure_http"):
                    if key in updates and not isinstance(updates[key], bool):
                        raise ServiceError(f"{key} 必须为布尔值")
                for key in ("token", "base_url", "trade_date"):
                    if key in updates and (not isinstance(updates[key], str) or len(updates[key]) > 4096 or "\r" in updates[key] or "\n" in updates[key]):
                        raise ServiceError(f"{key} 必须为单行文本")
                if "base_url" in updates and updates["base_url"]:
                    parsed = urlsplit(updates["base_url"])
                    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
                        raise ServiceError("数据源地址必须是 http/https 地址，认证信息请使用 Token 字段")
                    if parsed.query or parsed.fragment:
                        raise ServiceError("数据源地址不接受查询字符串/片段；密钥请单独填写 Token")
                conf["provider_options"].update(updates)
            if patch.get("notifications") is not None:
                updates = {k: v for k, v in patch["notifications"].items() if k in {"enabled", "webhook_url", "token"}}
                if "enabled" in updates and not isinstance(updates["enabled"], bool):
                    raise ServiceError("通知 enabled 必须为布尔值")
                for key in ("webhook_url", "token"):
                    if key in updates and (not isinstance(updates[key], str) or len(updates[key]) > 4096 or "\r" in updates[key] or "\n" in updates[key]):
                        raise ServiceError(f"通知 {key} 必须为单行文本")
                if "webhook_url" in updates and updates["webhook_url"]:
                    parsed = urlsplit(updates["webhook_url"])
                    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username:
                        raise ServiceError("Webhook 地址格式不正确")
                conf["notifications"].update(updates)
            if patch.get("account") is not None:
                account = self.account(conf["mode"])
                account.update(patch["account"])
                account["updated_at"] = now_iso()
                if "equity" in patch["account"]:
                    account["equity_updated_at"] = now_iso()
                if account.get("cash") is not None and account.get("equity") is not None and account["cash"] > account["equity"]:
                    raise ServiceError("现金不能大于账户权益；请核对手工参考值")
                self.store.put(f"account:{conf['mode']}", account)
            self.store.put("config", conf)
            self.next_refresh = 0
            self.event("config", "配置已更新", {"provider": conf["provider"], "mode": conf["mode"]})
        return self.get_config()

    def get_config(self):
        result = public_config(self.config)
        result["account"] = self.account()
        return result

    def refresh(self, provider=None):
        if not self.refresh_lock.acquire(blocking=False):
            raise ServiceError("已有采集任务进行中", 409)
        try:
            from .providers import fetch_bundle
            conf = self.config
            selected = provider or conf["provider"]
            target_mode = "demo" if selected == "demo" else "historical" if selected == "tushare" else "live"
            self.select_provider(conf, selected)
            conf["mode"] = target_mode
            self.store.put("config", conf)
            old = self.store.get(f"status:{target_mode}", {})
            status = {**old, "state": "running", "provider": selected, "last_attempt_at": now_iso(), "last_error": None}
            self.store.put(f"status:{target_mode}", status)
            started = time.monotonic()
            options = deepcopy(conf["provider_options"])
            if selected == "tushare" and os.environ.get("HOTSTOCK_TUSHARE_TOKEN"):
                options["token"] = os.environ["HOTSTOCK_TUSHARE_TOKEN"]
            elif selected == "http" and os.environ.get("HOTSTOCK_SHARED_DATA_TOKEN"):
                options["token"] = os.environ["HOTSTOCK_SHARED_DATA_TOKEN"]
            bundle = demo_bundle() if selected == "demo" else fetch_bundle(selected, options)
            if selected != "demo" and bundle.get("mode") == "demo":
                raise ServiceError("真实数据源返回了演示数据，已拒绝", 502)
            result = self.import_bundle(bundle, origin=selected, lock_held=True)
            self.store.put(f"status:{bundle['mode']}", {"state": "ok", "provider": selected,
                "last_attempt_at": status["last_attempt_at"], "last_success_at": now_iso(),
                "last_error": None, "duration_seconds": round(time.monotonic() - started, 2),
                "warnings": result["warnings"]})
            return result
        except Exception as exc:
            mode = self.config["mode"]
            status = self.store.get(f"status:{mode}", {})
            message = self.clean_error(exc)
            status.update(state="error", last_error=message, last_attempt_at=status.get("last_attempt_at", now_iso()))
            self.store.put(f"status:{mode}", status)
            self.event("source_error", "数据采集失败", {"error": message, "fallback": False}, mode)
            if isinstance(exc, ServiceError):
                raise
            raise ServiceError(message, 502) from None
        finally:
            self.refresh_lock.release()

    def import_bundle(self, value, origin="import", lock_held=False):
        acquired = False
        if not lock_held:
            acquired = self.refresh_lock.acquire(blocking=False)
            if not acquired:
                raise ServiceError("已有采集任务进行中", 409)
        try:
            from .engine import analyze_market
            if value.get("mode") != "demo":
                from .providers import ProviderError, validate_import_bundle
                try:
                    value = validate_import_bundle(value)
                except ProviderError as exc:
                    raise ServiceError(str(exc)) from None
            bundle = Bundle.model_validate(value).model_dump(mode="json")
            if bundle["mode"] != "demo":
                real_now = datetime.now(SHANGHAI)
                times = [bundle["as_of"], bundle["received_at"]]
                times.extend(q[key] for q in bundle["quotes"] for key in ("quote_time", "received_at") if q.get(key))
                if any(parse_time(stamp) > real_now for stamp in times):
                    raise ServiceError("快照或行情时间晚于真实当前时间，拒绝将未来数据导入真实/历史工作区；请核对源端时钟。")
            with self.mutation_lock:
                if bundle["mode"] in {"demo", "historical"}:
                    previous = self.store.latest_snapshot(bundle["mode"])
                    if previous and parse_time(bundle["as_of"]) < parse_time(previous["bundle"]["as_of"]):
                        state_exists = (self.store.records("trade", bundle["mode"], 1)
                                        or self.store.records("plan", bundle["mode"], 1)
                                        or self.store.get(f"peaks:{bundle['mode']}", {}))
                        if state_exists:
                            raise ServiceError("当前研究工作区已有计划或成交状态，不能把快照时钟回退到更早时间；请用新的 --data-dir 开始另一轮历史研究，以免使用未来持仓或高点。", 409)
                conf = self.config
                conf["mode"] = bundle["mode"]
                if origin == "demo":
                    self.select_provider(conf, "demo")
                elif origin == "import":
                    conf["auto_refresh"] = False
                    if conf["provider"] == "demo" and bundle["mode"] != "demo":
                        self.select_provider(conf, "http")
                clock = bundle["as_of"] if bundle["mode"] in {"demo", "historical"} else now_iso()
                analysis = analyze_market(bundle, conf["risk"], clock)
                sid = identifier()
                self.store.snapshot(bundle, analysis, sid, now_iso())
                self.store.put("config", conf)
                self.store.put(f"status:{bundle['mode']}", {"state": "ok", "provider": origin,
                    "last_attempt_at": now_iso(), "last_success_at": now_iso(), "last_error": None,
                    "warnings": bundle["warnings"]})
                self.event("snapshot", "数据快照已保存", {"snapshot_id": sid, "source": bundle["source"],
                    "as_of": bundle["as_of"], "quotes": len(bundle["quotes"]), "themes": len(bundle["themes"]),
                    "warnings": bundle["warnings"], "summary": analysis.get("summary", {})}, bundle["mode"])
                self._capture_candidates(analysis.get("candidates", []), bundle["mode"], sid)
            self.monitor()
            return {"ok": True, "mode": bundle["mode"], "source": bundle["source"], "as_of": bundle["as_of"],
                    "warnings": bundle["warnings"], "summary": analysis.get("summary", {})}
        finally:
            if acquired:
                self.refresh_lock.release()

    def _capture_candidates(self, candidates, mode, sid):
        old = self.store.get(f"candidate_states:{mode}", {})
        new = {}
        for candidate in candidates:
            key = candidate["id"]
            state = (candidate.get("status"), tuple(candidate.get("blockers", [])))
            new[key] = list(state[:1]) + [list(state[1])]
            if old.get(key) != new[key]:
                self.event("candidate", "候选状态变化", {"snapshot_id": sid, "candidate": candidate}, mode)
        for key in old.keys() - new.keys():
            self.event("candidate_removed", "候选不再满足本轮观察范围", {"candidate_id": key, "snapshot_id": sid}, mode)
        self.store.put(f"candidate_states:{mode}", new)

    def positions(self, mode=None, bundle=None):
        from .engine import sellable_quantity
        mode = mode or self.config["mode"]
        if bundle is None:
            snapshot = self.store.latest_snapshot(mode)
            bundle = snapshot["bundle"] if snapshot else {}
        trades = sorted(self.store.records("trade", mode), key=lambda t: (parse_time(t["executed_at"]), t["id"]))
        positions = self._positions_from_trades(trades)
        quotes = {q["symbol"]: q for q in bundle.get("quotes", [])}
        clock = bundle.get("as_of", now_iso()) if mode in {"demo", "historical"} else now_iso()
        peaks = self.store.get(f"peaks:{mode}", {})
        for pos in positions:
            pos["theme_ids"] = sorted(set(pos.get("theme_ids", [])) | {
                t["id"] for t in bundle.get("themes", []) if pos["symbol"] in t.get("members", [])
            })
            pos["theme_membership_unknown"] = not bool(pos["theme_ids"])
            q = quotes.get(pos["symbol"])
            price = q.get("price") if q else None
            reliable = q and q.get("time_quality") in ({"simulated"} if mode == "demo" else {"exchange", "provider"})
            stamp = q.get("quote_time") if q else None
            age = (parse_time(clock) - parse_time(stamp)).total_seconds() if stamp else None
            stale = not reliable or age is None or age < 0 or age > self.config["risk"]["max_quote_age_seconds"]
            pos.update(mode=mode,price=price, price_time=stamp, price_is_stale=stale,
                       market_value=round(pos["quantity"] * price, 2) if price else None,
                       unrealized_pnl=round(pos["quantity"] * (price - pos["avg_cost"]), 2) if price else None,
                       unrealized_pct=round((price / pos["avg_cost"] - 1) * 100, 2) if price else None)
            pos["sellable_quantity"] = sellable_quantity(pos["lots"], clock, bundle.get("calendar", []),
                                                         mode == "demo" or bundle.get("calendar_confirmed", False))
            peak_key = pos["symbol"] + ":" + pos["opened_at"]
            pos["highest_price"] = max(pos["avg_cost"], peaks.get(peak_key, pos["avg_cost"]))
            if price and not stale:
                pos["highest_price"] = max(pos["highest_price"], price)
                peaks[peak_key] = pos["highest_price"]
        self.store.put(f"peaks:{mode}", peaks)
        return positions

    def _positions_from_trades(self, trades):
        all_lots, names = {}, {}
        for trade in trades:
            symbol = trade["symbol"]
            names[symbol] = trade["name"]
            lots = all_lots.setdefault(symbol, [])
            if trade["side"] == "buy":
                lots.append({"quantity": trade["quantity"], "remaining": trade["quantity"],
                             "bought_at": trade["executed_at"], "price": trade["price"],
                             "plan_id": trade.get("plan_id"), "theme_id": trade.get("theme_id"),
                             "theme_ids": trade.get("theme_ids", []), "trade_id": trade["id"]})
            else:
                needed = trade["quantity"]
                sale_date = parse_time(trade["executed_at"]).date()
                for lot in lots:
                    if parse_time(lot["bought_at"]).date() >= sale_date:
                        continue
                    take = min(needed, lot["remaining"])
                    lot["remaining"] -= take
                    needed -= take
                    if not needed:
                        break
                if needed:
                    raise ServiceError("卖出数量超过在该成交日可用的旧持仓；当日新增份额不能当日卖出")
        result = []
        for symbol, lots in all_lots.items():
            active = [lot for lot in lots if lot["remaining"] > 0]
            qty = sum(lot["remaining"] for lot in active)
            if qty:
                plan_ids = {lot.get("plan_id") for lot in active}
                result.append({"symbol": symbol, "name": names[symbol], "quantity": qty,
                    "avg_cost": round(sum(lot["remaining"] * lot["price"] for lot in active) / qty, 6),
                    "lots": active, "opened_at": active[0]["bought_at"],
                    "plan_id": next(iter(plan_ids)) if len(plan_ids) == 1 else None,
                    "plan_allocation_ambiguous": len(plan_ids) > 1,
                    "theme_id": next((lot["theme_id"] for lot in active if lot.get("theme_id")), None),
                    "theme_ids": sorted({tid for lot in active for tid in lot.get("theme_ids", [])} |
                                        {lot["theme_id"] for lot in active if lot.get("theme_id")})})
        return result

    def dashboard(self):
        from .engine import analyze_market, size_position
        conf = self.config
        mode = conf["mode"]
        snap = self.store.latest_snapshot(mode)
        self.monitor()
        bundle = snap["bundle"] if snap else {}
        clock = bundle.get("as_of", now_iso()) if mode in {"demo", "historical"} else now_iso()
        analysis = analyze_market(bundle, conf["risk"], clock) if snap else {"themes": [], "candidates": [], "quality_issues": ["当前工作区尚未取得数据"], "summary": {}}
        positions = self.positions(mode, bundle)
        themes = {t["id"]: t for t in bundle.get("themes", [])}
        quotes = {q["symbol"]: q for q in bundle.get("quotes", [])}
        for c in analysis.get("candidates", []):
            c["theme_ids"] = sorted({t["id"] for t in themes.values() if c["symbol"] in t.get("members", [])} |
                                    ({c["theme_id"]} if c.get("theme_id") else set()))
            c["evidence"] = themes.get(c.get("theme_id"), {}).get("evidence", [])
            c["sizing"] = size_position(c, self.account(mode), conf["risk"], quotes.get(c["symbol"], {}), positions)
        status = self.store.get(f"status:{mode}", {"state": "never", "last_error": None})
        status["snapshot_as_of"] = bundle.get("as_of")
        status["quotes_with_reliable_time"] = sum(q.get("time_quality") in {"exchange", "provider"} and bool(q.get("quote_time")) for q in bundle.get("quotes", []))
        status["total_quotes"] = len(bundle.get("quotes", []))
        status["warnings"] = list(dict.fromkeys(status.get("warnings", []) + bundle.get("warnings", [])))
        status["capabilities"] = bundle.get("capabilities", {})
        return {"mode": mode, "source": bundle.get("source", conf["provider"]), "as_of": bundle.get("as_of"),
                "received_at": bundle.get("received_at"), "analysis": analysis, "account": self.account(mode),
                "positions": positions, "plans": self.store.records("plan", mode),
                "alerts": [a for a in self.store.records("alert", mode) if a.get("is_active")],
                "quotes": list(quotes.values()), "news": bundle.get("news", []), "data_status": status,
                "scheduler": {"enabled": conf["auto_refresh"], "refresh_seconds": conf["refresh_seconds"],
                              "effective_refresh_seconds": self.effective_refresh_seconds(conf),
                              "collecting": self.refresh_lock.locked(), "running": bool(self.worker and self.worker.is_alive())}}

    def create_plan(self, candidate_id):
        with self.mutation_lock:
            data = self.dashboard()
            candidate = next((c for c in data["analysis"]["candidates"] if c["id"] == candidate_id), None)
            if candidate is None:
                raise ServiceError("候选已不在当前快照，请刷新后重新查看", 404)
            if candidate["status"] in {"blocked", "expired"}:
                raise ServiceError("数据不足或已过期的候选不能建立有效计划")
            for existing in data["plans"]:
                if existing["symbol"] == candidate["symbol"] and existing.get("status") not in {"cancelled", "expired", "invalidated", "closed"}:
                    raise ServiceError("该股票已有冻结计划，请先查看或取消原计划", 409)
            plan = deepcopy(candidate)
            plan.update(id=identifier(), candidate_id=candidate_id, recorded_at=now_iso(),
                        source_snapshot_id=self.store.latest_snapshot(data["mode"])["id"],
                        risk_settings=deepcopy(self.config["risk"]), manual_execution_only=True,
                        price_policy="frozen", last_evaluated_at=now_iso())
            plan.setdefault("created_at", now_iso())
            plan["initial_stop_price"] = plan.get("stop_price")
            self.store.record("plan", plan, data["mode"])
            self.event("plan_created", "交易计划已冻结", {"plan_id": plan["id"], "plan": plan}, data["mode"])
            return plan

    def cancel_plan(self, plan_id):
        with self.mutation_lock:
            mode = self.config["mode"]
            plan = next((p for p in self.store.records("plan", mode) if p["id"] == plan_id), None)
            if not plan:
                raise ServiceError("计划不存在于当前工作区", 404)
            plan.update(status="cancelled", can_execute=False, cancelled_at=now_iso())
            self.store.record("plan", plan, mode)
            self.event("plan_cancelled", "计划已取消", {"plan_id": plan_id}, mode)
            return plan

    def record_trade(self, value):
        with self.mutation_lock:
            conf = self.config
            mode = conf["mode"]
            snap = self.store.latest_snapshot(mode)
            bundle = snap["bundle"] if snap else {}
            clock = bundle.get("as_of", now_iso()) if mode in {"demo", "historical"} else now_iso()
            trade_time = parse_time(value["executed_at"])
            value["executed_at"] = trade_time.isoformat()
            if trade_time > parse_time(clock) + timedelta(seconds=5):
                raise ServiceError("成交时间不能晚于当前工作区时钟")
            if mode != "demo" and value["symbol"].startswith("DEMO"):
                raise ServiceError("模拟证券不能记入真实工作区")
            if mode == "demo" and not value["symbol"].startswith("DEMO"):
                raise ServiceError("演示工作区只允许 DEMO 证券；真实记录请先切换数据源")
            plans = self.store.records("plan", mode)
            if value.get("plan_id"):
                plan = next((p for p in plans if p["id"] == value["plan_id"]), None)
                if not plan or plan["symbol"] != value["symbol"]:
                    raise ServiceError("计划与证券或工作区不匹配")
                value["theme_id"] = plan.get("theme_id")
            elif not value.get("theme_id"):
                value["theme_id"] = next((t["id"] for t in bundle.get("themes", []) if value["symbol"] in t["members"]), None)
            value["theme_ids"] = sorted({t["id"] for t in bundle.get("themes", []) if value["symbol"] in t.get("members", [])} |
                                        ({value["theme_id"]} if value.get("theme_id") else set()))
            if bundle.get("calendar_confirmed") or mode == "demo":
                if trade_time.date().isoformat() not in bundle.get("calendar", []):
                    raise ServiceError("成交日期不在当前确认的交易日历覆盖内，请先补全日历")
            trade = {**value, "id": identifier(), "mode": mode, "created_at": now_iso(),
                     "record_source": "manual_confirmed", "execution": "record_only"}
            existing = self.store.records("trade", mode)
            self._positions_from_trades(sorted(existing + [trade], key=lambda t: (parse_time(t["executed_at"]), t["id"])))
            account = self.account(mode)
            notional = round(trade["quantity"] * trade["price"], 2)
            fee = round(notional * conf["risk"]["fee_pct"] / 100, 2)
            trade.update(notional=notional, estimated_fee=fee, fee_is_estimate=True)
            cash = account.get("cash")
            if cash is not None:
                delta = -notional - fee if trade["side"] == "buy" else notional - fee
                if cash + delta < -0.001:
                    raise ServiceError("记录金额超过已登记现金；请先核对资金参考值")
                account["cash"] = round(cash + delta, 2)
            account["updated_at"] = now_iso()
            # Equity is deliberately not fabricated from an incomplete manually entered account.
            account["equity_note"] = "权益为上次手工核定参考值；现金按记录及估算费用变化，请定期与券商核对。"
            with self.store.connect() as db:
                self.store.record("trade", trade, mode, db)
                self.store.put(f"account:{mode}", account, db)
                self.event("trade_recorded", "已记录外部成交" if mode != "demo" else "已记录模拟成交", trade, mode, db)
            self.monitor()
            return {"trade": trade, "positions": self.positions(mode, bundle), "account": account}

    def monitor(self):
        from .engine import analyze_market, evaluate_position
        try:
            from .engine import evaluate_plan
        except ImportError:
            evaluate_plan = None
        with self.mutation_lock:
            conf = self.config
            mode = conf["mode"]
            snap = self.store.latest_snapshot(mode)
            if not snap:
                return
            bundle = snap["bundle"]
            clock = bundle["as_of"] if mode in {"demo", "historical"} else now_iso()
            plans = self.store.records("plan", mode)
            if evaluate_plan:
                for p in plans:
                    if p.get("status") in {"cancelled", "closed"}:
                        continue
                    state = evaluate_plan(p, bundle, p.get("risk_settings", conf["risk"]), clock)
                    if not isinstance(state, dict):
                        continue
                    # Only lifecycle and evidence fields can change. Entry/stop anchors remain frozen.
                    before = (p.get("status"), p.get("can_execute"))
                    for key in ("status", "can_execute", "blockers", "reasons", "last_evaluated_at", "invalidated_at", "invalidation_reason"):
                        if key in state:
                            p[key] = state[key]
                    p["last_evaluated_at"] = clock
                    self.store.record("plan", p, mode)
                    if before != (p.get("status"), p.get("can_execute")):
                        self.event("plan_state", "冻结计划状态变化", {"plan_id": p["id"], "before": before, "status": p["status"]}, mode)
            positions = self.positions(mode, bundle)
            quotes = {q["symbol"]: q for q in bundle["quotes"]}
            analysis = analyze_market(bundle, conf["risk"], clock)
            themes = {t["id"]: t for t in analysis.get("themes", [])}
            plans_map = {p["id"]: p for p in plans}
            active = {}
            equity = self.account(mode).get("equity")
            for pos in positions:
                if equity and not any(p.get("price_is_stale") or p.get("market_value") is None for p in positions):
                    exposure_by_theme = {tid: sum(p["market_value"] for p in positions if tid in p.get("theme_ids", [])) / equity * 100
                                         for tid in pos.get("theme_ids", [])}
                    pos["theme_exposure_by_id"] = exposure_by_theme
                    pos["theme_exposure_pct"] = max(exposure_by_theme.values()) if exposure_by_theme else None
                plan = plans_map.get(pos.get("plan_id"))
                alerts = evaluate_position(pos, plan, quotes.get(pos["symbol"]), themes.get(pos.get("theme_id")),
                                           conf["risk"], clock, bundle["calendar"], mode == "demo" or bundle["calendar_confirmed"])
                if pos.get("plan_allocation_ambiguous"):
                    alerts.append({"code":"PLAN_ALLOCATION_REVIEW","level":"warning",
                                   "message":"同一证券存在不同计划或未关联计划的批次，请分别核对退出依据；当前显示整仓基础风险提醒。",
                                   "triggered_at":clock,"sellable_quantity":pos.get("sellable_quantity"),"status":"review"})
                for alert in alerts:
                    aid = hashlib.sha256(f"{mode}|{pos['symbol']}|{alert.get('code')}".encode()).hexdigest()[:24]
                    active[aid] = {**alert, "id": aid, "symbol": pos["symbol"], "name": pos["name"],
                                   "mode": mode, "created_at": alert.get("triggered_at", clock), "is_active": True,
                                   "last_seen_at": now_iso(), "execution": "not_executed"}
            existing = {a["id"]: a for a in self.store.records("alert", mode)}
            new_alerts = []
            for aid, alert in active.items():
                old = existing.get(aid)
                if old:
                    alert["created_at"] = old["created_at"]
                self.store.record("alert", alert, mode)
                if not old or not old.get("is_active") or old.get("status") != alert.get("status"):
                    self.event("risk_alert", "风控条件变化", alert, mode)
                    new_alerts.append(alert)
            for aid, old in existing.items():
                if aid not in active and old.get("is_active"):
                    old.update(is_active=False, resolved_at=now_iso())
                    self.store.record("alert", old, mode)
                    self.event("risk_condition_cleared", "风险条件不再满足（不代表已成交）", {"alert_id": aid}, mode)
        if new_alerts and mode != "demo" and conf.get("notifications", {}).get("enabled"):
            threading.Thread(target=self.send_notification, args=({"type": "risk_alerts", "mode": mode, "alerts": new_alerts},), daemon=True).start()

    def send_notification(self, payload, test=False):
        options = self.config.get("notifications", {})
        if not options.get("webhook_url") or (not test and not options.get("enabled")):
            if test:
                raise ServiceError("尚未配置 Webhook URL")
            return
        headers = {"Content-Type": "application/json"}
        if options.get("token"):
            headers["Authorization"] = "Bearer " + str(options["token"])
        try:
            with httpx.Client(timeout=10, follow_redirects=False) as client:
                response = client.post(options["webhook_url"], json=payload, headers=headers)
                response.raise_for_status()
            self.event("notification", "Webhook 通知已送达接收端", {"http_status": response.status_code, "test": test})
            return {"ok": True, "http_status": response.status_code}
        except Exception as exc:
            self.event("notification_error", "Webhook 通知失败", {"error": self.clean_error(exc), "test": test})
            if test:
                raise ServiceError("Webhook 通知失败，请查看事件日志", 502) from None

    def export(self):
        conf = self.config
        mode = conf["mode"]
        return {"schema_version": 1, "exported_at": now_iso(), "mode": mode,
                "config": self.get_config(), "snapshot": self.store.latest_snapshot(mode),
                "plans": self.store.records("plan", mode), "trades": self.store.records("trade", mode),
                "alerts": self.store.records("alert", mode), "events": self.store.records("event", mode),
                "snapshot_history": self.store.snapshot_history(mode),
                "note": "用于追溯分析，不是收益回测报告。成交来自手工确认，导出不含配置密钥。"}

    def start(self):
        self.stop_event.clear()
        self.worker = threading.Thread(target=self._loop, daemon=True, name="hotstock-monitor")
        self.worker.start()

    def stop(self):
        self.stop_event.set()
        if self.worker:
            self.worker.join(timeout=3)

    @staticmethod
    def effective_refresh_seconds(conf):
        minimum = 600 if conf["provider"] == "akshare" else 1800 if conf["provider"] == "tushare" else 60
        return max(minimum, conf["refresh_seconds"])

    def _loop(self):
        while not self.stop_event.wait(5):
            try:
                self.monitor()
                conf = self.config
                if conf["auto_refresh"] and not self.refresh_lock.locked() and time.monotonic() >= self.next_refresh:
                    # Public scraping defaults to a low-frequency research workflow.
                    self.next_refresh = time.monotonic() + self.effective_refresh_seconds(conf)
                    threading.Thread(target=self._refresh_background, daemon=True, name="hotstock-collector").start()
            except Exception as exc:
                self.event("monitor_error", "后台监控异常", {"error": self.clean_error(exc)})

    def _refresh_background(self):
        try:
            self.refresh()
        except ServiceError:
            pass  # refresh records the error and retains the last real snapshot as stale evidence.
