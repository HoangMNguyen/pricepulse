"""Server-rendered HTMX dashboard. Reuses the same query layer as the JSON API."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from pricepulse.api import queries
from pricepulse.api.deps import ReadConn
from pricepulse.api.schemas import SortKey

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

PAGE_SIZE = 50


def qs(params: dict[str, Any], **overrides: Any) -> str:
    """Query string for links: drops None/""/False, renders True as "true"."""
    merged = {**params, **overrides}
    kept = {
        k: ("true" if v is True else v)
        for k, v in merged.items()
        if v is not None and v != "" and v is not False
    }
    return urlencode(kept)


@dataclass(frozen=True, slots=True)
class PageParams:
    """The `/v1/deals` parameters as the dashboard exposes them (no limit, no cursor)."""

    source: str | None
    category: str | None
    min_discount: Decimal
    min_price: Decimal | None
    max_price: Decimal | None
    q: str | None
    flagged_only: bool
    on_sale_only: bool
    sort: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "category": self.category,
            "min_discount": self.min_discount if self.min_discount else None,
            "min_price": self.min_price,
            "max_price": self.max_price,
            "q": self.q,
            "flagged_only": self.flagged_only,
            "on_sale_only": self.on_sale_only,
            "sort": self.sort,
        }

    def filters(self, cursor: str | None = None) -> queries.DealFilters:
        return queries.DealFilters(
            source=self.source,
            category=self.category,
            min_discount=self.min_discount,
            min_price=self.min_price,
            max_price=self.max_price,
            q=self.q,
            flagged_only=self.flagged_only,
            on_sale_only=self.on_sale_only,
            sort=self.sort,
            limit=PAGE_SIZE,
            cursor=cursor,
        )


def _number(value: str, name: str, upper: int | None = None) -> Decimal | None:
    """HTML number inputs submit "" when blank; treat that as unset, reject garbage with 422."""
    if not value.strip():
        return None
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise HTTPException(422, f"{name} must be a number") from exc
    if number < 0 or (upper is not None and number > upper):
        raise HTTPException(422, f"{name} out of range")
    return number


def page_params(
    source: str = "",
    category: str = Query("", max_length=100),
    min_discount: str = Query("0", max_length=20),
    min_price: str = Query("", max_length=20),
    max_price: str = Query("", max_length=20),
    q: str = Query("", max_length=100),
    flagged_only: bool = False,
    on_sale_only: bool = False,
    sort: SortKey = queries.DEFAULT_SORT,
) -> PageParams:
    return PageParams(
        source=source or None,
        category=category or None,
        min_discount=_number(min_discount, "min_discount", upper=100) or Decimal("0"),
        min_price=_number(min_price, "min_price"),
        max_price=_number(max_price, "max_price"),
        q=q.strip() or None,
        flagged_only=flagged_only,
        on_sale_only=on_sale_only,
        sort=sort,
    )


Params = Annotated[PageParams, Depends(page_params)]


def _render(request: Request, name: str, **ctx: object) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(
        request,
        name,
        {"cdn": CDN, "sorts": list(queries.SORTS), "sort_labels": queries.SORT_LABELS, **ctx},
    )


@router.get("/", response_class=HTMLResponse)
def index(request: Request, conn: ReadConn, params: Params) -> HTMLResponse:
    items, next_cursor, total = queries.list_deals(conn, params.filters())
    return _render(
        request,
        "deals.html",
        stats=queries.stats(conn),
        categories=queries.categories(conn),
        items=items,
        next_cursor=next_cursor,
        total=total,
        params=params.as_dict(),
    )


@router.get("/partials/deals", response_class=HTMLResponse)
def deals_partial(
    request: Request, conn: ReadConn, params: Params, cursor: str | None = None
) -> HTMLResponse:
    items, next_cursor, _ = queries.list_deals(conn, params.filters(cursor))
    return _render(
        request,
        "partials/deals_rows.html",
        items=items,
        next_cursor=next_cursor,
        params=params.as_dict(),
    )


@router.get("/products/{product_id}", response_class=HTMLResponse)
def product_page(request: Request, conn: ReadConn, product_id: int) -> HTMLResponse:
    return _render(request, "product.html", product=queries.get_product(conn, product_id))
