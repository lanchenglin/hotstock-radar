from copy import deepcopy
from datetime import datetime, timedelta
import json

import pytest
from fastapi.testclient import TestClient

from hotstock.app import create_app
from hotstock.demo import demo_bundle
from hotstock.providers import ProviderError
from hotstock.timeutil import SHANGHAI


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path, start_worker=False)) as c:
        yield c


def trade(side="buy", quantity=100, **overrides):
    result = {"symbol": "DEMO001", "name": "模拟·精密结构", "side": side,
              "quantity": quantity, "price": 20, "executed_at": "2026-10-19T10:00:00+08:00", "confirmed": True}
    result.update(overrides)
    return result


def test_initial_demo_is_explicit_and_does_not_place_orders(client):
    d = client.get("/api/dashboard").json()
    assert d["mode"] == "demo"
    assert d["analysis"]["candidates"]
    assert all(not c["can_execute"] for c in d["analysis"]["candidates"])
    assert d["positions"] == []
    assert client.get("/api/trades").json()["items"] == []
    assert client.post("/api/orders", json={}).status_code == 404


def test_required_key_and_cross_origin(tmp_path):
    with TestClient(create_app(tmp_path, start_worker=False, api_key="test-private-key")) as c:
        assert c.get("/api/health").status_code == 200
        assert c.get("/api/dashboard").status_code == 401
        headers = {"X-API-Key": "test-private-key"}
        assert c.get("/api/dashboard", headers=headers).status_code == 200
        assert c.put("/api/config", json={"auto_refresh": True}, headers={**headers, "Origin": "https://foreign.example"}).status_code == 403


def test_secrets_are_not_exported(client):
    assert client.put("/api/config", json={"provider_options": {"token": "SECRET_DATA_123"},
                "notifications": {"token": "SECRET_NOTIFY_456", "webhook_url": "https://example.invalid/private-secret-path"}}).status_code == 200
    data = client.get("/api/export")
    assert "SECRET_DATA_123" not in data.text
    assert "SECRET_NOTIFY_456" not in data.text
    assert "private-secret-path" not in data.text
    assert data.json()["config"]["provider_options"]["has_token"]


def test_failed_real_source_does_not_fallback_to_demo(client, monkeypatch):
    def fail(*args, **kwargs):
        raise ProviderError("synthetic source outage")
    monkeypatch.setattr("hotstock.providers.fetch_bundle", fail)
    r = client.post("/api/refresh", json={"provider": "http"})
    assert r.status_code == 502
    d = client.get("/api/dashboard").json()
    assert d["mode"] == "live"
    assert not d["analysis"]["candidates"]
    assert d["data_status"]["state"] == "error"
    assert d["account"]["cash"] is None
    client.post("/api/refresh", json={"provider": "demo"})
    assert client.get("/api/dashboard").json()["mode"] == "demo"


def test_demo_cannot_be_relabelled_live(client):
    bundle = demo_bundle()
    bundle["mode"] = "live"
    assert client.post("/api/import", json=bundle).status_code == 422


def test_import_duplicate_and_invalid_ohlc_rejected(client):
    bundle = demo_bundle()
    bundle["quotes"].append(deepcopy(bundle["quotes"][0]))
    assert client.post("/api/import", json=bundle).status_code == 422
    bundle = demo_bundle()
    bundle["bars"]["DEMO001"][0]["high"] = 0.1
    assert client.post("/api/import", json=bundle).status_code == 422


def test_oversell_is_atomic_and_same_day_is_not_sellable(client):
    assert client.post("/api/trades", json=trade(executed_at="2026-10-20T10:00:00+08:00")).status_code == 200
    before = client.get("/api/config").json()["account"]["cash"]
    assert client.post("/api/trades", json=trade("sell", executed_at="2026-10-20T10:20:00+08:00")).status_code == 422
    assert client.get("/api/config").json()["account"]["cash"] == before
    assert len(client.get("/api/trades").json()["items"]) == 1
    assert client.get("/api/dashboard").json()["positions"][0]["sellable_quantity"] == 0


def test_fifo_sell_and_cash_recording(client):
    client.post("/api/trades", json=trade(quantity=200, price=20))
    client.post("/api/trades", json=trade(quantity=100, price=22, executed_at="2026-10-20T10:00:00+08:00"))
    r = client.post("/api/trades", json=trade("sell", quantity=100, price=23, executed_at="2026-10-20T10:15:00+08:00"))
    assert r.status_code == 200
    p = r.json()["positions"][0]
    assert p["quantity"] == 200
    assert p["avg_cost"] == 21
    assert p["sellable_quantity"] == 100
    assert r.json()["trade"]["execution"] == "record_only"


