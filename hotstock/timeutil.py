from datetime import datetime
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")


def now_iso():
    return datetime.now(SHANGHAI).isoformat(timespec="seconds")


def parse_time(value):
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("时间必须包含时区，例如 2026-10-20T10:30:00+08:00")
    return result.astimezone(SHANGHAI)
