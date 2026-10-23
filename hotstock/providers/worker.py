"""Bounded isolated worker for optional AKShare requests (no trading imports)."""
from __future__ import annotations

import contextlib
import io
import json
import sys

from .common import ProviderError, bounded_int


def main() -> None:
    try:
        options = json.loads(sys.stdin.read(65536))
        if not isinstance(options, dict):
            raise ProviderError("AKShare 采集配置格式错误。")
        timeout = bounded_int(options, "timeout_seconds", 15, 1, 120)
        try:
            import requests
        except ImportError:
            raise ProviderError("尚未安装 AKShare 可选依赖；在本软件环境执行 pip install akshare 后再采集。") from None
        original = requests.sessions.Session.request

        def bounded_request(session, method, url, **kwargs):
            kwargs["timeout"] = timeout
            return original(session, method, url, **kwargs)

        # This change is confined to a disposable child process. The web server
        # and every other provider retain their original HTTP behavior.
        requests.sessions.Session.request = bounded_request
        from .akshare_provider import collect_akshare
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            bundle = collect_akshare(options)
        response = {"ok": True, "bundle": bundle}
    except ProviderError as error:
        response = {"ok": False, "error": str(error)}
    except Exception as error:
        response = {"ok": False, "error": f"AKShare 采集进程异常（{type(error).__name__}）。"}
    sys.stdout.write(json.dumps(response, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
