"""Provider contracts and strict, dependency-free boundary helpers."""
from __future__ import annotations

import copy
import json
import math
import re
from datetime import date, datetime, timezone, timedelta
from typing import Any
from urllib.parse import urlsplit

CN_TZ = timezone(timedelta(hours=8))
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
SYMBOL_RE = re.compile(r"^[0-9]{6}\.(SH|SZ|BJ)$")


class ProviderError(RuntimeError):
    """A sanitized, displayable data collection or validation failure."""


def now_cn() -> datetime:
    return datetime.now(CN_TZ)


def iso_now() -> str:
    return now_cn().isoformat(timespec="seconds")


def number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def symbol_of(value: Any) -> str:
    text = str(value).strip().upper()
    if SYMBOL_RE.fullmatch(text):
        return text
    if re.fullmatch(r"(SH|SZ|BJ)\.[0-9]{6}", text):
        market, code = text.split(".")
        return f"{code}.{market}"
    if re.fullmatch(r"[0-9]{1,6}", text):
        code = text.zfill(6)
        if code.startswith(("4", "8", "92")):
            market = "BJ"
        elif code.startswith("6"):
            market = "SH"
        elif code.startswith(("0", "3")):
            market = "SZ"
        else:
            raise ProviderError("无法确定股票交易所；请提供六位代码及 .SH/.SZ/.BJ 后缀。")
        return f"{code}.{market}"
    raise ProviderError("股票代码格式错误；请使用例如 300001.SZ 的 A 股代码。")


def board_of(symbol: str) -> str:
    if symbol.endswith(".BJ"):
        return "BSE"
    if symbol.startswith(("688", "689")):
        return "STAR"
    if symbol.startswith(("300", "301", "302", "303")):
        return "CHINEXT"
    return "MAIN"


