"""Data provenance, unit conversion, real HTTP parsing and failure isolation."""
from __future__ import annotations

import copy
import json
import subprocess
import threading
import unittest
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from hotstock.providers import ProviderError, fetch_bundle, validate_import_bundle
from hotstock.providers.akshare_provider import collect_akshare, fetch_akshare
from hotstock.providers.common import CN_TZ, endpoint_url, symbol_of
from hotstock.providers.http_provider import fetch_http
from hotstock.providers.tushare_provider import TushareClient, fetch_tushare

NOW = datetime(2026, 10, 20, 20, 0, tzinfo=CN_TZ)
STAMP = NOW.isoformat()


def bundle_fixture():
    return {
        "schema_version": 1, "source": "test-provider", "mode": "historical",
        "as_of": STAMP, "received_at": STAMP,
        "quotes": [{"symbol": "300001.SZ", "name": "测试股", "price": 10.0,
                    "prev_close": 9.5, "open": 9.6, "high": 10.1, "low": 9.5,
                    "volume": 10000, "amount": 100000, "quote_time": None,
                    "received_at": STAMP, "time_quality": "unknown", "is_suspended": None,
                    "limit_up": None, "limit_down": None, "board": "CHINEXT",
                    "min_buy_qty": None, "buy_qty_step": None}],
        "bars": {"300001.SZ": [{"date": "2026-10-19", "open": 9.4, "high": 9.6,
                    "low": 9.3, "close": 9.5, "volume": 10000, "amount": 95000}]},
        "themes": [{"id": "test", "name": "测试题材", "members": ["300001.SZ"],
                    "known_at": STAMP, "membership_quality": "current_snapshot", "evidence": []}],
        "calendar": ["2026-10-19", "2026-10-20", "2026-10-21"], "calendar_confirmed": True,
        "news": [], "warnings": [],
        "capabilities": {"quotes": True, "themes": True, "history": True, "calendar": True, "evidence": False},
    }


class Frame:
    def __init__(self, rows):
        self.rows = rows

    def to_dict(self, orient):
        assert orient == "records"
        return copy.deepcopy(self.rows)


class FakeAK:
    def __init__(self):
        self.history_args = []

    def stock_zh_a_spot_em(self):
        return Frame([
            {"代码": "300001", "名称": "甲股", "最新价": 10.0, "昨收": 9.5, "今开": 9.6,
             "最高": 10.1, "最低": 9.5, "成交量": 123, "成交额": 123000},
            {"代码": "600001", "名称": "乙股", "最新价": 6.0, "昨收": 5.9, "今开": 5.9,
             "最高": 6.1, "最低": 5.8, "成交量": 100, "成交额": 60000},
        ])

    def stock_board_concept_name_em(self):
        return Frame([{"板块名称": "机器人", "板块代码": "BK0001", "涨跌幅": 3.2}])

    def stock_board_concept_cons_em(self, symbol):
        return Frame([{"代码": "300001"}, {"代码": "600001"}])

    def stock_zh_a_hist(self, **kwargs):
        self.history_args.append(kwargs)
        return Frame([
            {"日期": "2026-10-19", "开盘": 9.4, "最高": 9.6, "最低": 9.3, "收盘": 9.5, "成交量": 80, "成交额": 76000},
            {"日期": "2026-10-20", "开盘": 9.6, "最高": 10.1, "最低": 9.5, "收盘": 10.0, "成交量": 123, "成交额": 123000},
        ])

    def tool_trade_date_hist_sina(self):
        return Frame([{"trade_date": "2026-10-19"}, {"trade_date": "2026-10-20"}, {"trade_date": "2026-10-21"}])


