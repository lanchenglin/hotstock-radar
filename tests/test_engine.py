"""Behavior tests for event timing, frozen plans, T+1 and risk budget arithmetic.

Calendars below are explicit test fixtures; they are not market-calendar data.
"""

import copy
import unittest
from datetime import date, timedelta

from hotstock.engine import analyze_market, evaluate_plan, evaluate_position, sellable_quantity, size_position


NOW = "2026-10-20T10:30:00+08:00"


def market_fixture(mode="live"):
    start, end = date(2026, 8, 20), date(2026, 10, 23)
    calendar = []
    day = start
    while day <= end:
        if day.weekday() < 5 and not date(2026, 10, 1) <= day <= date(2026, 10, 9):
            calendar.append(day.isoformat())
        day += timedelta(days=1)
    history_days = [day for day in calendar if day < "2026-10-20"][-25:]
    bars = [{"date": day, "open": 19.7, "high": 20.0, "low": 19.5, "close": 19.75,
             "volume": 1000000, "amount": 19750000} for day in history_days]
    quotes = []
    for symbol, name in (("300001.SZ", "测试甲"), ("300002.SZ", "测试乙")):
        quotes.append({"symbol": symbol, "name": name, "price": 20.15, "prev_close": 19.75,
                       "open": 19.8, "high": 20.2, "low": 19.7, "volume": 600000, "amount": 12000000,
                       "quote_time": NOW, "received_at": NOW, "time_quality": "simulated" if mode == "demo" else "provider",
                       "is_suspended": False, "limit_up": 23.7, "limit_down": 15.8,
                       "board": "CHINEXT", "min_buy_qty": 100, "buy_qty_step": 100})
    return {"schema_version": 1, "source": "test-fixture", "mode": mode, "as_of": NOW,
            "received_at": NOW, "quotes": quotes, "bars": {quote["symbol"]: copy.deepcopy(bars) for quote in quotes},
            "themes": [{"id": "test_theme", "name": "测试题材", "members": [quote["symbol"] for quote in quotes],
                        "known_at": "2026-10-19T18:00:00+08:00", "membership_quality": "simulated" if mode == "demo" else "point_in_time", "evidence": []}],
            "calendar": calendar, "calendar_confirmed": mode != "demo", "warnings": []}


def first_plan(bundle=None):
    bundle = bundle or market_fixture()
    result = analyze_market(bundle, {}, NOW)
    return next(plan for plan in result["candidates"] if plan["symbol"] == "300001.SZ" and plan["strategy"] == "expansion")


def advance_clock(bundle, now):
    bundle["as_of"] = bundle["received_at"] = now
    for quote in bundle["quotes"]:
        quote["quote_time"] = quote["received_at"] = now


