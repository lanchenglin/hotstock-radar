"""Deterministic invented data, never a fallback for a real provider failure."""
from datetime import date, timedelta
from .timeutil import now_iso

DEMO_TIME = "2026-10-20T10:30:00+08:00"


def demo_bundle():
    days = []
    d = date(2026, 8, 1)
    while d <= date(2026, 11, 20):
        if d.weekday() < 5:
            days.append(d.isoformat())
        d += timedelta(days=1)
    past = [d for d in days if d < "2026-10-20"][-45:]
    # Explicit invented shapes exercise different lifecycle states without
    # relaxing the strategy or data-quality rules to make the demo succeed.
    specs = [
        ("DEMO001", "模拟·精密结构", 20, "robotics", "consolidation", 20.15),
        ("DEMO002", "模拟·关节传动", 30, "robotics", "consolidation", 30.75),
        ("DEMO003", "模拟·感知系统", 15, "robotics", "consolidation", 15.01),
        ("DEMO004", "模拟·内容创作", 25, "media", "pullback", 24.60),
        ("DEMO005", "模拟·数字版权", 12, "media", "pullback", 11.74),
        ("DEMO006", "模拟·发行平台", 18, "media", "consolidation", 18.14),
        ("DEMO007", "模拟·应用服务", 10, "software", "consolidation", 9.70),
        ("DEMO008", "模拟·企业工具", 16, "software", "consolidation", 15.85),
    ]
    bars, quotes = {}, []
    for idx, (symbol, name, base, theme, shape, current_price) in enumerate(specs):
        values = []
        for i, day in enumerate(past):
            if shape == "pullback":
                # The last 25 sessions advance from 17 to 19.73, then pull
                # back for three sessions while staying above the 20-day mean.
                offset = i - (len(past) - 25)
                if offset < 0:
                    raw = 15.0 + i * 0.10
                elif offset < 22:
                    raw = 17.0 + offset * 0.13
                else:
                    raw = [19.55, 19.35, 19.40][offset - 22]
                close = round(base * raw / 20, 2)
                high = round(base * (raw + 0.12) / 20, 2)
                low = round(base * (raw - 0.15) / 20, 2)
            else:
                close = round(base * 0.9875, 2)
                high = round(base, 2)
                low = round(base * 0.975, 2)
            values.append({"date": day, "open": round(close * 0.995, 2),
                           "high": high, "low": min(low, round(close * 0.995, 2)),
                           "close": close, "volume": 1200000 + idx * 80000 + i * 1000,
                           "amount": round(close * (1200000 + idx * 80000 + i * 1000), 2)})
        bars[symbol] = values
        prev = values[-1]["close"]
        price = current_price
        quotes.append({"symbol": symbol, "name": name, "price": price, "prev_close": prev,
                       "open": round(prev * 1.002, 2), "high": round(max(price, prev) * 1.003, 2),
                       "low": round(min(price, prev) * 0.997, 2), "volume": 2800000,
                       "amount": round(price * 2800000, 2), "quote_time": DEMO_TIME,
                       "received_at": DEMO_TIME, "time_quality": "simulated", "is_suspended": False,
                       "limit_up": round(prev * 1.2, 2), "limit_down": round(prev * 0.8, 2),
                       "board": "CHINEXT", "min_buy_qty": 100, "buy_qty_step": 100})
    themes = [{"id": theme, "name": name, "members": [s[0] for s in specs if s[3] == theme],
               "known_at": "2026-10-19T18:00:00+08:00", "membership_quality": "simulated",
               "evidence": [{"title": "模拟业务关系材料", "url": "", "published_at": "2026-10-19T17:00:00+08:00",
                             "observed_at": "2026-10-19T18:00:00+08:00", "kind": "demo",
                             "summary": "仅供体验界面的虚构材料，不对应任何真实公司或真实行情。",
                             "symbols": [s[0] for s in specs if s[3] == theme], "confidence": "simulated"}]}
              for theme, name in [("robotics", "机器人·模拟"), ("media", "AI漫剧·模拟"), ("software", "软件服务·模拟")]]
    return {"schema_version": 1, "source": "demo", "mode": "demo", "as_of": DEMO_TIME,
            "received_at": DEMO_TIME, "generated_at": now_iso(), "quotes": quotes, "bars": bars, "themes": themes,
            "calendar": days, "calendar_confirmed": False, "news": [],
            "capabilities": {"quotes": True, "themes": True, "history": True, "calendar": True, "evidence": False},
            "warnings": ["全部股票、价格、题材关系及工作日历均为虚构演示。模拟时钟固定为 2026-10-20 10:30。",
                         "演示资金与记录和真实数据工作区完全隔离。"]}