def date_text(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if re.fullmatch(r"\d{8}", text):
        text = f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    else:
        text = text[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        raise ProviderError("数据源返回了无效的交易日期。") from None


def timestamp(value: Any, field: str, nullable: bool = False) -> datetime | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        raise ProviderError(f"{field} 必须是带时区的 ISO 8601 时间。")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ProviderError(f"{field} 不是有效的 ISO 8601 时间。") from None
    if result.tzinfo is None or result.utcoffset() is None:
        raise ProviderError(f"{field} 缺少时区，不能推测行情发生时间。")
    return result


def bounded_int(options: dict, key: str, default: int, low: int, high: int) -> int:
    value = options.get(key, default)
    if isinstance(value, bool):
        raise ProviderError(f"{key} 必须是整数。")
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        raise ProviderError(f"{key} 必须是整数。") from None
    if str(value).strip() not in {str(parsed), f"{parsed}.0"} and not isinstance(value, int):
        raise ProviderError(f"{key} 必须是整数。")
    if not low <= parsed <= high:
        raise ProviderError(f"{key} 必须介于 {low} 和 {high} 之间。")
    return parsed


def list_option(options: dict, key: str, maximum: int = 100) -> list[str]:
    value = options.get(key, [])
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > maximum or any(not isinstance(x, str) for x in value):
        raise ProviderError(f"{key} 必须是最多 {maximum} 项的文本数组。")
    return list(dict.fromkeys(x.strip() for x in value if x.strip()))


def endpoint_url(value: Any, *, credentials: bool = False, allow_insecure: bool = False) -> str:
    if not isinstance(value, str) or len(value) > 2048:
        raise ProviderError("请配置完整的 HTTP/HTTPS 数据源 URL。")
    try:
        parts = urlsplit(value)
        _ = parts.port
    except ValueError:
        raise ProviderError("数据源 URL 无效。") from None
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password or parts.fragment:
        raise ProviderError("数据源 URL 必须是 HTTP/HTTPS，不能包含用户名、密码或片段。")
    if parts.query:
        raise ProviderError("数据源 URL 不接受查询参数；凭据请放在独立 token 配置中。")
    loopback = parts.hostname in {"localhost", "127.0.0.1", "::1"}
    if credentials and parts.scheme == "http" and not loopback and not allow_insecure:
        raise ProviderError("携带 Token 的远程数据源应使用 HTTPS；如供应商只支持 HTTP，须显式配置 allow_insecure_http=true，系统不会自动降级。")
    return value


def frame_records(frame: Any, label: str) -> list[dict]:
    try:
        records = frame.to_dict(orient="records")
    except Exception:
        raise ProviderError(f"{label} 返回格式异常，预期为表格。") from None
    if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
        raise ProviderError(f"{label} 返回格式异常。")
    return records


def blank_bundle(source: str, mode: str, as_of: str | None = None) -> dict:
    current = iso_now()
    return {
        "schema_version": 1, "source": source, "mode": mode,
        "as_of": as_of or current, "received_at": current,
        "quotes": [], "bars": {}, "themes": [], "calendar": [],
        "calendar_confirmed": False, "news": [],
        "capabilities": {"quotes": False, "themes": False, "history": False, "calendar": False, "evidence": False},
        "warnings": [],
    }


def _numeric_field(obj: dict, key: str, label: str, *, nullable: bool = True, positive: bool = False) -> None:
    value = obj.get(key)
    if value is None and nullable:
        obj.setdefault(key, None)
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ProviderError(f"{label}.{key} 必须是有限数值" + ("或 null。" if nullable else "。"))
    if value < 0 or (positive and value <= 0):
        raise ProviderError(f"{label}.{key} 数值范围无效。")


def validate_import_bundle(data: dict) -> dict:
    """Validate data only. Never execute code or infer missing source timestamps."""
    if not isinstance(data, dict):
        raise ProviderError("数据包必须是 JSON 对象。")
    try:
        encoded = json.dumps(data, ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise ProviderError("数据包必须只包含有效 JSON 值，不能包含 NaN、Infinity 或可执行对象。") from None
    if len(encoded.encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise ProviderError("数据包超过 8 MiB。")
    result = copy.deepcopy(data)
    if type(result.get("schema_version")) is not int or result["schema_version"] != 1:
        raise ProviderError("不支持的数据格式版本；schema_version 必须为 1。")
    if result.get("mode") not in {"live", "historical", "demo"}:
        raise ProviderError("mode 必须为 live、historical 或 demo。")
    if not isinstance(result.get("source"), str) or not result["source"].strip() or len(result["source"]) > 200:
        raise ProviderError("source 必须为非空数据源名称。")
    if result["mode"] != "demo" and result["source"].lower() == "demo":
        raise ProviderError("模拟数据不能声明为真实或历史数据。")
    as_of = timestamp(result.get("as_of"), "as_of")
    timestamp(result.get("received_at"), "received_at")
    for name in ("quotes", "themes", "calendar", "news", "warnings"):
        if not isinstance(result.get(name), list):
            raise ProviderError(f"{name} 必须是数组。")
    if len(result["quotes"]) > 10000 or len(result["themes"]) > 2000 or len(result["news"]) > 10000:
        raise ProviderError("数据包记录数超过单次导入上限。")
    if not isinstance(result.get("bars"), dict):
        raise ProviderError("bars 必须是按股票代码组织的对象。")
    if type(result.get("calendar_confirmed")) is not bool:
        raise ProviderError("calendar_confirmed 必须为 true 或 false。")
    capabilities = result.get("capabilities")
    if not isinstance(capabilities, dict) or any(type(capabilities.get(k)) is not bool for k in ("quotes", "themes", "history", "calendar", "evidence")):
        raise ProviderError("capabilities 必须明确标记 quotes/themes/history/calendar/evidence 五项布尔能力。")
    if any(not isinstance(x, str) for x in result["warnings"]):
        raise ProviderError("warnings 必须是文本数组。")
    seen = set()
    for quote in result["quotes"]:
        if not isinstance(quote, dict) or not isinstance(quote.get("symbol"), str) or not SYMBOL_RE.fullmatch(quote["symbol"]):
            raise ProviderError("quotes.symbol 必须为六位 A 股代码及交易所后缀。")
        symbol = quote["symbol"]
        if symbol in seen:
            raise ProviderError(f"股票 {symbol} 出现重复快照，不能确定采用哪条行情。")
        seen.add(symbol)
        if not isinstance(quote.get("name"), str) or not quote["name"]:
            raise ProviderError(f"{symbol} 缺少名称。")
        for key in ("price", "prev_close", "open", "high", "low", "volume", "amount", "limit_up", "limit_down"):
            _numeric_field(quote, key, symbol, nullable=key != "price", positive=key == "price")
        timestamp(quote.get("received_at"), f"{symbol}.received_at")
        quote_time = timestamp(quote.get("quote_time"), f"{symbol}.quote_time", nullable=True)
        quality = quote.get("time_quality")
        if quality not in {"exchange", "provider", "unknown", "simulated"}:
            raise ProviderError(f"{symbol}.time_quality 无效。")
        if quote_time is None and quality != "unknown":
            raise ProviderError(f"{symbol} 缺少行情时间，不得标记为已确认时间。")
        if result["mode"] != "demo" and quality == "simulated":
            raise ProviderError("模拟行情不能装入真实或历史数据包。")
        for key in ("min_buy_qty", "buy_qty_step"):
            value = quote.get(key)
            if value is not None and (type(value) is not int or value <= 0):
                raise ProviderError(f"{symbol}.{key} 必须为正整数或 null。")
            quote.setdefault(key, None)
        if quote.get("is_suspended") is not None and type(quote["is_suspended"]) is not bool:
            raise ProviderError(f"{symbol}.is_suspended 必须为布尔值或 null。")
        quote.setdefault("is_suspended", None)
        quote.setdefault("board", "UNKNOWN")
        if not isinstance(quote["board"], str):
            raise ProviderError(f"{symbol}.board 必须为文本。")
    for symbol, rows in result["bars"].items():
        if not isinstance(symbol, str) or not SYMBOL_RE.fullmatch(symbol) or not isinstance(rows, list):
            raise ProviderError("bars 的键必须为标准股票代码，值必须为日 K 数组。")
        if len(rows) > 10000:
            raise ProviderError("单只股票日 K 超过 10000 根。")
        dates = set()
        for row in rows:
            if not isinstance(row, dict):
                raise ProviderError(f"{symbol} 日 K 记录必须为对象。")
            day = date_text(row.get("date"))
            if row.get("date") != day:
                raise ProviderError("日 K 日期必须为 YYYY-MM-DD。")
            if day in dates:
                raise ProviderError(f"{symbol} 同一交易日出现重复日 K。")
            dates.add(day)
            if date.fromisoformat(day) > as_of.astimezone(CN_TZ).date():
                raise ProviderError(f"{symbol} 含有晚于 as_of 的日 K，不能用于该时点分析。")
            for key in ("open", "high", "low", "close", "volume", "amount"):
                _numeric_field(row, key, f"{symbol}.{day}", nullable=key in {"volume", "amount"}, positive=key not in {"volume", "amount"})
            if row["high"] < max(row["open"], row["close"], row["low"]) or row["low"] > min(row["open"], row["close"]):
                raise ProviderError(f"{symbol}.{day} 日 K 高低价关系无效。")
        rows.sort(key=lambda row: row["date"])
    theme_ids = set()
    for theme in result["themes"]:
        if not isinstance(theme, dict) or not isinstance(theme.get("id"), str) or not theme["id"] or not isinstance(theme.get("name"), str):
            raise ProviderError("主题必须包含文本 id 与 name。")
        if theme["id"] in theme_ids:
            raise ProviderError("主题 id 不能重复。")
        theme_ids.add(theme["id"])
        members = theme.get("members")
        if not isinstance(members, list) or any(not isinstance(x, str) or not SYMBOL_RE.fullmatch(x) for x in members):
            raise ProviderError("主题 members 必须为标准股票代码数组。")
        if len(set(members)) != len(members):
            raise ProviderError("同一主题成分不能重复。")
        known_at = timestamp(theme.get("known_at"), "theme.known_at", nullable=True)
        quality = theme.get("membership_quality")
        if quality not in {"point_in_time", "current_snapshot", "unknown", "simulated"}:
            raise ProviderError("membership_quality 无效。")
        if known_at is None and quality in {"point_in_time", "current_snapshot"}:
            raise ProviderError("主题缺少 known_at，不得声明已知时点或当前已采集快照。")
        if result["mode"] != "demo" and quality == "simulated":
            raise ProviderError("模拟主题不能装入真实或历史数据包。")
        if not isinstance(theme.get("evidence", []), list):
            raise ProviderError("theme.evidence 必须是数组。")
        theme.setdefault("evidence", [])
    calendar = result["calendar"]
    if any(not isinstance(x, str) or date_text(x) != x for x in calendar) or len(set(calendar)) != len(calendar):
        raise ProviderError("calendar 必须为不重复的 YYYY-MM-DD 交易日数组。")
    calendar.sort()
    if result["calendar_confirmed"] and not calendar:
        raise ProviderError("空交易日历不能标记为已确认。")
    evidence_rows = list(result["news"])
    for theme in result["themes"]:
        evidence_rows.extend(theme["evidence"])
    for item in evidence_rows:
        if not isinstance(item, dict) or not isinstance(item.get("title"), str):
            raise ProviderError("新闻/证据必须包含 title。")
        if item.get("url") is not None:
            if not isinstance(item["url"], str) or urlsplit(item["url"]).scheme not in {"http", "https"}:
                raise ProviderError("证据 URL 必须是 HTTP/HTTPS 链接。")
        timestamp(item.get("published_at"), "evidence.published_at", nullable=True)
        timestamp(item.get("observed_at"), "evidence.observed_at")
        if item.get("confidence") not in {"verified", "unverified", "simulated"}:
            raise ProviderError("证据 confidence 无效。")
        if result["mode"] != "demo" and item.get("confidence") == "simulated":
            raise ProviderError("模拟证据不能装入真实或历史数据包。")
    return result
