from copy import deepcopy

RISK_DEFAULTS = {
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
    "strict_data_quality": True,
}


def default_config():
    return {
        "mode": "demo", "provider": "demo", "auto_refresh": False,
        "refresh_seconds": 300, "risk": deepcopy(RISK_DEFAULTS),
        "provider_options": {"timeout_seconds": 15, "history_days": 60,
                             "max_symbols": 12, "symbols": [], "theme_names": []},
        "notifications": {"enabled": False, "webhook_url": "", "token": ""},
    }


def default_account(mode):
    return {"equity": 200000.0 if mode == "demo" else None,
            "cash": 200000.0 if mode == "demo" else None,
            "updated_at": None, "equity_updated_at": None,
            "source": "simulated" if mode == "demo" else "manual_reference"}
