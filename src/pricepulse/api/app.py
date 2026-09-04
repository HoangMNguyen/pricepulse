from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import NoResultFound

import pricepulse
from pricepulse.api.routes import dashboard, products, system, watches

TEMPLATES_DIR = Path(__file__).parent / "templates"


def create_app() -> FastAPI:
    app = FastAPI(
        title="PricePulse",
        version=pricepulse.__version__,
        description="IKEA US + UNIQLO US price history, sale detection, and alerts.",
    )
    app.state.templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    app.state.templates.env.globals["qs"] = dashboard.qs

    @app.exception_handler(NoResultFound)
    async def _not_found(_: Request, __: NoResultFound) -> JSONResponse:
        return JSONResponse({"detail": "not found"}, status_code=404)

    app.include_router(system.router)
    app.include_router(products.router)
    app.include_router(watches.router)
    app.include_router(dashboard.router)
    return app
