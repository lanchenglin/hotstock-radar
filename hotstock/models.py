"""Strict boundaries; providers and engine exchange plain dictionaries."""
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .timeutil import parse_time


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Quote(BaseModel):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)
    symbol: str = Field(min_length=1, max_length=30)
    name: str = Field(min_length=1, max_length=80)
    price: float = Field(gt=0)
    prev_close: float = Field(gt=0)
    open: float | None = Field(default=None, ge=0)
    high: float | None = Field(default=None, ge=0)
    low: float | None = Field(default=None, ge=0)
    volume: float | None = Field(default=None, ge=0)
    amount: float | None = Field(default=None, ge=0)
    quote_time: str | None = None
    received_at: str
    time_quality: Literal["exchange", "provider", "unknown", "simulated"] = "unknown"
    is_suspended: bool | None = None
    limit_up: float | None = Field(default=None, gt=0)
    limit_down: float | None = Field(default=None, gt=0)
    board: str = "UNKNOWN"
    min_buy_qty: int | None = Field(default=None, gt=0)
    buy_qty_step: int | None = Field(default=None, gt=0)

    @field_validator("quote_time", "received_at")
    @classmethod
    def validate_times(cls, value):
        if value is not None:
            parse_time(value)
        return value


class Bar(BaseModel):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)
    date: date
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float | None = Field(default=None, ge=0)
    amount: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def check_ohlc(self):
        if self.high < max(self.open, self.close, self.low) or self.low > min(self.open, self.close):
            raise ValueError("K线 high/low 与 open/close 不一致")
        return self


class Theme(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=100)
    members: list[str] = Field(default_factory=list, max_length=10000)
    known_at: str | None = None
    membership_quality: Literal["point_in_time", "current_snapshot", "unknown", "simulated"] = "unknown"
    evidence: list[dict[str, Any]] = Field(default_factory=list, max_length=200)

    @field_validator("known_at")
    @classmethod
    def known_time(cls, value):
        if value:
            parse_time(value)
        return value


class Bundle(BaseModel):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)
    schema_version: Literal[1]
    source: str = Field(min_length=1, max_length=100)
    mode: Literal["demo", "live", "historical"]
    as_of: str
    received_at: str
    quotes: list[Quote] = Field(default_factory=list, max_length=10000)
    bars: dict[str, list[Bar]] = Field(default_factory=dict)
    themes: list[Theme] = Field(default_factory=list, max_length=1000)
    calendar: list[date] = Field(default_factory=list, max_length=20000)
    calendar_confirmed: bool = False
    news: list[dict[str, Any]] = Field(default_factory=list, max_length=3000)
    capabilities: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list, max_length=200)

    @field_validator("as_of", "received_at")
    @classmethod
    def times(cls, value):
        parse_time(value)
        return value

    @model_validator(mode="after")
    def unique(self):
        symbols = [q.symbol for q in self.quotes]
        if len(symbols) != len(set(symbols)):
            raise ValueError("同一份行情快照不能包含重复证券")
        if len([t.id for t in self.themes]) != len({t.id for t in self.themes}):
            raise ValueError("题材 id 不能重复")
        if self.mode != "demo" and (any(q.time_quality == "simulated" or q.symbol.startswith("DEMO") for q in self.quotes)
                                    or any(t.membership_quality == "simulated" for t in self.themes)):
            raise ValueError("模拟数据不能导入真实或历史工作区")
        if sum(len(b) for b in self.bars.values()) > 200000:
            raise ValueError("单次导入历史条数过多，请分批整理")
        reference_day = parse_time(self.as_of).date()
        for symbol, rows in self.bars.items():
            dates = [row.date for row in rows]
            if len(dates) != len(set(dates)):
                raise ValueError(f"{symbol} 不能包含重复交易日的日K")
            if any(day > reference_day for day in dates):
                raise ValueError(f"{symbol} 包含晚于快照时点的日K")
        if len(self.calendar) != len(set(self.calendar)):
            raise ValueError("交易日历不能包含重复日期")
        if any(len(t.members) != len(set(t.members)) for t in self.themes):
            raise ValueError("同一题材的成分股不能重复")
        return self


class RefreshRequest(StrictModel):
    provider: Literal["demo", "akshare", "tushare", "http", "tencent_baostock"] | None = None


class PlanRequest(StrictModel):
    candidate_id: str = Field(min_length=1, max_length=300)


class PlanPatch(StrictModel):
    status: Literal["cancelled"]


class TradeRequest(StrictModel):
    symbol: str = Field(pattern=r"^(?:\d{6}\.(?:SH|SZ|BJ)|DEMO\d{3})$")
    name: str = Field(min_length=1, max_length=80)
    side: Literal["buy", "sell"]
    quantity: int = Field(gt=0, le=100000000)
    price: float = Field(gt=0, le=1000000)
    executed_at: str
    plan_id: str | None = None
    theme_id: str | None = None
    confirmed: Literal[True]
    notes: str = Field(default="", max_length=1000)

    @field_validator("executed_at")
    @classmethod
    def time(cls, value):
        parse_time(value)
        return value


class RiskPatch(StrictModel):
    risk_per_trade_pct: float | None = Field(default=None, gt=0, le=5)
    max_position_pct: float | None = Field(default=None, gt=0, le=100)
    max_theme_pct: float | None = Field(default=None, gt=0, le=100)
    slippage_pct: float | None = Field(default=None, ge=0, le=10)
    fee_pct: float | None = Field(default=None, ge=0, le=5)
    max_quote_age_seconds: int | None = Field(default=None, ge=15, le=3600)
    min_history_bars: int | None = Field(default=None, ge=20, le=250)
    min_theme_members: int | None = Field(default=None, ge=2, le=100)
    min_theme_up_ratio: float | None = Field(default=None, ge=0.5, le=1)
    min_theme_change_pct: float | None = Field(default=None, ge=0, le=20)
    entry_buffer_pct: float | None = Field(default=None, ge=0, le=5)
    max_chase_pct: float | None = Field(default=None, ge=0, le=10)
    max_stop_distance_pct: float | None = Field(default=None, gt=0, le=20)
    trailing_stop_pct: float | None = Field(default=None, gt=0, le=30)
    max_holding_sessions: int | None = Field(default=None, ge=1, le=250)
    strict_data_quality: bool | None = None


class AccountPatch(StrictModel):
    equity: float | None = Field(default=None, ge=0, le=1e12)
    cash: float | None = Field(default=None, ge=0, le=1e12)


class ConfigPatch(StrictModel):
    provider: Literal["demo", "akshare", "tushare", "http", "tencent_baostock"] | None = None
    mode: Literal["demo", "live", "historical"] | None = None
    auto_refresh: bool | None = None
    refresh_seconds: int | None = Field(default=None, ge=60, le=86400)
    provider_options: dict[str, Any] | None = None
    risk: RiskPatch | None = None
    account: AccountPatch | None = None
    notifications: dict[str, Any] | None = None