class MarketQualityTests(unittest.TestCase):
    def assertBlocked(self, bundle, text):
        plan = first_plan(bundle)
        self.assertEqual(plan["status"], "blocked")
        self.assertFalse(plan["can_execute"])
        self.assertTrue(any(text in issue for issue in plan["blockers"]), plan["blockers"])

    def test_complete_current_data_can_confirm_a_research_entry(self):
        plan = first_plan()
        self.assertEqual(plan["status"], "ready")
        self.assertTrue(plan["can_execute"])
        self.assertLess(plan["entry_min"], 20.15)
        self.assertGreater(plan["entry_max"], 20.15)
        self.assertFalse(plan["score_is_probability"])

    def test_demo_can_show_ready_but_never_execute(self):
        plan = first_plan(market_fixture("demo"))
        self.assertEqual(plan["status"], "ready")
        self.assertFalse(plan["can_execute"])

    def test_missing_quote_time_cannot_be_replaced_with_received_at(self):
        bundle = market_fixture()
        bundle["quotes"][0]["quote_time"] = None
        self.assertBlocked(bundle, "quote_time")

    def test_unknown_timestamps_preserve_reference_metrics_without_fake_zero(self):
        bundle = market_fixture()
        for quote in bundle["quotes"]:
            quote["quote_time"] = None
            quote["time_quality"] = "unknown"
        theme = analyze_market(bundle, {}, NOW)["themes"][0]
        self.assertIsNone(theme["avg_change_pct"])
        self.assertIsNone(theme["up_ratio"])
        self.assertIsNone(theme["heat_score"])
        self.assertTrue(theme["observed_only"])
        self.assertEqual(theme["observed_member_count"], 2)
        self.assertGreater(theme["observed_avg_change_pct"], 2)
        self.assertEqual(theme["observed_up_ratio"], 1)
        self.assertFalse(theme["entry_allowed"])

    def test_naive_time_and_unknown_time_quality_are_rejected(self):
        for key, value, expected in (("quote_time", "2026-10-20T10:30:00", "quote_time"), ("time_quality", "unknown", "时间来源")):
            with self.subTest(key=key):
                bundle = market_fixture()
                bundle["quotes"][0][key] = value
                self.assertBlocked(bundle, expected)

    def test_previous_day_quote_rejected_even_with_huge_age_allowance(self):
        bundle = market_fixture()
        bundle["quotes"][0]["quote_time"] = "2026-10-19T15:00:00+08:00"
        result = analyze_market(bundle, {"max_quote_age_seconds": 9999999, "strict_data_quality": False}, NOW)
        plan = next(plan for plan in result["candidates"] if plan["symbol"] == "300001.SZ")
        self.assertFalse(plan["can_execute"])
        self.assertIn("行情不是评估当日的数据", plan["blockers"])

    def test_stale_and_future_quotes_cannot_trigger(self):
        for timestamp, expected in (("2026-10-20T10:20:00+08:00", "新鲜度"), ("2026-10-20T10:31:00+08:00", "未来信息")):
            with self.subTest(timestamp=timestamp):
                bundle = market_fixture()
                bundle["quotes"][0]["quote_time"] = timestamp
                self.assertBlocked(bundle, expected)

    def test_future_or_unfinished_daily_bar_never_enters_signal_prices(self):
        for extra_day in ("2026-10-20", "2026-10-21"):
            with self.subTest(extra_day=extra_day):
                bundle = market_fixture()
                extra = copy.deepcopy(bundle["bars"]["300001.SZ"][-1])
                extra.update(date=extra_day, high=100, close=99)
                bundle["bars"]["300001.SZ"].append(extra)
                plan = first_plan(bundle)
                self.assertEqual(plan["status"], "blocked")
                self.assertIsNone(plan["entry_min"])
                self.assertTrue(any("未来" in issue or "未完成" in issue for issue in plan["blockers"]))

    def test_missing_latest_session_and_duplicate_bar_are_rejected(self):
        bundle = market_fixture()
        bundle["bars"]["300001.SZ"].pop()
        self.assertBlocked(bundle, "未更新")
        bundle = market_fixture()
        bundle["bars"]["300001.SZ"].append(copy.deepcopy(bundle["bars"]["300001.SZ"][-1]))
        self.assertBlocked(bundle, "重复")

    def test_unconfirmed_calendar_and_missing_security_rules_are_rejected(self):
        bundle = market_fixture()
        bundle["calendar_confirmed"] = False
        self.assertBlocked(bundle, "日历未经确认")
        bundle = market_fixture()
        bundle["quotes"][0]["limit_up"] = None
        self.assertBlocked(bundle, "涨跌停")

    def test_future_theme_membership_and_future_evidence_are_rejected(self):
        bundle = market_fixture()
        bundle["themes"][0]["known_at"] = "2026-10-20T12:00:00+08:00"
        self.assertBlocked(bundle, "事后补标签")
        bundle = market_fixture()
        bundle["themes"][0]["evidence"] = [{"title": "未来发布的新闻", "published_at": "2026-10-20T12:00:00+08:00", "confidence": "verified"}]
        self.assertBlocked(bundle, "证据时间")

    def test_weak_theme_is_watch_even_when_stock_price_hits_trigger(self):
        bundle = market_fixture()
        bundle["quotes"][1]["price"] = 19.1
        plan = first_plan(bundle)
        self.assertEqual(plan["status"], "watch")
        self.assertFalse(plan["can_execute"])

    def test_no_ask_and_suspension_prevent_readiness(self):
        for field, value in (("ask1_volume", 0), ("is_suspended", True)):
            with self.subTest(field=field):
                bundle = market_fixture()
                bundle["quotes"][0][field] = value
                self.assertFalse(first_plan(bundle)["can_execute"])


