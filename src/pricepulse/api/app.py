from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import FastAPI, Request, Response, status
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import NoResultFound, OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

import pricepulse
from pricepulse.api.routes import dashboard, products, system, watches

TEMPLATES_DIR = Path(__file__).parent / "templates"

# CloudFront honours s-maxage; the daily pipeline invalidates after each run.
CACHEABLE = "public, max-age=300, s-maxage=86400"
NO_STORE = "no-store"
CACHEABLE_EXACT = frozenset({"/", "/v1/deals", "/v1/categories", "/v1/stats"})
CACHEABLE_PREFIXES = ("/products/", "/partials/", "/v1/products/")


def cache_control_for(method: str, path: str, status_code: int) -> str:
    if method not in ("GET", "HEAD") or status_code != status.HTTP_200_OK:
        return NO_STORE
    if path in CACHEABLE_EXACT or path.startswith(CACHEABLE_PREFIXES):
        return CACHEABLE
    return NO_STORE


def wants_html(request: Request) -> bool:
    return not request.url.path.startswith("/v1/") and "text/html" in request.headers.get(
        "accept", ""
    )


def create_app() -> FastAPI:
    app = FastAPI(
        title="PricePulse",
        version=pricepulse.__version__,
        description="IKEA US + UNIQLO US price history, sale detection, and alerts.",
    )
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.globals["qs"] = dashboard.qs
    app.state.templates = templates

    def error_page(request: Request, status_code: int, title: str, message: str) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "error.html",
            {"cdn": dashboard.CDN, "status_code": status_code, "title": title, "message": message},
            status_code=status_code,
        )

    @app.middleware("http")
    async def cache_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers["Cache-Control"] = cache_control_for(
            request.method, request.url.path, response.status_code
        )
        return response

    @app.exception_handler(OperationalError)
    async def _db_unavailable(request: Request, _: OperationalError) -> Response:
        if wants_html(request):
            response: Response = error_page(
                request, 503, "Database unavailable", "Please retry in a few seconds."
            )
        else:
            response = JSONResponse(
                {"detail": "database unavailable, retry shortly"}, status_code=503
            )
        response.headers["Retry-After"] = "5"
        return response

    @app.exception_handler(NoResultFound)
    async def _not_found(request: Request, __: NoResultFound) -> Response:
        if wants_html(request):
            return error_page(request, 404, "Not found", "Nothing here.")
        return JSONResponse({"detail": "not found"}, status_code=404)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> Response:
        if wants_html(request):
            title = (
                "Not found"
                if exc.status_code == status.HTTP_404_NOT_FOUND
                else f"Error {exc.status_code}"
            )
            return error_page(request, exc.status_code, title, str(exc.detail))
        return await http_exception_handler(request, exc)

    app.include_router(system.router)
    app.include_router(products.router)
    app.include_router(watches.router)
    app.include_router(dashboard.router)
    return app