def test_timezone_normalization_and_future_trade(client):
    r = client.post("/api/trades", json=trade(executed_at="2026-10-19T02:00:00Z"))
    assert r.status_code == 200
    assert r.json()["trade"]["executed_at"] == "2026-10-19T10:00:00+08:00"
    assert client.post("/api/trades", json=trade(executed_at="2026-10-21T10:00:00+08:00")).status_code == 422


def test_no_confirmation_no_trade(client):
    value = trade()
    value["confirmed"] = False
    assert client.post("/api/trades", json=value).status_code == 422


def test_workspace_isolation(client):
    client.post("/api/trades", json=trade())
    client.put("/api/config", json={"provider": "http"})
    d = client.get("/api/dashboard").json()
    assert d["mode"] == "live"
    assert d["positions"] == []
    assert d["account"]["cash"] is None
    assert client.post("/api/trades", json=trade()).status_code == 422
    client.put("/api/config", json={"provider": "demo"})
    assert client.get("/api/dashboard").json()["positions"][0]["quantity"] == 100


def test_plan_prices_frozen_across_refresh_and_config(client):
    candidates = client.get("/api/dashboard").json()["analysis"]["candidates"]
    selected = next(c for c in candidates if c["status"] in {"watch", "ready"})
    r = client.post("/api/plans", json={"candidate_id": selected["id"]})
    assert r.status_code == 200
    plan = r.json()
    client.put("/api/config", json={"risk": {"max_chase_pct": 4}})
    bundle = demo_bundle()
    for q in bundle["quotes"]:
        if q["symbol"] == selected["symbol"]:
            q["price"] = round(plan["entry_max"] + 0.2, 2)
            q["high"] = max(q["high"], q["price"])
    assert client.post("/api/import", json=bundle).status_code == 200
    current = next(p for p in client.get("/api/plans").json()["items"] if p["id"] == plan["id"])
    assert current["entry_max"] == plan["entry_max"]
    assert current["stop_price"] == plan["stop_price"]
    assert current["status"] == "expired"
    client.post("/api/refresh", json={"provider": "demo"})
    current = next(p for p in client.get("/api/plans").json()["items"] if p["id"] == plan["id"])
    assert current["status"] == "expired"


def test_unknown_account_no_suggested_quantity(client):
    client.put("/api/config", json={"account": {"equity": None, "cash": None}})
    candidates = client.get("/api/dashboard").json()["analysis"]["candidates"]
    assert all(c["sizing"]["quantity"] is None for c in candidates)


def test_alert_does_not_close_position_or_repeat_events(client):
    client.post("/api/trades", json=trade(price=30))
    before = client.get("/api/dashboard").json()
    assert any(a["code"] == "PRICE_STOP" for a in before["alerts"])
    event_count = len(client.get("/api/events", params={"limit": 1000}).json()["items"])
    for _ in range(3):
        current = client.get("/api/dashboard").json()
        assert current["positions"][0]["quantity"] == 100
        assert all(a["execution"] == "not_executed" for a in current["alerts"])
    assert len(client.get("/api/events", params={"limit": 1000}).json()["items"]) == event_count


def test_records_persist_across_restart(tmp_path):
    with TestClient(create_app(tmp_path, start_worker=False)) as c:
        c.post("/api/trades", json=trade())
    with TestClient(create_app(tmp_path, start_worker=False)) as c:
        assert c.get("/api/dashboard").json()["positions"][0]["quantity"] == 100
        assert len(c.get("/api/trades").json()["items"]) == 1


def test_no_risky_endpoint_and_bounded_settings(client):
    assert client.put("/api/config", json={"refresh_seconds": 1}).status_code == 422
    assert client.put("/api/config", json={"risk": {"risk_per_trade_pct": 90}}).status_code == 422
    assert client.put("/api/config", json={"provider_options": {"base_url": "file:///etc/passwd"}}).status_code == 422
    assert client.put("/api/config", json={"provider_options": {"symbols": "300115.SZ"}}).status_code == 422
    assert client.post("/api/notifications/test").status_code == 422


def historical_fixture():
    # Synthetic SDK-like values for boundary checks; never external market data.
    encoded = json.dumps(demo_bundle())
    for i in range(1, 9):
        encoded = encoded.replace(f"DEMO{i:03d}", f"300{i:03d}.SZ")
    bundle = json.loads(encoded)
    bundle.update(mode="historical", source="synthetic-integration-fixture")
    for quote in bundle["quotes"]:
        quote["time_quality"] = "provider"
    evidence = list(bundle["news"])
    for theme in bundle["themes"]:
        theme["membership_quality"] = "point_in_time"
        evidence.extend(theme["evidence"])
    for item in evidence:
        item["confidence"] = "unverified"
        if not item.get("url"):
            item["url"] = None
    return bundle


