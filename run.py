"""Cross-platform entrypoint; a single process owns scheduled collection."""
import argparse
import os


def main():
    parser = argparse.ArgumentParser(description="热点雷达独立服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", default=os.environ.get("HOTSTOCK_DATA_DIR", "data"))
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"} and not os.environ.get("HOTSTOCK_API_KEY"):
        parser.error("监听局域网时请先设置 HOTSTOCK_API_KEY；本机默认 127.0.0.1 无需配置。")
    os.environ["HOTSTOCK_DATA_DIR"] = args.data_dir
    import uvicorn
    uvicorn.run("hotstock.app:app", host=args.host, port=args.port, workers=1, log_level="info")


if __name__ == "__main__":
    main()