class FrozenPlanTests(unittest.TestCase):
    def test_price_does_not_move_with_market_and_missed_entry_stays_expired(self):
        bundle = market_fixture()
        saved = first_plan(bundle)
        original = copy.deepcopy(saved)
        later = "2026-10-20T10:32:00+08:00"
        advance_clock(bundle, later)
        bundle["quotes"][0]["price"] = saved["entry_max"] + 1
        evaluated = evaluate_plan(saved, bundle, {}, later)
        self.assertEqual(evaluated["status"], "expired")
        for key in ("entry_min", "entry_max", "stop_price", "created_at", "id"):
            self.assertEqual(evaluated[key], original[key])
        self.assertEqual(saved, original)
        bundle["quotes"][0]["price"] = 20.15
        self.assertEqual(evaluate_plan(evaluated, bundle, {}, later)["status"], "expired")

    def test_plan_expiration_uses_shanghai_trading_session(self):
        saved = first_plan()
        self.assertEqual(saved["expires_at"], "2026-10-20T15:00:00+08:00")
        bundle = market_fixture()
        later = "2026-10-20T15:01:00+08:00"
        advance_clock(bundle, later)
        self.assertEqual(evaluate_plan(saved, bundle, {}, later)["status"], "expired")

    def test_signal_is_reproducible_and_mode_cannot_cross(self):
        bundle = market_fixture()
        self.assertEqual(analyze_market(bundle, {}, NOW), analyze_market(bundle, {}, NOW))
        saved = first_plan(bundle)
        saved["mode"] = "demo"
        evaluated = evaluate_plan(saved, bundle, {}, NOW)
        self.assertEqual(evaluated["status"], "blocked")
        self.assertFalse(evaluated["can_execute"])

    def test_pullback_requires_an_actual_prior_advance(self):
        bundle = market_fixture()
        self.assertFalse(any(plan["strategy"] == "pullback" for plan in analyze_market(bundle, {}, NOW)["candidates"]))
        bars = bundle["bars"]["300001.SZ"]
        closes = [17.0 + index * 0.13 for index in range(22)] + [19.55, 19.35, 19.4]
        for bar, close in zip(bars, closes):
            bar.update(open=close - 0.02, high=close + 0.12, low=close - 0.15, close=close)
        bundle["quotes"][0].update(price=19.62, prev_close=19.4, high=19.65)
        result = analyze_market(bundle, {}, NOW)
        pulls = [plan for plan in result["candidates"] if plan["symbol"] == "300001.SZ" and plan["strategy"] == "pullback"]
        self.assertEqual(len(pulls), 1)
        self.assertLess(pulls[0]["stop_price"], pulls[0]["entry_min"])


