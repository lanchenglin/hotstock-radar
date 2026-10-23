"""Consistent SQLite backup; works while the single app process is running."""
import argparse
import os
from datetime import datetime, timezone
from pathlib import Path
import sqlite3


def main():
    parser = argparse.ArgumentParser(description="备份热点雷达独立数据库，包含配置与所有工作区")
    parser.add_argument("--data-dir", default=os.environ.get("HOTSTOCK_DATA_DIR", "data"))
    parser.add_argument("--output-dir", default="backups")
    args = parser.parse_args()
    source = (Path(args.data_dir) / "hotstock.sqlite3").resolve()
    if not source.is_file():
        parser.error(f"数据库不存在：{source}")
    target_dir = Path(args.output_dir).resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / ("hotstock-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".sqlite3")
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as original:
        with sqlite3.connect(target) as destination:
            original.backup(destination)
            check = destination.execute("PRAGMA integrity_check").fetchone()[0]
            if check != "ok":
                raise RuntimeError("备份完整性检查失败")
    try:
        target.chmod(0o600)
    except OSError:
        pass
    print(target)


if __name__ == "__main__":
    main()
