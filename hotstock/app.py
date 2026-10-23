import hmac
import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from . import __version__
from .models import Bundle, ConfigPatch, PlanPatch, PlanRequest, RefreshRequest, TradeRequest
from .service import ServiceError
from .research import RadarService as MarketService, register_research

MAX_BODY = 8 * 1024 * 1024


def create_app(data_dir=None, start_worker=True, api_key=None):
    key = api_key if api_key is not None else os.environ.get("HOTSTOCK_API_KEY", "")
    directory = Path(data_dir or os.environ.get("HOTSTOCK_DATA_DIR", "data"))

    @asynccontextmanager
    async def lifespan(application):
        service = MarketService(directory / "hotstock.sqlite3")
        application.state.service = service
        service.initialize()
        if start_worker:
            service.start()
        yield
        service.stop()

    application = FastAPI(title="热点雷达 · 独立研究与风控", version=__version__, lifespan=lifespan)

    @application.middleware("http")
    async def guard(request: Request, call_next):
        if request.url.path.startswith("/api/"):
            if request.url.path != "/api/health" and key and not hmac.compare_digest(request.headers.get("X-API-Key", ""), key):
                return JSONResponse({"detail": "需要正确的访问密钥（X-API-Key）"}, status_code=401)
            if request.method not in {"GET", "HEAD", "OPTIONS"}:
                origin = request.headers.get("Origin")
                if origin and urlsplit(origin).netloc != request.headers.get("Host"):
                    return JSONResponse({"detail": "拒绝跨站写入请求"}, status_code=403)
                try:
                    if int(request.headers.get("content-length", "0")) > MAX_BODY:
                        return JSONResponse({"detail": "请求不得超过 8 MiB"}, status_code=413)
                except ValueError:
                    return JSONResponse({"detail": "Content-Length 格式错误"}, status_code=400)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @application.exception_handler(ServiceError)
    async def service_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    @application.exception_handler(ValidationError)
    async def validation_error(request, exc):
        return JSONResponse({"detail": "数据格式校验失败", "errors": [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]}, status_code=422)

    @application.get("/api/health")
    def health(request: Request):
        return {"status": "ok", "version": __version__, "service": "hotstock-radar", "mode": request.app.state.service.config["mode"]}

    @application.get("/api/dashboard")
    def dashboard(request: Request):
        return request.app.state.service.dashboard()

    @application.get("/api/config")
    def config(request: Request):
        return request.app.state.service.get_config()

    @application.put("/api/config")
    def update_config(body: ConfigPatch, request: Request):
        return request.app.state.service.update_config(body.model_dump(exclude_unset=True))

    @application.get("/api/providers")
    def providers(request: Request):
        from .providers import describe_providers
        service = request.app.state.service
        items = describe_providers()
        if not any(p["id"] == "demo" for p in items):
            items.insert(0, {"id": "demo", "name": "内置演示数据", "account_required": False,
                             "install": "无需额外安装", "capabilities": ["演示全流程"],
                             "limitations": ["所有证券与价格均为虚构，不用于投资决策"], "docs_url": ""})
        return {"providers": items, "status": service.store.get(f"status:{service.config['mode']}", {})}

    @application.post("/api/refresh")
    def refresh(body: RefreshRequest, request: Request):
        return request.app.state.service.refresh(body.provider)

    @application.post("/api/import")
    async def import_data(request: Request):
        payload = await request.body()
        if len(payload) > MAX_BODY:
            raise ServiceError("请求不得超过 8 MiB", 413)
        try:
            parsed = Bundle.model_validate_json(payload).model_dump(mode="json")
        except ValidationError:
            raise
        # CPU/I/O work in a worker thread keeps health checks responsive.
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(request.app.state.service.import_bundle, parsed)

    @application.get("/api/plans")
    def plans(request: Request):
        service = request.app.state.service
        service.monitor()
        return {"items": service.dashboard()["plans"]}

    @application.post("/api/plans")
    def create_plan(body: PlanRequest, request: Request):
        return request.app.state.service.create_plan(body.candidate_id)

    @application.patch("/api/plans/{plan_id}")
    def cancel_plan(plan_id: str, body: PlanPatch, request: Request):
        return request.app.state.service.cancel_plan(plan_id)

    @application.get("/api/trades")
    def trades(request: Request):
        service = request.app.state.service
        return {"items": service.store.records("trade", service.config["mode"])}

    @application.post("/api/trades")
    def record_trade(body: TradeRequest, request: Request):
        return request.app.state.service.record_trade(body.model_dump())

    @application.get("/api/events")
    def events(request: Request, limit: int = Query(default=100, ge=1, le=1000)):
        service = request.app.state.service
        return {"items": service.store.records("event", service.config["mode"], limit)}

    @application.get("/api/export")
    def export(request: Request):
        return request.app.state.service.export()

    @application.post("/api/notifications/test")
    def notification_test(request: Request):
        return request.app.state.service.send_notification({"type": "test", "message": "热点雷达通知测试；没有执行任何交易。"}, test=True)

    static = Path(__file__).parent / "static"
    application.mount("/static", StaticFiles(directory=str(static)), name="static")

    @application.get("/")
    def home():
        return HTMLResponse((static / "index.html").read_text(encoding="utf-8").replace("</body>", '<script src="/static/research-link.js"></script></body>'), headers={"Cache-Control": "no-cache"})

    register_research(application)
    return application


app = create_app()
