"""Deterministic research signals and risk calculations; this module never trades.

Inputs are point-in-time JSON dictionaries (see docs/CONTRACT.md).  In particular,
fetch time is never substituted for an exchange/provider quote timestamp.  A
``ready`` result is a research condition, not a fill or an order instruction.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
from datetime import date, datetime, time
from decimal import Decimal, ROUND_HALF_UP
from statistics import fmean
from zoneinfo import ZoneInfo


SHANGHAI = ZoneInfo("Asia/Shanghai")
ENGINE_VERSION = "1.0.0"
DEFAULT_SETTINGS = {
    "risk_per_trade_pct": 0.5,
    "max_position_pct": 10.0,
    "max_theme_pct": 25.0,
    "slippage_pct": 0.3,
    "fee_pct": 0.05,
    "max_quote_age_seconds": 180,
    "min_history_bars": 20,
    "min_theme_members": 2,
    "min_theme_up_ratio": 0.6,
    "min_theme_change_pct": 1.0,
    "entry_buffer_pct": 0.3,
    "max_chase_pct": 1.5,
    "max_stop_distance_pct": 8.0,
    "trailing_stop_pct": 6.0,
    "max_holding_sessions": 5,
    "max_account_drawdown_pct": 6.0,
    "strict_data_quality": True,
}


def _number(value, *, positive=False):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(result) or (positive and result <= 0):
        return None
    return result


def _integer(value, *, minimum=0):
    number = _number(value)
    if number is None or number < minimum or not number.is_integer():
        return None
    return int(number)


def _dt(value):
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if result.tzinfo is None or result.utcoffset() is None:
        return None
    return result.astimezone(SHANGHAI)


def _day(value):
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _now(value):
    result = _dt(value)
    if result is None:
        raise ValueError("now 必须是包含明确时区偏移的 ISO 8601 时间")
    return result


def _config(settings):
    result = dict(DEFAULT_SETTINGS)
    for key, default in DEFAULT_SETTINGS.items():
        if key not in (settings or {}):
            continue
        if isinstance(default, bool):
            result[key] = settings[key] is True
        else:
            value = _number(settings[key])
            if value is not None and value >= 0:
                result[key] = value
    # A smaller optional setting must not remove the minimum history needed by
    # the actual 20-session algorithms.
    result["min_history_bars"] = max(20, int(result["min_history_bars"]))
    result["min_theme_members"] = max(2, int(result["min_theme_members"]))
    return result


def _price(value):
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _unique(items):
    return list(dict.fromkeys(items))


def _calendar(values):
    if not isinstance(values, list) or not values:
        return [], ["缺少交易日历"]
    days = [_day(value) for value in values]
    if any(day is None for day in days):
        return [], ["交易日历包含无效日期"]
    if len(set(days)) != len(days):
        return [], ["交易日历包含重复交易日"]
    return sorted(days), []


def _is_continuous_session(moment, days):
    local = moment.timetz().replace(tzinfo=None)
    return moment.date() in days and (
        time(9, 30) <= local < time(11, 30)
        or time(13) <= local < time(14, 57)
    )


def _mode(bundle):
    return bundle.get("mode", "live")


def _context_issues(bundle, moment, *, entry=True):
    mode = _mode(bundle)
    days, issues = _calendar(bundle.get("calendar", []))
    if mode not in {"demo", "live", "historical"}:
        issues.append("未知数据模式")
    symbols = [quote.get("symbol") for quote in bundle.get("quotes", []) if isinstance(quote, dict)]
    if len(symbols) != len(set(symbols)):
        issues.append("行情包含重复证券，无法确定唯一时点快照")
    if not symbols:
        issues.append("缺少可用于扫描的行情证券")
    if not bundle.get("themes"):
        issues.append("缺少题材成员数据，无法验证板块扩散")
    if mode != "demo" and bundle.get("calendar_confirmed") is not True:
        issues.append("交易日历未经确认，不能判断可执行入场")
    if days and not (days[0] <= moment.date() <= days[-1]):
        issues.append("当前日期超出交易日历覆盖范围")
    if entry and not _is_continuous_session(moment, days):
        issues.append("当前不在已确认的连续竞价交易时段")
    for key in ("as_of", "received_at"):
        value = bundle.get(key)
        if value is not None:
            parsed = _dt(value)
            if parsed is None:
                issues.append(f"{key} 缺少可靠时区或格式无效")
            elif parsed > moment:
                issues.append(f"{key} 晚于评估时间，存在未来信息")
    return days, issues


def _quote_issues(quote, moment, cfg, mode, *, require_limits=False):
    if not isinstance(quote, dict):
        return ["缺少个股行情"]
    issues = []
    timestamp = _dt(quote.get("quote_time"))
    if timestamp is None:
        issues.append("缺少带时区的可靠 quote_time；抓取时间不能代替行情时间")
    else:
        if timestamp > moment:
            issues.append("行情时间晚于评估时间，存在未来信息")
        if timestamp.date() != moment.date():
            issues.append("行情不是评估当日的数据")
        if (moment - timestamp).total_seconds() > cfg["max_quote_age_seconds"]:
            issues.append("行情超过允许的新鲜度，不能视作实时信号")
    allowed = {"exchange", "provider"}
    if mode == "demo":
        allowed.add("simulated")
    if quote.get("time_quality") not in allowed:
        issues.append("行情时间来源未经确认")
    received = quote.get("received_at")
    if received is not None:
        parsed = _dt(received)
        if parsed is None or parsed > moment:
            issues.append("行情 received_at 无效或晚于评估时间")
        elif timestamp is not None and parsed < timestamp:
            issues.append("行情接收时间早于行情发生时间")
    if _number(quote.get("price"), positive=True) is None:
        issues.append("当前价格缺失或无效")
    if _number(quote.get("prev_close"), positive=True) is None:
        issues.append("昨收价格缺失或无效")
    if require_limits:
        upper = _number(quote.get("limit_up"), positive=True)
        lower = _number(quote.get("limit_down"), positive=True)
        if upper is None or lower is None or upper <= lower:
            issues.append("缺少有效的当日涨跌停价格；不按旧比例猜测")
        if _integer(quote.get("min_buy_qty"), minimum=1) is None or _integer(
            quote.get("buy_qty_step"), minimum=1
        ) is None:
            issues.append("缺少该证券最小买入数量或递增单位")
    return _unique(issues)


def _history(bars, days, moment, cfg):
    issues, clean, dates = [], [], []
    if not isinstance(bars, list):
        return [], ["缺少历史日 K"]
    last_complete = [
        day for day in days
        if day < moment.date()
        or (day == moment.date() and moment.timetz().replace(tzinfo=None) >= time(15))
    ]
    for bar in bars:
        if not isinstance(bar, dict):
            issues.append("历史日 K 格式无效")
            continue
        day = _day(bar.get("date"))
        if day is None:
            issues.append("历史日 K 日期无效")
            continue
        dates.append(day)
        if day > moment.date() or (day == moment.date() and day not in last_complete):
            issues.append("历史日 K 含未来或当日尚未完成的数据")
        if days and day not in days:
            issues.append("历史日 K 日期不在交易日历中")
        if bar.get("complete") is False or bar.get("is_complete") is False:
            issues.append("历史日 K 被标记为尚未完成")
        for time_key in ("published_at", "available_at", "bar_end"):
            if bar.get(time_key) is not None:
                parsed = _dt(bar[time_key])
                if parsed is None or parsed > moment:
                    issues.append("历史日 K 的可获知时间无效或包含未来信息")
        values = {key: _number(bar.get(key), positive=True) for key in ("open", "high", "low", "close")}
        volume = _number(bar.get("volume"))
        if any(value is None for value in values.values()) or volume is None or volume < 0:
            issues.append("历史日 K 缺少有效 OHLC 或成交量")
            continue
        if values["high"] + 1e-8 < max(values.values()) or values["low"] - 1e-8 > min(values.values()):
            issues.append("历史日 K 高低价关系无效")
            continue
        clean.append({**values, "volume": volume, "date": day.isoformat()})
    if len(set(dates)) != len(dates):
        issues.append("历史日 K 包含重复交易日")
    if dates != sorted(dates):
        issues.append("历史日 K 未按交易日正序排列")
    required = cfg["min_history_bars"]
    if len(clean) < required:
        issues.append(f"完整历史不足 {required} 个交易日")
    if len(last_complete) < required:
        issues.append("交易日历不足以验证完整历史区间")
    elif dates[-required:] != last_complete[-required:]:
        issues.append("最近历史日 K 不完整或未更新到最近完成的交易日")
    return clean, _unique(issues)


def _theme_timing_issues(theme, moment, mode):
    issues = []
    known = _dt(theme.get("known_at"))
    if known is None:
        issues.append("题材成员缺少可验证的 known_at")
    elif known > moment:
        issues.append("题材归属在评估时尚不可知，不能事后补标签")
    qualities = {"point_in_time", "current_snapshot"}
    if mode == "demo":
        qualities.add("simulated")
    if theme.get("membership_quality") not in qualities:
        issues.append("题材成员来源或时点质量不足")
    for evidence in theme.get("evidence", []):
        if not isinstance(evidence, dict):
            issues.append("题材证据格式无效")
            continue
        for key in ("published_at", "observed_at"):
            value = evidence.get(key)
            if value is not None:
                parsed = _dt(value)
                if parsed is None or parsed > moment:
                    issues.append("题材证据时间无效或包含未来信息")
    return _unique(issues)


def _analyze_themes(bundle, quotes, moment, cfg):
    result = []
    mode = _mode(bundle)
    for theme in bundle.get("themes", []):
        members = _unique(theme.get("members", []))
        issues = _theme_timing_issues(theme, moment, mode)
        active, observed_changes = [], []
        for symbol in members:
            quote = quotes.get(symbol)
            if not _quote_issues(quote, moment, cfg, mode) and not quote.get("is_suspended", False):
                active.append(quote)
            if isinstance(quote, dict) and not quote.get("is_suspended", False):
                price = _number(quote.get("price"), positive=True)
                previous = _number(quote.get("prev_close"), positive=True)
                stamp = _dt(quote.get("quote_time"))
                if price is not None and previous is not None and (stamp is None or stamp <= moment):
                    observed_changes.append((price / previous - 1) * 100)
        changes = [(float(quote["price"]) / float(quote["prev_close"]) - 1) * 100 for quote in active]
        up_ratio = sum(change > 0 for change in changes) / len(changes) if changes else 0.0
        avg = fmean(changes) if changes else 0.0
        if len(members) < cfg["min_theme_members"] or len(active) < cfg["min_theme_members"]:
            issues.append("题材有效成员不足，不能把单只股票异动当作板块扩散")
        if members and len(active) / len(members) < 0.8:
            issues.append("题材成员当日有效行情覆盖不足 80%")
        heat = round(min(100, max(0, up_ratio * 60 + min(max(avg, 0) / 3, 1) * 40)), 1) if changes else None
        strong = up_ratio >= cfg["min_theme_up_ratio"] and avg >= cfg["min_theme_change_pct"]
        status = "blocked" if issues else "expansion" if strong else "weakening" if avg < 0 and up_ratio < 0.45 else "mixed"
        reasons = ([f"有效行情 {len(active)}/{len(members)} 只，上涨占比 {up_ratio:.0%}", f"有效成员等权平均涨跌 {avg:+.2f}%"]
                   if changes else [f"有效当日行情 0/{len(members)} 只；可靠的板块广度与涨跌指标缺失"])
        observed_avg = fmean(observed_changes) if observed_changes else None
        observed_up = sum(change > 0 for change in observed_changes) / len(observed_changes) if observed_changes else None
        observed_only = bool(observed_changes) and len(active) < len(observed_changes)
        if observed_only:
            reasons.append("部分报价只能作为参考快照，不能当作实时板块确认")
        if theme.get("membership_quality") == "current_snapshot":
            reasons.append("成员为当前快照，仅能用于 known_at 之后的研究")
        if mode == "demo":
            reasons.append("模拟题材与模拟行情，不代表真实市场")
        result.append({
            "id": theme.get("id"), "name": theme.get("name", theme.get("id", "未命名题材")),
            "members": members, "member_count": len(members), "valid_member_count": len(active),
            "up_ratio": round(up_ratio, 4) if changes else None,
            "avg_change_pct": round(avg, 3) if changes else None, "heat_score": heat,
            "observed_avg_change_pct": round(observed_avg, 3) if observed_avg is not None else None,
            "observed_up_ratio": round(observed_up, 4) if observed_up is not None else None,
            "observed_member_count": len(observed_changes), "observed_only": observed_only,
            "status": status, "entry_allowed": strong and not issues,
            "reasons": reasons, "quality_issues": issues, "known_at": theme.get("known_at"),
            "as_of": moment.isoformat(), "mode": mode,
        })
    return result


def _setup(bars, strategy, cfg):
    if len(bars) < cfg["min_history_bars"]:
        return None
    last20 = bars[-20:]
    close = last20[-1]["close"]
    ma20 = fmean(bar["close"] for bar in last20)
    ma10 = fmean(bar["close"] for bar in last20[-10:])
    ma5 = fmean(bar["close"] for bar in last20[-5:])
    reasons = []
    if strategy == "expansion":
        reference = max(bar["high"] for bar in last20)
        return5 = (close / bars[-6]["close"] - 1) * 100
        prior_breakout = any(
            bars[index]["close"] > max(bar["high"] for bar in bars[index-5:index]) * 1.003
            for index in range(len(bars) - 3, len(bars))
        )
        if close < ma20 * 0.97 or return5 > 15 or prior_breakout:
            return None
        support = min(bar["low"] for bar in last20[-3:])
        reasons = [
            "等待突破前 20 个完整交易日的高点，最近三日未出现本规则定义的提前突破",
            f"近 5 个交易日涨跌 {return5:+.2f}%，失效参考最近三日低点",
        ]
    else:
        recent = last20[-10:]
        peak_index = max(range(len(recent)), key=lambda index: recent[index]["high"])
        peak = recent[peak_index]["high"]
        drawdown = (1 - close / peak) * 100
        advance = (peak / last20[0]["close"] - 1) * 100
        if not (ma10 > ma20 * 1.001 and close >= ma20 and advance >= 5 and peak_index < len(recent)-1 and 1 <= drawdown <= 12):
            return None
        reference = max(recent[-1]["high"], ma5)
        support = min(bar["low"] for bar in recent[peak_index + 1:])
        reasons = [
            "10 日均价不低于 20 日均价，回撤后价格仍在 20 日均价之上",
            f"距最近 10 日高点回撤 {drawdown:.2f}%，等待重新越过最近日高点和 5 日均价",
        ]
    entry_min = _price(reference * (1 + cfg["entry_buffer_pct"] / 100))
    entry_max = _price(reference * (1 + (cfg["entry_buffer_pct"] + cfg["max_chase_pct"]) / 100))
    stop = _price(support * 0.998)
    return {"entry_min": entry_min, "entry_max": entry_max, "stop_price": stop,
            "reasons": reasons, "reference_date": last20[-1]["date"], "setup_valid": True}


def _expiry(days, moment):
    possible = [day for day in days if day > moment.date() or (day == moment.date() and moment.timetz().replace(tzinfo=None) < time(15))]
    return datetime.combine(possible[0], time(15), SHANGHAI).isoformat() if possible else None


def _buy_liquidity_issues(quote, plan):
    issues = []
    if quote.get("is_suspended") is True:
        issues.append("证券停牌，不能执行入场")
    price = _number(quote.get("price"), positive=True)
    upper = _number(quote.get("limit_up"), positive=True)
    if price is not None and upper is not None and price >= upper - 0.005:
        issues.append("当前触及涨停，封板排队不能视为可买到")
    if upper is not None and _number(plan.get("entry_min"), positive=True) and plan["entry_min"] > upper:
        issues.append("入场下限高于当日涨停价")
    for key in ("ask_volume", "ask1_volume", "best_ask_volume"):
        if key in quote and _number(quote[key]) == 0:
            issues.append("已知卖方对手盘为空，不能确认入场条件可执行")
    return _unique(issues)


def _evaluate_frozen(plan, quote, theme, moment, cfg, mode, data_issues):
    result = copy.deepcopy(plan)
    result["evaluated_at"] = moment.isoformat()
    result["quote_time"] = quote.get("quote_time") if isinstance(quote, dict) else None
    result["can_execute"] = False
    result["blockers"] = _unique(list(data_issues))
    if plan.get("status") in {"cancelled", "expired"}:
        return result
    expiry = _dt(plan.get("expires_at"))
    if expiry is not None and moment >= expiry:
        result.update(status="expired", invalidated_at=moment.isoformat(), invalidation_reason="已超过原定有效期")
        result["blockers"].append("已超过原定有效期")
        return result
    entry_min, entry_max, stop = (_number(plan.get(key), positive=True) for key in ("entry_min", "entry_max", "stop_price"))
    if entry_min is None or entry_max is None or stop is None or not (stop < entry_min <= entry_max):
        result["blockers"].append("计划缺少有效入场区间或失效线")
    elif (entry_max - stop) / entry_max * 100 > cfg["max_stop_distance_pct"]:
        result["blockers"].append("按入场上沿计算的失效距离超过配置上限")
    if expiry is None:
        result["blockers"].append("日历不足，无法确定计划过期时间")
    if result["blockers"]:
        result["status"] = "blocked"
        return result
    price = float(quote["price"])
    if price <= stop:
        result.update(status="expired", invalidated_at=moment.isoformat(), invalidation_reason="价格跌破原定失效线")
        result["blockers"].append("价格跌破原定失效线")
        return result
    if price > entry_max:
        result.update(status="expired", invalidated_at=moment.isoformat(), invalidation_reason="价格已超过原定追涨上限，保留原价且停止追价")
        result["blockers"].append("价格已超过原定追涨上限，保留原价且停止追价")
        return result
    liquidity = _buy_liquidity_issues(quote, plan)
    if liquidity:
        result.update(status="blocked", blockers=liquidity)
        return result
    if not theme or not theme.get("entry_allowed", False):
        result.update(status="watch", blockers=["题材当前广度或强度尚未满足确认条件"])
        return result
    if price < entry_min:
        result.update(status="watch", blockers=["价格尚未进入已冻结的触发区间"])
        return result
    result["status"] = "ready"
    result["can_execute"] = mode == "live"
    return result


def analyze_market(bundle: dict, settings: dict, now: str) -> dict:
    """Build explainable candidates using only information observable at ``now``."""
    moment, cfg, mode = _now(now), _config(settings), _mode(bundle)
    days, context_issues = _context_issues(bundle, moment)
    quotes = {quote.get("symbol"): quote for quote in bundle.get("quotes", []) if isinstance(quote, dict)}
    issues = list(context_issues)
    themes = _analyze_themes(bundle, quotes, moment, cfg)
    candidates, unmatched = [], []
    for theme in themes:
        issues.extend(f"{theme['name']}：{issue}" for issue in theme["quality_issues"])
        raw_theme = next((item for item in bundle.get("themes", []) if item.get("id") == theme["id"]), {})
        for symbol in theme["members"]:
            quote = quotes.get(symbol)
            name = quote.get("name", symbol) if quote else symbol
            history, history_issues = _history(bundle.get("bars", {}).get(symbol), days, moment, cfg)
            quote_issues = _quote_issues(quote, moment, cfg, mode, require_limits=True)
            stock_issues = context_issues + theme["quality_issues"] + history_issues + quote_issues
            issues.extend(f"{symbol}：{issue}" for issue in history_issues + quote_issues)
            setups = [(strategy, _setup(history, strategy, cfg) if not history_issues else None) for strategy in ("expansion", "pullback")]
            setups = [(strategy, setup) for strategy, setup in setups if setup is not None]
            if not setups and stock_issues:
                setups = [("expansion", {"entry_min": None, "entry_max": None, "stop_price": None, "reasons": ["数据不足，仅展示诊断，尚不能形成策略计划"], "setup_valid": False})]
            if not setups:
                unmatched.append(symbol)
            for strategy, setup in setups:
                reasons = list(setup["reasons"]) + theme["reasons"]
                for evidence in raw_theme.get("evidence", [])[:2]:
                    if isinstance(evidence, dict) and evidence.get("title"):
                        label = "已核实来源" if evidence.get("confidence") == "verified" else "待核实/模拟来源"
                        reasons.append(f"{label}：{evidence['title']}")
                identity = [ENGINE_VERSION, symbol, theme["id"], strategy, moment.date().isoformat(), setup.get("reference_date"), setup["entry_min"], setup["entry_max"], setup["stop_price"]]
                candidate = {
                    **setup, "id": "candidate_" + hashlib.sha256(json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()[:20],
                    "symbol": symbol, "name": name, "theme_id": theme["id"], "theme_name": theme["name"],
                    "strategy": strategy, "status": "watch", "can_execute": False, "reasons": reasons,
                    "blockers": [], "created_at": moment.isoformat(), "expires_at": _expiry(days, moment),
                    "mode": mode, "source": bundle.get("source", "unknown"), "quote_time": quote.get("quote_time") if quote else None,
                    "engine_version": ENGINE_VERSION, "score_is_probability": False,
                }
                candidates.append(_evaluate_frozen(candidate, quote, theme, moment, cfg, mode, stock_issues))
    candidates.sort(key=lambda item: ({"ready": 0, "watch": 1, "blocked": 2, "expired": 3}.get(item["status"], 4), item["theme_id"] or "", item["symbol"], item["strategy"]))
    return {
        "themes": themes, "candidates": candidates, "quality_issues": _unique(issues),
        "summary": {"engine_version": ENGINE_VERSION, "mode": mode, "theme_count": len(themes),
                    "candidate_count": len(candidates), "ready_count": sum(item["status"] == "ready" for item in candidates),
                    "watch_count": sum(item["status"] == "watch" for item in candidates),
                    "blocked_count": sum(item["status"] == "blocked" for item in candidates),
                    "expired_count": sum(item["status"] == "expired" for item in candidates),
                    "unmatched_symbols": _unique(unmatched), "as_of": moment.isoformat(),
                    "notice": "研究条件与热度排序不代表涨停概率或收益承诺；没有自动下单，触发不等于成交。"},
    }


def evaluate_plan(saved_plan: dict, bundle: dict, settings: dict, now: str) -> dict:
    """Re-evaluate a saved plan without changing its price levels or identity."""
    moment, cfg, mode = _now(now), _config(settings), _mode(bundle)
    days, issues = _context_issues(bundle, moment)
    quotes = {quote.get("symbol"): quote for quote in bundle.get("quotes", []) if isinstance(quote, dict)}
    quote = quotes.get(saved_plan.get("symbol"))
    if saved_plan.get("mode") != mode:
        issues.append("计划模式与行情模式不一致，禁止跨模式激活")
    created = _dt(saved_plan.get("created_at"))
    if created is None or created > moment:
        issues.append("计划创建时间无效或晚于评估时间")
    _, history_issues = _history(bundle.get("bars", {}).get(saved_plan.get("symbol")), days, moment, cfg)
    issues.extend(history_issues)
    issues.extend(_quote_issues(quote, moment, cfg, mode, require_limits=True))
    themes = _analyze_themes(bundle, quotes, moment, cfg)
    theme = next((item for item in themes if item["id"] == saved_plan.get("theme_id")), None)
    if theme is None or saved_plan.get("symbol") not in theme.get("members", []):
        issues.append("当前题材快照中找不到原计划的证券归属")
    elif theme["quality_issues"]:
        issues.extend(theme["quality_issues"])
    return _evaluate_frozen(saved_plan, quote, theme, moment, cfg, mode, issues)


def size_position(plan: dict, account: dict, settings: dict, security: dict, holdings: list | None = None) -> dict:
    """Calculate a planned quantity, never a guaranteed loss ceiling or an order."""
    cfg = _config(settings)
    equity, cash = _number(account.get("equity"), positive=True), _number(account.get("cash"))
    budget = equity * cfg["risk_per_trade_pct"] / 100 if equity is not None else None
    result = {"quantity": None, "notional": None, "planned_loss": None,
              "risk_budget": round(budget, 2) if budget is not None else None, "reason": "", "warnings": ["计划损失依赖成交假设；T+1、跳空、跌停、滑点可能使实际亏损超过预算。"]}
    if equity is None or cash is None or cash < 0:
        result["reason"] = "净资产或可用现金未知/无效，不提供可买股数"
        return result
    if any(account.get(key) is True for key in ("is_stale", "equity_is_stale", "cash_is_stale")):
        result["reason"] = "账户权益或现金已标记陈旧，需核对后再计算可买股数"
        return result
    entry, stop = _number(plan.get("entry_max"), positive=True), _number(plan.get("stop_price"), positive=True)
    if entry is None or stop is None or stop >= entry:
        result["reason"] = "计划入场上沿或失效线无效"
        return result
    minimum, step = _integer(security.get("min_buy_qty"), minimum=1), _integer(security.get("buy_qty_step"), minimum=1)
    if minimum is None or step is None:
        result["reason"] = "缺少该证券的最小买入数量与数量递增单位"
        return result
    if (entry - stop) / entry * 100 > cfg["max_stop_distance_pct"]:
        result.update(quantity=0, notional=0.0, planned_loss=0.0, reason="失效距离超过配置上限，不分配新增仓位")
        return result
    plan_theme_ids = set(value for value in (plan.get("theme_ids") or []) if isinstance(value, str) and value)
    if plan.get("theme_id"):
        plan_theme_ids.add(plan["theme_id"])
    if not plan_theme_ids:
        result["reason"] = "计划题材归属未知，无法可靠计算共同题材风险额度"
        return result
    symbol_value = 0.0
    theme_values = {theme_id: 0.0 for theme_id in plan_theme_ids}
    for holding in holdings or []:
        quantity = _integer(holding.get("quantity", 0))
        if quantity and any(holding.get(key) is True for key in ("price_is_stale", "valuation_is_stale")):
            result["reason"] = "现有持仓估值陈旧，无法可靠计算个股或题材剩余额度"
            return result
        reference_time = _dt(plan.get("evaluated_at", plan.get("created_at")))
        if quantity and "price_time" in holding and reference_time is not None:
            valuation_time = _dt(holding.get("price_time"))
            if (valuation_time is None or valuation_time > reference_time
                    or valuation_time.date() != reference_time.date()
                    or (reference_time - valuation_time).total_seconds() > cfg["max_quote_age_seconds"]):
                result["reason"] = "现有持仓估值时间无效或陈旧，暂不提供可买股数"
                return result
        theme_ids = set(value for value in (holding.get("theme_ids") or []) if isinstance(value, str) and value)
        if holding.get("theme_id"):
            theme_ids.add(holding["theme_id"])
        if not theme_ids and holding.get("symbol") == plan.get("symbol"):
            theme_ids.update(plan_theme_ids)
        if quantity and (not theme_ids or holding.get("theme_membership_unknown") is True):
            result["reason"] = "现有持仓题材归属未知，不能把它当作与新计划无关的风险"
            return result
        value = _number(holding.get("market_value"))
        if value is None:
            price = _number(holding.get("price"), positive=True)
            if quantity is not None and price is not None:
                value = quantity * price
            elif quantity == 0:
                value = 0.0
            else:
                result["reason"] = "现有持仓估值缺失，无法可靠计算个股或题材剩余额度"
                return result
        if value < 0 or quantity is None:
            result["reason"] = "现有持仓数量或估值无效"
            return result
        if holding.get("symbol") == plan.get("symbol"):
            symbol_value += value
        for theme_id in plan_theme_ids.intersection(theme_ids):
            theme_values[theme_id] += value
    reserve_per_share = entry * (cfg["slippage_pct"] + cfg["fee_pct"]) / 100
    loss_per_share = entry - stop + reserve_per_share
    theme_rooms = {theme_id: equity * cfg["max_theme_pct"] / 100 - value for theme_id, value in theme_values.items()}
    tightest_theme_room = min(theme_rooms.values())
    spend_limit = max(0.0, min(cash / (1 + cfg["fee_pct"] / 100), equity * cfg["max_position_pct"] / 100 - symbol_value, tightest_theme_room))
    raw_quantity = math.floor(min(budget / loss_per_share, spend_limit / entry) + 1e-9)
    quantity = 0 if raw_quantity < minimum else minimum + ((raw_quantity - minimum) // step) * step
    result.update(quantity=quantity, notional=round(quantity * entry, 2), planned_loss=round(quantity * loss_per_share, 2),
                  reason="预算或个股/题材/现金剩余额度不足最小买入数量" if quantity == 0 else "按入场区间上沿与费用滑点预留计算，并同时受现金、个股及题材额度约束")
    result["theme_exposures"] = {theme_id: round(theme_values[theme_id], 2) for theme_id in sorted(theme_values)}
    result["limiting_theme_ids"] = sorted(theme_id for theme_id, room in theme_rooms.items() if abs(room - tightest_theme_room) < 0.000001)
    if plan.get("mode") == "demo":
        result["warnings"].append("模拟仓位计算，仅演示研究流程")
    return result


def sellable_quantity(lots: list, now: str, calendar: list, calendar_confirmed: bool = True) -> int | None:
    """Return T+1 availability by purchase lot; None means it is not provable.

    For an unconfirmed calendar an optional ``sellable_confirmed_at`` (or
    ``confirmed_at``) must date each explicit broker availability to today.
    Even that value can never make today's newly purchased shares sellable.
    """
    moment = _now(now)
    if not lots:
        return 0
    days, issues = _calendar(calendar)
    usable_calendar = calendar_confirmed is True and not issues and days[0] <= moment.date() <= days[-1]
    total = 0
    for lot in lots:
        bought = _dt(lot.get("bought_at"))
        original = _integer(lot.get("quantity"))
        remaining = _integer(lot.get("remaining", lot.get("quantity")))
        if bought is None or bought > moment or original is None or remaining is None or remaining > original:
            return None
        if usable_calendar:
            if bought.date() not in days:
                return None
            allowed = any(bought.date() < day <= moment.date() for day in days)
            quantity = remaining if allowed else 0
            confirmed = lot.get("sellable_confirmed")
            if confirmed is not None:
                broker_quantity = _integer(confirmed)
                if broker_quantity is None or broker_quantity > remaining:
                    return None
                # Broker confirmation may reduce availability (e.g. reserved
                # orders), but can never override T+1 in this long-only model.
                quantity = min(quantity, broker_quantity)
            total += quantity
        else:
            confirmed_time = _dt(lot.get("sellable_confirmed_at", lot.get("confirmed_at")))
            confirmed = _integer(lot.get("sellable_confirmed"))
            if confirmed_time is None or confirmed_time > moment or confirmed_time.date() != moment.date() or confirmed is None or confirmed > remaining:
                return None
            total += confirmed if bought.date() < moment.date() else 0
    return total


def evaluate_position(position: dict, plan: dict | None, quote: dict | None, theme: dict | None,
                      settings: dict, now: str, calendar: list, calendar_confirmed: bool = True) -> list[dict]:
    """Produce alerts; no path infers a sale from a price or a stop trigger."""
    moment, cfg = _now(now), _config(settings)
    mode = position.get("mode") or (plan or {}).get("mode") or ("demo" if (quote or {}).get("time_quality") == "simulated" else "live")
    lots = position.get("lots") or []
    sellable = sellable_quantity(lots, now, calendar, calendar_confirmed)
    quantity = _integer(position.get("quantity"))
    if not lots and quantity:
        sellable = None
    elif lots and quantity is not None:
        remaining = [_integer(lot.get("remaining", lot.get("quantity"))) for lot in lots]
        if any(item is None for item in remaining) or sum(item for item in remaining if item is not None) != quantity:
            sellable = None
    alerts = []
    quality = _quote_issues(quote, moment, cfg, mode)
    execution_problems = []
    if sellable is None:
        execution_problems.append("可卖数量尚未确认")
    elif sellable <= 0:
        execution_problems.append("当前批次无可卖数量，今日新买份额受 T+1 限制")
    if isinstance(quote, dict):
        if quote.get("is_suspended") is True:
            execution_problems.append("证券停牌")
        price = _number(quote.get("price"), positive=True)
        lower = _number(quote.get("limit_down"), positive=True)
        if not quality and lower is not None and price is not None and price <= lower + 0.005:
            execution_problems.append("触及跌停，必须核实对手盘与排队，不能假定成交")
        for key in ("bid_volume", "bid1_volume", "best_bid_volume"):
            if key in quote and _number(quote[key]) == 0:
                execution_problems.append("已知买方对手盘为空")
    def add(code, level, message, *, review=False):
        suffix = "；" + "；".join(_unique(execution_problems)) if execution_problems and not review else ""
        if not review and quantity is not None and sellable is not None and 0 < sellable < quantity:
            suffix += f"；当前仅 {sellable}/{quantity} 股可卖，其余批次仍需等待或核对"
        alerts.append({"code": code, "level": level, "message": message + suffix,
                       "triggered_at": moment.isoformat(), "sellable_quantity": sellable,
                       "status": "review" if review else "cannot_sell" if execution_problems else "triggered"})
    if quality:
        add("DATA_STALE", "warning", "行情质量不足：" + "；".join(quality) + "；不会据此生成新的价格止损信号", review=True)
    if sellable is None:
        add("AVAILABILITY_UNKNOWN", "warning", "缺少可靠批次、交易日历或当日可卖确认，需核对券商真实可卖数量", review=True)
    lot_plan_ids = {lot.get("plan_id") for lot in lots if lot.get("plan_id")}
    ambiguous_plan = position.get("plan_allocation_ambiguous") is True or len(lot_plan_ids) > 1
    if ambiguous_plan:
        add("PLAN_ALLOCATION_UNKNOWN", "warning", "同一证券包含不同计划的持仓批次，需要逐批核对失效线；未将第一份计划的止损线套用到整个持仓", review=True)
    if not quality and not ambiguous_plan:
        price = float(quote["price"])
        cost = _number(position.get("avg_cost"), positive=True)
        stop = _number((plan or {}).get("stop_price"), positive=True)
        if stop is None and cost is not None:
            stop = _price(cost * (1 - cfg["max_stop_distance_pct"] / 100))
        if stop is not None and price <= stop:
            add("PRICE_STOP", "critical", f"现价 {price:.2f} 已触及失效线 {stop:.2f}；这是退出提醒，尚未成交")
        highest = _number(position.get("highest_price"), positive=True)
        if highest is not None and cost is not None and highest > cost:
            trailing = _price(highest * (1 - cfg["trailing_stop_pct"] / 100))
            if trailing > (stop or 0) and price <= trailing and not any(item["code"] == "PRICE_STOP" for item in alerts):
                add("TRAILING_STOP", "warning", f"价格从已记录高点回撤，触及移动保护线 {trailing:.2f}；提醒不代表卖出完成")
    if theme:
        up_ratio, change = _number(theme.get("up_ratio")), _number(theme.get("avg_change_pct"))
        theme_time = _dt(theme.get("as_of"))
        theme_fresh = theme_time is not None and theme_time <= moment and theme_time.date() == moment.date() and (moment - theme_time).total_seconds() <= cfg["max_quote_age_seconds"]
        if theme_fresh and not theme.get("quality_issues") and (theme.get("status") == "weakening" or (up_ratio is not None and change is not None and up_ratio < 0.4 and change < -1)):
            add("THEME_WEAKENING", "warning", "原题材的整体广度和强度转弱，需复核原跟涨逻辑")
    days, calendar_issues = _calendar(calendar)
    opened = _dt(position.get("opened_at"))
    if opened is None and lots:
        bought_times = [_dt(lot.get("bought_at")) for lot in lots]
        if all(value is not None for value in bought_times):
            opened = min(bought_times)
    if calendar_confirmed and not calendar_issues and opened is not None and opened.date() in days and days[0] <= moment.date() <= days[-1]:
        sessions = sum(opened.date() <= day <= moment.date() for day in days)
        if sessions >= max(1, int(cfg["max_holding_sessions"])):
            add("TIME_REVIEW", "warning", f"已进入第 {sessions} 个持有交易日，达到计划复核期限；重新评估，勿自动延长短线逻辑")
    drawdown = _number(position.get("account_drawdown_pct", position.get("portfolio_drawdown_pct")))
    if drawdown is not None and abs(drawdown) >= cfg["max_account_drawdown_pct"] and cfg["max_account_drawdown_pct"] > 0:
        add("ACCOUNT_DRAWDOWN", "critical", "账户回撤达到配置阈值，暂停新增风险并复核现有仓位", review=True)
    exposure = _number(position.get("theme_exposure_pct", (theme or {}).get("exposure_pct")))
    if exposure is not None and exposure > cfg["max_theme_pct"]:
        add("THEME_CONCENTRATION", "warning", "同题材合计仓位超过配置上限，应按共同下跌情景复核", review=True)
    return alerts
