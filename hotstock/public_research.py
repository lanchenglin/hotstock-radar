"""Bounded public research collection, executed in a disposable child process."""
import contextlib
import importlib
import io
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta

from .providers.akshare_provider import _invoke
from .providers.common import ProviderError, number, symbol_of
from .timeutil import SHANGHAI, now_iso


def timestamp(value):
    if value is None or str(value) in {"", "None", "NaT", "nan"}:
        return None
    try:
        t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return (t.replace(tzinfo=SHANGHAI) if t.tzinfo is None else t.astimezone(SHANGHAI)).isoformat()
    except ValueError:
        return None


def collect(kind, cfg, ak=None):
    ak = ak or importlib.import_module("akshare")
    now = datetime.now(SHANGHAI)
    result = {"observed_at": now.isoformat(), "sources": {}, "warnings": []}

    def fetch(label, method, **params):
        try:
            rows = _invoke(ak, method, **params)
            result["sources"][label] = {"state": "ok", "count": len(rows), "observed_at": now_iso()}
            return rows
        except ProviderError:
            result["sources"][label] = {"state": "error", "observed_at": now_iso()}
            result["warnings"].append(label + " 采集失败；不代表没有消息或风险。")
            return []

    if kind == "topics":
        rows = fetch("concept_catalog", "stock_board_concept_name_em")
        catalog = [{"id": "em:" + str(r["板块代码"]), "code": str(r["板块代码"]), "name": str(r["板块名称"]),
                    "change_pct": number(r.get("涨跌幅")), "up": number(r.get("上涨家数")),
                    "down": number(r.get("下跌家数"))}
                   for r in rows if r.get("板块代码") and r.get("板块名称")]
        catalog.sort(key=lambda r: r["change_pct"] if r["change_pct"] is not None else -999, reverse=True)
        result["catalog"] = catalog
        names = cfg.get("theme_names", [])
        selected = ([r for r in catalog if r["name"] in names or r["code"] in names] if names else catalog)[:cfg.get("max_themes", 3)]
        themes, ranked = [], []
        for row in selected:
            members = fetch("members:" + row["code"], "stock_board_concept_cons_em", symbol=row["code"])
            members.sort(key=lambda r: number(r.get("涨跌幅")) or 0, reverse=True)
            codes = []
            for m in members:
                try:
                    code = symbol_of(m.get("代码"))
                    if not code.endswith(".BJ"):
                        codes.append(code)
                except ProviderError:
                    continue
            codes = list(dict.fromkeys(codes))
            if codes:
                ranked.append(codes)
                themes.append({"id": row["id"], "name": row["name"], "members": codes,
                               "known_at": now_iso(), "membership_quality": "current_snapshot", "evidence": []})
        if themes:
            result["themes"] = themes
            result["ranked_symbols"] = list(dict.fromkeys(s for i in range(max(map(len, ranked))) for group in ranked for s in group[i:i+1]))
        calendar = fetch("calendar", "tool_trade_date_hist_sina")
        days = sorted({str(r.get("trade_date"))[:10] for r in calendar if timestamp(r.get("trade_date"))})
        result["calendar"] = days
        result["calendar_confirmed"] = bool(days and days[0] <= now.date().isoformat() <= days[-1])
        pool = fetch("limit_up_pool", "stock_zt_pool_em", date=now.strftime("%Y%m%d"))
        result["limit_up_pool"] = [{"code": str(r.get("代码", "")), "name": str(r.get("名称", "")),
                                   "change_pct": number(r.get("涨跌幅")), "boards": number(r.get("连板数")),
                                   "break_count": number(r.get("炸板次数"))} for r in pool[:1000]]
        result["market_date"] = now.date().isoformat()
        result["warnings"].append("目录涨幅是公开参考快照；涨停池仅本次请求日期，不能替代完整历史；概念标签不等于业务证据。")
    elif kind == "news":
        items = []
        if cfg.get("cls_enabled", True):
            rows = fetch("cls", "stock_info_global_cls", symbol="全部")
            for r in rows:
                items.append({"title": str(r.get("标题") or "财经快讯"), "text": str(r.get("内容") or "")[:12000],
                              "source": "财联社/AKShare", "kind": "news", "url": "https://www.cls.cn/telegraph",
                              "published_at": timestamp(str(r.get("发布日期", "")) + "T" + str(r.get("发布时间", "")))})
        # Bounded rotation is done by the runtime. No whole-market per-stock crawling.
        for symbol in cfg.get("symbols", [])[:cfg.get("max_news_symbols", 5)]:
            base = symbol.split(".")[0]
            if cfg.get("eastmoney_enabled", True):
                for r in fetch("eastmoney:" + symbol, "stock_news_em", symbol=base)[:20]:
                    text = str(r.get("新闻内容") or "")
                    title = str(r.get("新闻标题") or "个股新闻")
                    # A search query matching a code isn't evidence that its results concern it.
                    symbols = [symbol] if base in title + text else []
                    items.append({"title": title, "text": text[:12000], "source": str(r.get("文章来源") or "东方财富聚合")[:100],
                                  "url": str(r.get("新闻链接") or ""), "kind": "news", "symbols": symbols,
                                  "published_at": timestamp(r.get("发布时间"))})
            if cfg.get("announcements_enabled", True):
                for r in fetch("cninfo:" + symbol, "stock_zh_a_disclosure_report_cninfo", symbol=base, market="沪深京",
                               start_date=(now.date() - timedelta(days=3)).strftime("%Y%m%d"), end_date=now.strftime("%Y%m%d"))[:30]:
                    items.append({"title": str(r.get("公告标题") or "公司公告"), "source": "巨潮公告目录/AKShare", "kind": "announcement",
                                  "url": str(r.get("公告链接") or ""), "symbols": [symbol] if str(r.get("代码", "")) == base else [],
                                  "title_only": True, "published_at": timestamp(r.get("公告时间"))})
        result["articles"] = items[:500]
        result["warnings"].append("快讯与搜索有返回窗口限制；公告仅采集目录标题和链接，未自动读取PDF正文；不宣称消息全覆盖。")
    else:
        raise ProviderError("未知采集任务")
    return result


