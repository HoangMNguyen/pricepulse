"""Server-rendered HTMX dashboard. Reuses the same query layer as the JSON API."""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from pricepulse.api import queries
from pricepulse.api.deps import ReadConn

router = APIRouter(include_in_schema=False)

CDN = {
    "htmx": (
        "https://cdn.jsdelivr.net/npm/htmx.org@2.0.4/dist/htmx.min.js",
        "sha384-HGfztofotfshcF7+8n44JQL2oJmowVChPTg48S+jvZoztPfvwD79OC/LTtG6dMp+",
    ),
    "pico": (
        "https://cdn.jsdelivr.net/npm/@picocss/pico@2.1.1/css/pico.min.css",
        "sha384-L1dWfspMTHU/ApYnFiMz2QID/PlP1xCW9visvBdbEkOLkSSWsP6ZJWhPw6apiXxU",
    ),
    "chart": (
        "https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js",
        "sha384-vsrfeLOOY6KuIYKDlmVH5UiBmgIdB1oEf7p01YgWHuqmOHfZr374+odEv96n9tNC",
    ),
}


def _render(request: Request, name: str, **ctx: object) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(request, name, {"cdn": CDN, **ctx})


@router.get("/", response_class=HTMLResponse)
def index(request: Request, conn: ReadConn) -> HTMLResponse:
    return _render(request, "deals.html", stats=queries.stats(conn))


@router.get("/partials/deals", response_class=HTMLResponse)
def deals_partial(
    request: Request,
    conn: ReadConn,
    source: str = "",
    min_discount: Decimal = Query(Decimal("0"), ge=0, le=100),
    flagged_only: bool = False,
    q: str = Query("", max_length=100),
    cursor: str | None = None,
) -> HTMLResponse:
    items, next_cursor = queries.list_deals(
        conn,
        queries.DealFilters(
            source=source or None,
            min_discount=min_discount,
            flagged_only=flagged_only,
            q=q or None,
            limit=50,
            cursor=cursor,
        ),
    )
    return _render(
        request,
        "partials/deals_table.html",
        items=items,
        next_cursor=next_cursor,
        params={
            "source": source,
            "min_discount": min_discount,
            "flagged_only": flagged_only,
            "q": q,
        },
        append=cursor is not None,
    )


@router.get("/products/{product_id}", response_class=HTMLResponse)
def product_page(request: Request, conn: ReadConn, product_id: int) -> HTMLResponse:
    return _render(request, "product.html", product=queries.get_product(conn, product_id))