class SizingTests(unittest.TestCase):
    def setUp(self):
        self.plan = {"symbol": "A", "theme_id": "robot", "entry_max": 20.0, "stop_price": 19.0, "mode": "live"}
        self.account = {"equity": 200000, "cash": 50000}
        self.security = {"min_buy_qty": 100, "buy_qty_step": 100, "price": 19.8}

    def test_unknown_cash_or_equity_never_produces_shares(self):
        for field in ("cash", "equity"):
            account = dict(self.account, **{field: None})
            result = size_position(self.plan, account, {}, self.security)
            self.assertIsNone(result["quantity"])
            self.assertIsNone(result["notional"])

    def test_risk_and_fees_reduce_the_naive_thousand_share_example(self):
        result = size_position(self.plan, self.account, {}, self.security)
        self.assertEqual(result["risk_budget"], 1000)
        self.assertEqual(result["quantity"], 900)
        self.assertEqual(result["notional"], 18000)
        self.assertAlmostEqual(result["planned_loss"], 963.0)
        self.assertLessEqual(result["planned_loss"], result["risk_budget"])

    def test_cash_symbol_and_theme_caps_all_apply(self):
        holdings = [{"symbol": "B", "theme_id": "robot", "quantity": 1000, "market_value": 45000}]
        result = size_position(self.plan, self.account, {}, self.security, holdings)
        self.assertEqual(result["quantity"], 200)
        holdings.append({"symbol": "A", "theme_id": "robot", "quantity": 100, "market_value": 6000})
        self.assertEqual(size_position(self.plan, self.account, {}, self.security, holdings)["quantity"], 0)
        self.assertEqual(size_position(self.plan, {"equity": 200000, "cash": 1900}, {}, self.security)["quantity"], 0)

    def test_security_minimum_and_increment_are_not_assumed_to_be_hundreds(self):
        security = {"min_buy_qty": 200, "buy_qty_step": 1}
        result = size_position(self.plan, self.account, {}, security)
        self.assertEqual(result["quantity"], 934)
        self.assertLessEqual(result["planned_loss"], result["risk_budget"])
        self.assertIsNone(size_position(self.plan, self.account, {}, {})["quantity"])

    def test_unvalued_existing_holdings_do_not_silently_become_zero_exposure(self):
        result = size_position(self.plan, self.account, {}, self.security, [{"symbol": "B", "theme_id": "robot", "quantity": 1000}])
        self.assertIsNone(result["quantity"])

    def test_stale_valuation_or_unknown_theme_does_not_produce_exact_shares(self):
        for extra in ({"price_is_stale": True}, {"theme_id": None}):
            with self.subTest(extra=extra):
                holding = {"symbol": "B", "theme_id": "robot", "quantity": 100, "market_value": 2000, **extra}
                result = size_position(self.plan, self.account, {}, self.security, [holding])
                self.assertIsNone(result["quantity"])
        holding = {"symbol": "B", "theme_id": "other", "theme_ids": ["other", "robot"], "quantity": 1000, "market_value": 49000}
        self.assertEqual(size_position(self.plan, self.account, {}, self.security, [holding])["quantity"], 0)

    def test_every_theme_of_the_new_plan_is_capped_by_its_own_exposure(self):
        plan = {**self.plan, "theme_ids": ["robot", "media"]}
        holdings = [
            {"symbol": "B", "theme_id": "robot", "quantity": 500, "market_value": 10000},
            {"symbol": "C", "theme_id": "media", "quantity": 1000, "market_value": 46000},
        ]
        result = size_position(plan, self.account, {}, self.security, holdings)
        self.assertEqual(result["quantity"], 200)
        self.assertEqual(result["limiting_theme_ids"], ["media"])
        self.assertEqual(result["theme_exposures"], {"media": 46000, "robot": 10000})
        # Removing only the secondary label would incorrectly allow 900 shares.
        self.assertEqual(size_position(self.plan, self.account, {}, self.security, holdings)["quantity"], 900)
        holdings[1]["market_value"] = 49000
        self.assertEqual(size_position(plan, self.account, {}, self.security, holdings)["quantity"], 0)

    def test_shared_holdings_count_once_inside_each_relevant_theme(self):
        plan = {**self.plan, "theme_ids": ["robot", "media", "media"]}
        holdings = [
            {"symbol": "B", "theme_ids": ["robot", "media"], "quantity": 400, "market_value": 8000},
            {"symbol": "C", "theme_id": "media", "quantity": 1000, "market_value": 40000},
        ]
        result = size_position(plan, self.account, {}, self.security, holdings)
        self.assertEqual(result["quantity"], 100)
        self.assertEqual(result["theme_exposures"], {"media": 48000, "robot": 8000})