class FakeTushare:
    def call(self, name, params, fields):
        if name == "trade_cal":
            return [{"cal_date": "20261019", "is_open": 1}, {"cal_date": "20261020", "is_open": 1}, {"cal_date": "20261021", "is_open": 1}]
        if name == "stock_basic":
            raise ProviderError("股票基础信息权限不足。")
        if name == "daily":
            return [
                {"ts_code": "300001.SZ", "trade_date": "20261020", "open": 9.6, "high": 10.1, "low": 9.5, "close": 10.0,
                 "pre_close": 9.5, "vol": 123, "amount": 123.4},
                {"ts_code": "300001.SZ", "trade_date": "20261019", "open": 9.4, "high": 9.6, "low": 9.3, "close": 9.5,
                 "pre_close": 9.4, "vol": 80, "amount": 76},
            ]
        raise ProviderError("测试接口无权限。")


class ProviderValidationTests(unittest.TestCase):
    def test_valid_bundle_keeps_unknown_timestamps_and_is_a_copy(self):
        source = bundle_fixture()
        result = validate_import_bundle(source)
        self.assertIsNone(result["quotes"][0]["quote_time"])
        result["warnings"].append("new")
        self.assertEqual(source["warnings"], [])

    def test_invalid_quality_cannot_claim_timestamp(self):
        source = bundle_fixture()
        source["quotes"][0]["time_quality"] = "exchange"
        with self.assertRaisesRegex(ProviderError, "缺少行情时间"):
            validate_import_bundle(source)

    def test_timezone_is_required(self):
        source = bundle_fixture()
        source["received_at"] = "2026-10-20T20:00:00"
        with self.assertRaisesRegex(ProviderError, "时区"):
            validate_import_bundle(source)

    def test_demo_labels_cannot_be_promoted(self):
        source = bundle_fixture()
        source["quotes"][0]["quote_time"] = STAMP
        source["quotes"][0]["time_quality"] = "simulated"
        with self.assertRaisesRegex(ProviderError, "模拟行情"):
            validate_import_bundle(source)

    def test_non_finite_prices_rejected(self):
        source = bundle_fixture()
        source["quotes"][0]["price"] = float("nan")
        with self.assertRaises(ProviderError):
            validate_import_bundle(source)

    def test_future_bar_rejected(self):
        source = bundle_fixture()
        source["bars"]["300001.SZ"][0]["date"] = "2026-10-21"
        with self.assertRaisesRegex(ProviderError, "晚于"):
            validate_import_bundle(source)

    def test_duplicate_quote_rejected(self):
        source = bundle_fixture()
        source["quotes"].append(copy.deepcopy(source["quotes"][0]))
        with self.assertRaisesRegex(ProviderError, "重复"):
            validate_import_bundle(source)

    def test_unknown_provider_never_falls_back_to_demo(self):
        with self.assertRaises(ProviderError):
            fetch_bundle("unavailable", {})

    def test_symbol_normalization(self):
        self.assertEqual(symbol_of("000001"), "000001.SZ")
        self.assertEqual(symbol_of("SH.600001"), "600001.SH")
        self.assertEqual(symbol_of("920001"), "920001.BJ")

    def test_token_url_cannot_be_silently_downgraded(self):
        with self.assertRaisesRegex(ProviderError, "HTTPS"):
            endpoint_url("http://api.tushare.pro", credentials=True)
        self.assertEqual(endpoint_url("http://api.tushare.pro", credentials=True, allow_insecure=True), "http://api.tushare.pro")


