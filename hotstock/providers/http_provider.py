"""Read-only standard JSON/HTTP data boundary, independent of any other app."""
from __future__ import annotations

import json
from urllib import error, request

from .common import (
    MAX_RESPONSE_BYTES, ProviderError, bounded_int, endpoint_url, iso_now,
    validate_import_bundle,
)


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_json(url: str, *, timeout: int, headers: dict | None = None, body: dict | None = None) -> dict:
    """Do not forward authorization across redirects; do not print remote bodies."""
    encoded = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8") if body is not None else None
    safe_headers = {"Accept": "application/json", "User-Agent": "HotstockRadar/1.0"}
    if body is not None:
        safe_headers["Content-Type"] = "application/json"
    safe_headers.update(headers or {})
    req = request.Request(url, data=encoded, headers=safe_headers, method="POST" if body is not None else "GET")
    try:
        opener = request.build_opener(_NoRedirect())
        with opener.open(req, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except error.HTTPError as exc:
        raise ProviderError(f"数据源 HTTP {exc.code}；请核实地址、访问权限及供应商服务状态。重定向不会自动跟随。") from None
    except (error.URLError, TimeoutError, OSError, ValueError):
        raise ProviderError("数据源连接失败或超时；请检查网络、证书与服务状态。") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise ProviderError("数据源响应超过 8 MiB。")
    try:
        data = json.loads(raw.decode("utf-8-sig"), parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite")))
    except (ValueError, UnicodeError, RecursionError):
        raise ProviderError("数据源未返回合法 UTF-8 JSON；可能是登录页或错误页面。") from None
    if not isinstance(data, dict):
        raise ProviderError("数据源 JSON 顶层必须为对象。")
    return data


def fetch_http(options: dict) -> dict:
    token = options.get("token") or ""
    if not isinstance(token, str) or len(token) > 4096 or "\r" in token or "\n" in token:
        raise ProviderError("HTTP 数据源 Token 格式无效。")
    url = endpoint_url(options.get("base_url"), credentials=bool(token), allow_insecure=options.get("allow_insecure_http") is True)
    timeout = bounded_int(options, "timeout_seconds", 15, 1, 120)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    bundle = validate_import_bundle(request_json(url, timeout=timeout, headers=headers))
    # Preserve source's received_at and quote_time. A fresh HTTP fetch must not
    # turn an old quote into a fresh quote.
    bundle["warnings"].append(f"标准 HTTP 数据包于 {iso_now()} 读取；原始 source/as_of/received_at/quote_time 均保留，源数据新鲜度单独核验。")
    return bundle