def run_collection(kind, cfg):
    try:
        child = subprocess.run([sys.executable, "-m", "hotstock.public_research"],
             input=json.dumps({"kind": kind, "config": cfg}, ensure_ascii=False),
             env={k:v for k,v in os.environ.items() if k not in {"MARKET_MCP_TOKEN", "HOTSTOCK_API_KEY", "HOTSTOCK_LLM_API_KEY", "HOTSTOCK_SHARED_DATA_TOKEN", "HOTSTOCK_TUSHARE_TOKEN"}},
             encoding="utf-8", capture_output=True, timeout=cfg.get("job_timeout_seconds", 120), check=False)
    except subprocess.TimeoutExpired:
        raise ProviderError("公开采集超过本轮时限，子进程已停止；旧消息不标记为新消息。") from None
    if len(child.stdout.encode()) > 8 * 1024 * 1024:
        raise ProviderError("公开采集结果过大")
    try:
        value = json.loads(child.stdout)
        if value.get("error"):
            raise ProviderError(value["error"])
        return value
    except (ValueError, TypeError):
        raise ProviderError("公开采集未返回合法结果，请检查依赖。") from None


def main():
    try:
        value = json.loads(sys.stdin.read(65536))
        import requests
        original = requests.sessions.Session.request
        def bounded(session, method, url, **kwargs):
            kwargs["timeout"] = 10
            return original(session, method, url, **kwargs)
        requests.sessions.Session.request = bounded
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = collect(value["kind"], value["config"])
    except Exception as exc:
        result = {"error": "公开采集失败（" + type(exc).__name__ + "）；检查 AKShare 依赖及网络，不自动切换数据源。"}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