class AdapterTests(unittest.TestCase):
    @patch("hotstock.providers.akshare_provider.now_cn", return_value=NOW)
    @patch("hotstock.providers.akshare_provider.iso_now", return_value=STAMP)
    def test_akshare_units_history_and_provenance(self, *mocks):
        fake = FakeAK()
        result = collect_akshare({"theme_names": ["机器人"], "max_symbols": 1}, fake)
        quote = next(q for q in result["quotes"] if q["symbol"] == "300001.SZ")
        self.assertEqual(quote["volume"], 12300)
        self.assertEqual(quote["amount"], 123000)
        self.assertIsNone(quote["quote_time"])
        self.assertIsNone(quote["limit_up"])
        self.assertEqual(result["themes"][0]["membership_quality"], "current_snapshot")
        self.assertEqual(len(result["themes"][0]["members"]), 2)
        self.assertEqual(result["bars"]["300001.SZ"][0]["volume"], 8000)
        self.assertEqual([x["date"] for x in result["bars"]["300001.SZ"]], ["2026-10-19"])
        self.assertEqual(fake.history_args[0]["adjust"], "")
        self.assertEqual(fake.history_args[0]["end_date"], "20261019")
        self.assertTrue(any("仅有 1 根" in x for x in result["warnings"]))

    def test_upstream_error_does_not_leak_secret(self):
        fake = FakeAK()
        fake.stock_zh_a_spot_em = lambda: (_ for _ in ()).throw(RuntimeError("secret-key=do-not-display"))
        with self.assertRaises(ProviderError) as caught:
            collect_akshare({}, fake)
        self.assertNotIn("do-not-display", str(caught.exception))
        self.assertIn("RuntimeError", str(caught.exception))

    @patch("hotstock.providers.akshare_provider.subprocess.run", side_effect=subprocess.TimeoutExpired("worker", 5))
    def test_akshare_hard_timeout_is_visible(self, mocked):
        with self.assertRaisesRegex(ProviderError, "已停止采集进程"):
            fetch_akshare({"collection_timeout_seconds": 5})

    @patch("hotstock.providers.tushare_provider.now_cn", return_value=NOW)
    def test_tushare_is_historical_and_converts_units(self, mocked):
        result = fetch_tushare({"symbols": ["300001.SZ"]}, FakeTushare())
        self.assertEqual(result["mode"], "historical")
        self.assertEqual(result["quotes"][0]["volume"], 12300)
        self.assertEqual(result["quotes"][0]["amount"], 123400)
        self.assertEqual(result["bars"]["300001.SZ"][0]["date"], "2026-10-19")
        self.assertFalse(result["capabilities"]["themes"])
        self.assertTrue(any("没有可用主题" in x for x in result["warnings"]))

    def test_tushare_requires_token(self):
        with self.assertRaisesRegex(ProviderError, "Token"):
            TushareClient({})

    @patch("hotstock.providers.tushare_provider.request_json", return_value={"code": 2002, "msg": "secret-token-do-not-display"})
    def test_tushare_permission_error_is_sanitized(self, mocked):
        client = TushareClient({"token": "test-token"})
        with self.assertRaises(ProviderError) as caught:
            client.call("daily", {}, "")
        self.assertIn("2002", str(caught.exception))
        self.assertNotIn("secret-token", str(caught.exception))


class HttpAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seen = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                cls.seen.append((self.path, self.headers.get("Authorization")))
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "/bundle")
                    self.end_headers()
                    return
                raw = json.dumps(bundle_fixture()).encode("utf-8") if self.path == "/bundle" else b"<html>login</html>"
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, format, *args):
                pass

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_actual_http_bundle_preserves_market_timestamps(self):
        result = fetch_http({"base_url": self.url + "/bundle", "token": "local-test"})
        self.assertEqual(result["received_at"], STAMP)
        self.assertEqual(result["source"], "test-provider")
        self.assertIsNone(result["quotes"][0]["quote_time"])
        self.assertIn(("/bundle", "Bearer local-test"), self.seen)

    def test_html_response_is_not_data(self):
        with self.assertRaisesRegex(ProviderError, "JSON"):
            fetch_http({"base_url": self.url + "/login"})

    def test_redirect_does_not_forward_auth(self):
        before = len(self.seen)
        with self.assertRaisesRegex(ProviderError, "302"):
            fetch_http({"base_url": self.url + "/redirect", "token": "local-test"})
        self.assertEqual(self.seen[before:], [("/redirect", "Bearer local-test")])


if __name__ == "__main__":
    unittest.main()