class SettlementAndAlertsTests(unittest.TestCase):
    CALENDAR = ["2026-09-30", "2026-10-12", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16", "2026-10-19", "2026-10-20", "2026-10-21"]

    def test_t1_respects_holiday_weekend_and_mixed_purchase_lots(self):
        old = {"quantity": 100, "bought_at": "2026-09-30T14:00:00+08:00"}
        self.assertEqual(sellable_quantity([old], "2026-10-10T10:00:00+08:00", self.CALENDAR), 0)
        self.assertEqual(sellable_quantity([old], "2026-10-12T10:00:00+08:00", self.CALENDAR), 100)
        new = {"quantity": 200, "bought_at": "2026-10-20T09:45:00+08:00"}
        self.assertEqual(sellable_quantity([old, new], NOW, self.CALENDAR), 100)
        self.assertEqual(sellable_quantity([new], NOW, self.CALENDAR), 0)

    def test_unknown_or_out_of_coverage_calendar_is_unknown(self):
        lots = [{"quantity": 100, "bought_at": "2026-10-19T14:00:00+08:00"}]
        self.assertIsNone(sellable_quantity(lots, NOW, self.CALENDAR, False))
        self.assertIsNone(sellable_quantity(lots, "2026-10-22T10:30:00+08:00", self.CALENDAR))

    def test_fresh_broker_confirmation_can_reduce_but_not_override_t1(self):
        lots = [{"quantity": 100, "bought_at": "2026-10-19T14:00:00+08:00", "sellable_confirmed": 50, "sellable_confirmed_at": NOW}]
        self.assertEqual(sellable_quantity(lots, NOW, self.CALENDAR, False), 50)
        lots[0]["bought_at"] = "2026-10-20T09:45:00+08:00"
        self.assertEqual(sellable_quantity(lots, NOW, self.CALENDAR, False), 0)
        lots[0]["sellable_confirmed"] = 101
        self.assertIsNone(sellable_quantity(lots, NOW, self.CALENDAR))

    def position(self, bought_at="2026-10-20T09:45:00+08:00"):
        return {"symbol": "300001.SZ", "name": "测试甲", "quantity": 100, "avg_cost": 20,
                "opened_at": bought_at, "lots": [{"quantity": 100, "bought_at": bought_at}], "highest_price": 20.5, "mode": "live"}

    def stop_quote(self):
        quote = market_fixture()["quotes"][0]
        quote["price"] = 18.8
        return quote

    def test_new_lot_stop_is_pending_unavailable_and_never_sold(self):
        position = self.position()
        original = copy.deepcopy(position)
        alerts = evaluate_position(position, {"stop_price": 19}, self.stop_quote(), None, {}, NOW, self.CALENDAR)
        stop = next(alert for alert in alerts if alert["code"] == "PRICE_STOP")
        self.assertEqual(stop["status"], "cannot_sell")
        self.assertEqual(stop["sellable_quantity"], 0)
        self.assertEqual(position, original)
        self.assertFalse(any(alert["status"] == "sold" for alert in alerts))

    def test_mixed_old_and_new_lots_show_remaining_unavailable_exposure(self):
        position = self.position("2026-10-19T10:00:00+08:00")
        position["quantity"] = 300
        position["lots"].append({"quantity": 200, "bought_at": "2026-10-20T09:45:00+08:00"})
        alerts = evaluate_position(position, {"stop_price": 19}, self.stop_quote(), None, {}, NOW, self.CALENDAR)
        stop = next(alert for alert in alerts if alert["code"] == "PRICE_STOP")
        self.assertEqual(stop["status"], "triggered")
        self.assertEqual(stop["sellable_quantity"], 100)
        self.assertIn("100/300", stop["message"])

    def test_sellable_lot_needs_liquidity_and_trigger_does_not_equal_fill(self):
        position = self.position("2026-10-19T10:00:00+08:00")
        for extra in ({}, {"bid1_volume": 0}, {"is_suspended": True}, {"limit_down": 18.8}):
            with self.subTest(extra=extra):
                quote = dict(self.stop_quote(), **extra)
                stop = next(alert for alert in evaluate_position(position, {"stop_price": 19}, quote, None, {}, NOW, self.CALENDAR) if alert["code"] == "PRICE_STOP")
                self.assertEqual(stop["status"], "cannot_sell" if extra else "triggered")
                self.assertEqual(stop["sellable_quantity"], 100)

    def test_stale_price_does_not_generate_a_price_stop(self):
        quote = self.stop_quote()
        quote["quote_time"] = "2026-10-20T10:00:00+08:00"
        alerts = evaluate_position(self.position(), {"stop_price": 19}, quote, None, {}, NOW, self.CALENDAR)
        self.assertIn("DATA_STALE", {alert["code"] for alert in alerts})
        self.assertNotIn("PRICE_STOP", {alert["code"] for alert in alerts})

    def test_mixed_plans_are_not_silently_covered_by_one_stop_line(self):
        position = self.position("2026-10-19T10:00:00+08:00")
        position["quantity"] = 200
        position["lots"][0]["plan_id"] = "old-plan"
        position["lots"].append({"quantity": 100, "bought_at": "2026-10-19T11:00:00+08:00", "plan_id": "new-plan"})
        alerts = evaluate_position(position, {"stop_price": 19}, self.stop_quote(), None, {}, NOW, self.CALENDAR)
        codes = {alert["code"] for alert in alerts}
        self.assertIn("PLAN_ALLOCATION_UNKNOWN", codes)
        self.assertNotIn("PRICE_STOP", codes)

    def test_price_theme_time_and_account_alerts_are_distinct(self):
        position = self.position("2026-10-14T10:00:00+08:00")
        position.update(account_drawdown_pct=7, theme_exposure_pct=30)
        theme = {"up_ratio": 0.2, "avg_change_pct": -2, "status": "weakening", "as_of": NOW}
        alerts = evaluate_position(position, {"stop_price": 19}, self.stop_quote(), theme, {}, NOW, self.CALENDAR)
        codes = {alert["code"] for alert in alerts}
        self.assertTrue({"PRICE_STOP", "THEME_WEAKENING", "TIME_REVIEW", "ACCOUNT_DRAWDOWN", "THEME_CONCENTRATION"}.issubset(codes))
        self.assertEqual(next(alert for alert in alerts if alert["code"] == "TIME_REVIEW")["sellable_quantity"], 100)


if __name__ == "__main__":
    unittest.main()
