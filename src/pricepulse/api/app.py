from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import FastAPI, Request, Response, status
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

import pricepulse
from pricepulse.api.routes import dashboard, products, system, watches

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
STATIC_FILES = ("app.css", "app.js")

# CloudFront honours s-maxage; the daily pipeline invalidates after each run.
CACHEABLE = "public, max-age=300, s-maxage=86400"
NO_STORE = "no-store"
CACHEABLE_EXACT = frozenset(
    {"/", "/v1/deals", "/v1/categories", "/v1/stats", "/robots.txt", "/sitemap.xml"}
)
CACHEABLE_PREFIXES = ("/products/", "/partials/", "/v1/products/", "/static/")


def cache_control_for(method: str, path: str, status_code: int) -> str:
    if method not in ("GET", "HEAD") or status_code != status.HTTP_200_OK:
        return NO_STORE
    if path in CACHEABLE_EXACT or path.startswith(CACHEABLE_PREFIXES):
        return CACHEABLE
    return NO_STORE


class HeadAsGet:
    """FastAPI registers GET routes only; CDNs and `curl -I` send HEAD. Serve HEAD as GET
    without a body (headers, including Content-Length and Cache-Control, are the GET ones)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] != "HEAD":
            await self.app(scope, receive, send)
            return
        body_sent = False

        async def send_without_body(message: Message) -> None:
            nonlocal body_sent
            if message["type"] == "http.response.body":
                if body_sent:
                    return
                body_sent = True
                message = {"type": "http.response.body", "body": b"", "more_body": False}
            await send(message)

        await self.app({**scope, "method": "GET"}, receive, send_without_body)


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
    # Cache-busting query string for /static links; changes only when the assets change.
    templates.env.globals["asset_v"] = hashlib.sha256(
        b"".join((STATIC_DIR / name).read_bytes() for name in STATIC_FILES)
    ).hexdigest()[:8]
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

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, _: Exception) -> Response:
        # No logging here: ServerErrorMiddleware re-raises after sending, and the ASGI server
        # (Mangum / uvicorn) logs the traceback once.
        if wants_html(request):
            response: Response = error_page(
                request, 500, "Something went wrong", "We logged it. Please try again in a moment."
            )
        else:
            response = JSONResponse({"detail": "internal server error"}, status_code=500)
        response.headers["Cache-Control"] = NO_STORE  # the cache middleware does not run here
        return response

    app.include_router(system.router)
    app.include_router(products.router)
    app.include_router(watches.router)
    app.include_router(dashboard.router)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.add_middleware(HeadAsGet)
    return app