@pytest.mark.parametrize("mode", ["demo", "historical"])
def test_stateful_research_clock_cannot_rewind(client, mode):
    bundle = demo_bundle() if mode == "demo" else historical_fixture()
    first = client.post("/api/import", json=bundle)
    assert first.status_code == 200, first.text
    symbol = "DEMO001" if mode == "demo" else "300001.SZ"
    assert client.post("/api/trades", json=trade(symbol=symbol, executed_at="2026-10-20T09:45:00+08:00")).status_code == 200
    old = deepcopy(bundle)
    old["as_of"] = "2026-10-19T10:30:00+08:00"
    response = client.post("/api/import", json=old)
    assert response.status_code == 409
    current = client.get("/api/dashboard").json()
    assert current["as_of"] == bundle["as_of"]
    assert current["positions"][0]["quantity"] == 100


@pytest.mark.parametrize("field", ["as_of", "quote_time", "quote_received_at"])
def test_non_demo_time_cannot_be_future(client, field):
    bundle = historical_fixture()
    future = (datetime.now(SHANGHAI) + timedelta(days=1)).isoformat()
    if field == "as_of":
        bundle["as_of"] = future
    else:
        bundle["quotes"][0]["quote_time" if field == "quote_time" else "received_at"] = future
    response = client.post("/api/import", json=bundle)
    assert response.status_code == 422
    assert "未来数据" in response.json()["detail"]
    assert client.get("/api/dashboard").json()["mode"] == "demo"


def test_import_rejects_duplicate_and_future_bars(client):
    for change in ("duplicate", "future"):
        bundle = demo_bundle()
        if change == "duplicate":
            bundle["bars"]["DEMO001"].append(deepcopy(bundle["bars"]["DEMO001"][0]))
        else:
            bundle["bars"]["DEMO001"][0]["date"] = "2026-10-21"
        assert client.post("/api/import", json=bundle).status_code == 422


def test_multiple_theme_memberships_reach_sizing_and_positions(client):
    bundle = demo_bundle()
    extra = deepcopy(bundle["themes"][0])
    extra.update(id="shared-theme", name="模拟·共同题材", members=["DEMO001", "DEMO006"])
    bundle["themes"].append(extra)
    assert client.post("/api/import", json=bundle).status_code == 200
    assert client.post("/api/trades", json=trade()).status_code == 200
    data = client.get("/api/dashboard").json()
    expected = {t["id"] for t in bundle["themes"] if "DEMO001" in t["members"]}
    assert set(data["positions"][0]["theme_ids"]) == expected
    for candidate in data["analysis"]["candidates"]:
        if candidate["symbol"] == "DEMO001":
            assert set(candidate["theme_ids"]) == expected


def test_source_options_preserved_and_effective_interval(client):
    r = client.put("/api/config", json={"provider": "akshare", "refresh_seconds": 60,
        "provider_options": {"max_themes": 4, "collection_timeout_seconds": 120}})
    assert r.status_code == 200
    assert r.json()["provider_options"]["max_themes"] == 4
    assert client.get("/api/dashboard").json()["scheduler"]["effective_refresh_seconds"] == 600
    assert client.put("/api/config", json={"provider_options": {"max_themes": 1.5}}).status_code == 422
    assert client.put("/api/config", json={"notifications": {"webhook_url": []}}).status_code == 422


def test_provider_credentials_are_not_reused_for_another_source(client, monkeypatch):
    r = client.put("/api/config", json={"provider": "tushare", "provider_options": {
        "token": "TUSHARE_TEST_SECRET", "base_url": "https://api.tushare.pro"}})
    assert r.status_code == 200
    captured = {}
    def fail(provider, options):
        captured.update(provider=provider, options=deepcopy(options))
        raise ProviderError("synthetic source outage")
    monkeypatch.setattr("hotstock.providers.fetch_bundle", fail)
    assert client.post("/api/refresh", json={"provider": "http"}).status_code == 502
    assert captured["provider"] == "http"
    assert not captured["options"].get("token")
    assert not captured["options"].get("base_url")
    restored = client.put("/api/config", json={"provider": "tushare"}).json()
    assert restored["provider_options"]["has_token"]
    assert restored["provider_options"]["base_url"] == "https://api.tushare.pro"
    assert "TUSHARE_TEST_SECRET" not in client.get("/api/export").text
