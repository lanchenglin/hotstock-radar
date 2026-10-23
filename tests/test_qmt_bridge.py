"""Contract tests with a synthetic SDK; not a claim of live MiniQMT access."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from datetime import datetime, timedelta

from hotstock.providers import ProviderError
from hotstock.providers.common import CN_TZ
from bridges.qmt_bridge import QmtCollector, create_app, epoch_millis

NOW = datetime(2026, 10, 20, 10, 30, tzinfo=CN_TZ)


def millis(dt):
    return int(dt.timestamp() * 1000)


class Frame:
    def __init__(self, rows):
        self.rows = rows

    def to_dict(self, orient):
        return self.rows


class MockXtData:
    def __init__(self):
        self.called = []
        self.tick_time = millis(NOW)
        self.minimum = 100
        self.prev_close = 10
        self.days = [NOW - timedelta(days=i) for i in range(25, -1, -1)]

    def get_trading_dates(self, market, **kwargs):
        self.called.append("get_trading_dates")
        return [millis(d.replace(hour=0, minute=0)) for d in self.days]

    def download_history_data(self, symbol, **kwargs):
        self.called.append("download_history_data")

    def get_market_data_ex(self, **kwargs):
        self.called.append("get_market_data_ex")
        assert kwargs["fill_data"] is False
        assert kwargs["dividend_type"] == "none"
        rows = [{"time": millis(day.replace(hour=0, minute=0)), "open": 10.0,
                 "high": 10.5, "low": 9.9, "close": 10.2, "volume": 100,
                 "amount": 102000, "suspendFlag": 0} for day in self.days]
        return {s: Frame(rows) for s in kwargs["stock_list"]}

    def get_full_tick(self, symbols):
        self.called.append("get_full_tick")
        return {s: {"time": self.tick_time, "lastPrice": 10.5, "lastClose": self.prev_close,
                    "open": 10.1, "high": 10.6, "low": 10.0, "volume": 123, "amount": 129150} for s in symbols}

    def get_instrument_detail(self, symbol, iscomplete):
        self.called.append("get_instrument_detail")
        return {"InstrumentName": "合成测试股", "PreClose": 10, "UpStopPrice": 12,
                "DownStopPrice": 8, "MinLimitOrderVolume": self.minimum, "VolumeMultiple": 1, "IsTrading": True}


class QmtTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "config.json"
        self.config = {
            "symbols": ["300001.SZ"], "history_days": 90,
            "themes": [{"id": "t", "name": "合成题材", "members": ["300001.SZ"], "confirmed": False, "evidence": []}],
            "security_rules": {},
        }
        self.save()
        self.sdk = MockXtData()
        self.collector = QmtCollector(self.path, xtdata=self.sdk, clock=lambda: NOW)

    def tearDown(self):
        self.tmp.cleanup()

    def save(self):
        self.path.write_text(json.dumps(self.config, ensure_ascii=False), encoding="utf-8")

    def test_milliseconds_and_unknown_time(self):
        self.assertEqual(epoch_millis(millis(NOW)), NOW)
        self.assertIsNone(epoch_millis(1720000000))
        self.assertIsNone(epoch_millis(None))

    def test_read_only_bundle_keeps_source_time_and_complete_history(self):
        result = self.collector.bundle()
        quote = result["quotes"][0]
        self.assertEqual(datetime.fromisoformat(quote["quote_time"]), NOW)
        self.assertEqual(quote["volume"], 12300)
        self.assertEqual(quote["limit_up"], 12)
        self.assertEqual(quote["min_buy_qty"], 100)
        self.assertIsNone(quote["buy_qty_step"])
        self.assertEqual(len(result["bars"]["300001.SZ"]), 25)
        self.assertTrue(all(row["date"] < "2026-10-20" for row in result["bars"]["300001.SZ"]))
        self.assertEqual(result["themes"][0]["membership_quality"], "unknown")
        self.assertEqual(set(self.sdk.called), {"get_trading_dates", "download_history_data", "get_market_data_ex", "get_full_tick", "get_instrument_detail"})

    def test_volume_multiple_never_becomes_buy_step(self):
        self.assertIsNone(self.collector.bundle()["quotes"][0]["buy_qty_step"])
        self.config["security_rules"]["300001.SZ"] = {"confirmed": True, "source_url": "https://www.szse.cn/lawrules/", "buy_qty_step": 100}
        self.save()
        self.assertEqual(self.collector.bundle()["quotes"][0]["buy_qty_step"], 100)

    def test_previous_close_mismatch_hides_limits(self):
        self.sdk.prev_close = 9
        quote = self.collector.bundle()["quotes"][0]
        self.assertIsNone(quote["limit_up"])
        self.assertIsNone(quote["limit_down"])

    def test_missing_timestamp_does_not_become_received_time(self):
        self.sdk.tick_time = None
        quote = self.collector.bundle()["quotes"][0]
        self.assertIsNone(quote["quote_time"])
        self.assertEqual(quote["time_quality"], "unknown")

    def test_theme_time_cannot_be_backfilled_and_changes_are_new(self):
        self.config["themes"][0]["known_at"] = "2000-01-01T00:00:00+08:00"
        self.save()
        first = self.collector.bundle()["themes"][0]["known_at"]
        self.assertEqual(first, NOW.isoformat())
        self.collector.clock = lambda: NOW + timedelta(minutes=1)
        self.assertEqual(self.collector.bundle()["themes"][0]["known_at"], first)
        self.config["themes"][0]["name"] = "主题关系重新确认"
        self.save()
        self.assertNotEqual(self.collector.bundle()["themes"][0]["known_at"], first)

    def test_verified_theme_requires_explicit_evidence(self):
        self.config["themes"][0]["confirmed"] = True
        self.save()
        self.assertEqual(self.collector.bundle()["themes"][0]["membership_quality"], "unknown")
        self.config["themes"][0]["evidence"] = [{"title": "人工核查测试证据", "url": "https://www.cninfo.com.cn/", "verified": True, "published_at": None, "symbols": ["300001.SZ"]}]
        self.save()
        self.assertEqual(self.collector.bundle()["themes"][0]["membership_quality"], "point_in_time")

    def test_evidence_does_not_automatically_cover_all_members(self):
        self.config["symbols"].append("600001.SH")
        self.config["themes"][0]["members"].append("600001.SH")
        self.config["themes"][0]["confirmed"] = True
        self.config["themes"][0]["evidence"] = [{"title": "只核实了一家公司", "url": "https://www.cninfo.com.cn/", "verified": True, "symbols": ["300001.SZ"]}]
        self.save()
        result = self.collector.bundle()
        self.assertEqual(result["themes"][0]["membership_quality"], "unknown")
        self.assertEqual(result["themes"][0]["evidence"][0]["symbols"], ["300001.SZ"])

    def test_bridge_auth_and_no_mutation_endpoints(self):
        from fastapi.testclient import TestClient
        app = create_app(token="test-only-token-123456", collector=self.collector)
        client = TestClient(app)
        self.assertEqual(client.get("/bundle").status_code, 401)
        self.assertEqual(client.get("/bundle", headers={"Authorization": "Bearer test-only-token-123456"}).status_code, 200)
        self.assertEqual(client.post("/bundle").status_code, 405)
        self.assertEqual({route.path for route in app.routes}, {"/health", "/bundle"})
        self.assertEqual(client.get("/health").json()["terminal_connected"], "not_probed")

    def test_missing_token_prevents_startup(self):
        with self.assertRaises(ProviderError):
            create_app(token="")

    def test_missing_sdk_is_a_failure_not_demo(self):
        from unittest.mock import patch
        collector = QmtCollector(self.path, clock=lambda: NOW)
        with patch("bridges.qmt_bridge.importlib.import_module", side_effect=ImportError):
            with self.assertRaisesRegex(ProviderError, "XtQuant"):
                collector.bundle()


if __name__ == "__main__":
    unittest.main()
