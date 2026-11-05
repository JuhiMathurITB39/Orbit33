"""Starlette ASGI app: versioned REST API under /api/v1, optional API-key auth, uniform errors, static frontend."""

from __future__ import annotations

import hmac
import os
import time
import uuid
from pathlib import Path

from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from orbit.api import schemas as S
from orbit.api import services as sv
from orbit.assistant import assistant
from orbit.config import get_settings
from orbit.logging_setup import get_logger

log = get_logger("orbit.api")
V = "/api/v1"
FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "dist"
MAX_BODY = 64 * 1024


def err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def make_app(settings=None) -> Starlette:
    settings = settings or get_settings()
    rt = sv.Runtime(settings)
    api_keys = list(settings.api_keys) + ([os.environ["ORBIT_API_KEY"]] if os.environ.get("ORBIT_API_KEY") else [])
    api_key = bool(api_keys)  # if any key is configured, every /api/v1 route except /health requires X-API-Key
    provider = assistant.get_provider()

    def route(path, model=None, methods=("GET",), public=False):
        def deco(fn):
            async def endpoint(request: Request):
                t = time.perf_counter()
                rid = uuid.uuid4().hex[:8]
                try:
                    if api_key and not public:
                        given = request.headers.get("x-api-key", "")
                        if not any([hmac.compare_digest(given, k) for k in api_keys]):
                            return err(401, "unauthorized", "missing or invalid X-API-Key")
                    if request.method == "POST":
                        raw = await request.body()
                        if len(raw) > MAX_BODY:
                            return err(413, "payload_too_large", "request body too large")
                        body = model.model_validate_json(raw) if model else None
                        out = fn(request, body)
                    else:
                        params = dict(request.query_params)
                        for k, v in params.items():
                            if len(v) > 100:
                                return err(422, "validation_error", f"parameter {k} too long")
                        pyd = model.model_validate(params) if model else None
                        out = fn(request, pyd)
                    return JSONResponse(out)
                except ValidationError as e:
                    msg = "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()[:5])
                    return err(422, "validation_error", msg)
                except sv.NotFoundError as e:
                    return err(404, "not_found", str(e))
                except sv.BadRequest as e:
                    return err(400, "bad_request", str(e))
                except sv.ServiceUnavailable as e:
                    return err(503, "data_unavailable", str(e))
                except Exception:  # never leak internals
                    log.exception("unhandled error", extra={"request_id": rid, "path": request.url.path})
                    return err(500, "internal_error", f"unexpected error (request {rid})")
                finally:
                    log.info("request", extra={"path": request.url.path, "ms": round((time.perf_counter() - t) * 1000)})
            endpoint.__name__ = fn.__name__
            routes.append(Route(V + path, endpoint, methods=list(methods)))
            return fn
        return deco

    routes: list = []

    @route("/health", public=True)
    def _health(r, q): return sv.health(rt)

    @route("/meta")
    def _meta(r, q): return rt.meta()

    @route("/products", S.ProductQuery)
    def _products(r, q): return sv.search_products(rt, q.q, q.cat_id, q.limit, q.offset)

    @route("/stores")
    def _stores(r, q): return sv.list_stores(rt)

    @route("/sales/history", S.SalesQuery)
    def _sales(r, q): return sv.sales_history(rt, q.item_id, q.store_id, q.start, q.end, q.granularity)

    @route("/forecast", S.SeriesQuery)
    def _forecast(r, q): return sv.forecast_for(rt, q.item_id, q.store_id)

    @route("/forecast/backtest", S.BacktestQuery)
    def _bt(r, q): return sv.forecast_backtest(rt, q.item_id, q.store_id, q.horizon)

    @route("/models/comparison")
    def _models(r, q): return sv.model_comparison(rt)

    @route("/demand/seasonality", S.SeriesQuery)
    def _seas(r, q): return sv.series_seasonality(rt, q.item_id, q.store_id)

    @route("/stockout-risk", S.RiskQuery)
    def _risk(r, q): return sv.stockout_risk(rt, q.store_id, q.min_prob, q.sort, q.limit, q.offset)

    @route("/inventory", S.InventoryQuery)
    def _inv(r, q): return sv.inventory(rt, q.store_id, q.item_id, q.q, q.limit, q.offset)

    @route("/suppliers")
    def _sups(r, q): return sv.suppliers(rt)

    @route("/suppliers/{supplier_id}")
    def _sup(r, q):
        sid = r.path_params["supplier_id"]
        import re
        if not re.match(S.ID_PATTERN, sid):
            raise sv.BadRequest("invalid supplier id")
        return sv.supplier_profile(rt, sid)

    @route("/purchase-orders", S.POQuery)
    def _pos(r, q): return sv.purchase_orders(rt, q.status, q.supplier_id, q.limit, q.offset)

    @route("/replenishment/recommend", S.RecommendRequest, methods=("POST",))
    def _rec(r, b): return sv.recommend(rt, b)

    @route("/scenarios/run", S.ScenarioRequest, methods=("POST",))
    def _scn(r, b): return sv.run_scenarios(rt, b)

    @route("/simulation")
    def _sim(r, q): return sv.simulation_results(rt)

    @route("/kpis")
    def _kpis(r, q): return sv.kpis(rt)

    @route("/assistant/ask", S.AskRequest, methods=("POST",))
    def _ask(r, b): return assistant.answer(rt, b.question, b.item_id, b.store_id, provider)

    async def openapi(request: Request):
        return JSONResponse({"openapi": "3.0.3", "info": {"title": "ORBIT API", "version": "1.0.0"},
                             "paths": {rt_.path: {m.lower(): {"operationId": rt_.name} for m in sorted(rt_.methods - {"HEAD"})}
                                       for rt_ in routes}})

    async def not_found(request: Request, exc):
        if request.url.path.startswith("/api/"):
            return err(404, "not_found", "no such endpoint")
        index = FRONTEND / "index.html"
        return FileResponse(index) if index.exists() else err(404, "not_found", "frontend not built")

    async def method_not_allowed(request, exc): return err(405, "method_not_allowed", "method not allowed")

    routes.append(Route(V + "/openapi.json", openapi))
    if FRONTEND.exists():
        routes.append(Mount("/", StaticFiles(directory=FRONTEND, html=True)))
    origins = [o for o in os.environ.get("ORBIT_CORS_ORIGINS", "http://localhost:5173").split(",") if o]
    return Starlette(routes=routes, exception_handlers={404: not_found, 405: method_not_allowed},
                     middleware=[Middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"],
                                            allow_headers=["x-api-key", "content-type"])])


app = make_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=os.environ.get("ORBIT_HOST", "127.0.0.1"), port=int(os.environ.get("ORBIT_PORT", "8000")))
