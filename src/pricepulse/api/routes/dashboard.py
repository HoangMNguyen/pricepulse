"""Server-rendered HTMX dashboard. Reuses the same query layer as the JSON API."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, PlainTextResponse

from pricepulse.api import deps, queries
from pricepulse.api.deps import ReadConn, WriteConn
from pricepulse.api.schemas import SortKey
from pricepulse.db import watches
from pricepulse.sources import SourceError, get_source

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
    source: str = Query("", max_length=32),
    category: str = Query("", max_length=100),
    min_discount: str = Query("0", max_length=20),
    min_price: str = Query("", max_length=20),
    max_price: str = Query("", max_length=20),
    q: str = Query("", max_length=100),
    flagged_only: bool = False,
    on_sale_only: bool = False,
    sort: SortKey = queries.DEFAULT_SORT,
) -> queries.DealFilters:
    return queries.DealFilters(
        source=source or None,
        category=category or None,
        min_discount=_number(min_discount, "min_discount", upper=100) or Decimal("0"),
        min_price=_number(min_price, "min_price"),
        max_price=_number(max_price, "max_price"),
        q=q.strip() or None,
        flagged_only=flagged_only,
        on_sale_only=on_sale_only,
        sort=sort,
        limit=PAGE_SIZE,
    )


Filters = Annotated[queries.DealFilters, Depends(page_params)]


def template_params(f: queries.DealFilters) -> dict[str, Any]:
    """The filters as the templates render them into links and inputs (no limit, no cursor)."""
    return {
        "source": f.source,
        "category": f.category,
        "min_discount": f.min_discount or None,
        "min_price": f.min_price,
        "max_price": f.max_price,
        "q": f.q,
        "flagged_only": f.flagged_only,
        "on_sale_only": f.on_sale_only,
        "sort": f.sort,
    }


def _render(request: Request, name: str, **ctx: object) -> HTMLResponse:
    base = deps.get_settings().public_base_url
    return request.app.state.templates.TemplateResponse(
        request,
        name,
        {
            "cdn": CDN,
            "sorts": list(queries.SORTS),
            "sort_labels": queries.SORT_LABELS,
            "canonical": f"{base}{request.url.path}",  # path only, never the query string
            **ctx,
        },
    )


def _layout(source: str) -> str:
    try:
        return get_source(source).layout
    except SourceError as exc:
        raise HTTPException(404, "unknown source") from exc


@router.get("/", response_class=HTMLResponse)
def index(request: Request, conn: ReadConn, f: Filters) -> HTMLResponse:
    """One tab per retailer; `/` is the first tab, `/?source=<code>` the others."""
    stats = {s["source"]: s for s in queries.stats(conn)}
    tabs = list(stats)
    source = f.source or tabs[0]
    layout = _layout(source)
    f = replace(f, source=source)
    items, next_cursor, total = queries.list_deals(conn, f)
    base = deps.get_settings().public_base_url
    return _render(
        request,
        "deals.html",
        tabs=tabs,
        source=source,
        layout=layout,
        stat=stats[source],
        stats=stats,
        items=items,
        next_cursor=next_cursor,
        total=total,
        categories=[c for c in queries.categories(conn) if c["source"] == source],
        params=template_params(f),
        canonical=f"{base}/" if source == tabs[0] else f"{base}/?source={source}",
    )


@router.get("/partials/deals", response_class=HTMLResponse)
def deals_partial(
    request: Request, conn: ReadConn, f: Filters, cursor: str | None = None
) -> HTMLResponse:
    if not f.source:
        raise HTTPException(422, "source is required")
    items, next_cursor, _ = queries.list_deals(conn, replace(f, cursor=cursor), with_total=False)
    return _render(
        request,
        "partials/deals_rows.html",
        items=items,
        next_cursor=next_cursor,
        source=f.source,
        layout=_layout(f.source),
        params=template_params(f),
    )


@router.get("/products/{product_id}", response_class=HTMLResponse)
def product_page(request: Request, conn: ReadConn, product_id: int) -> HTMLResponse:
    return _render(request, "product.html", product=queries.get_product(conn, product_id))


@router.get("/robots.txt", response_class=PlainTextResponse)
def robots() -> str:
    base = deps.get_settings().public_base_url
    return (
        "User-agent: *\nAllow: /\nDisallow: /partials/\nDisallow: /v1/\nDisallow: /watches/\n"
        f"Sitemap: {base}/sitemap.xml\n"
    )


@router.get("/sitemap.xml")
def sitemap(conn: ReadConn) -> Response:
    base = deps.get_settings().public_base_url
    tabs = [s["source"] for s in queries.stats(conn)]
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
        f"<url><loc>{base}/</loc></url>",
    ]
    parts.extend(f"<url><loc>{base}/?source={code}</loc></url>" for code in tabs[1:])
    parts.extend(
        f"<url><loc>{base}/products/{pid}</loc><lastmod>{seen:%Y-%m-%d}</lastmod></url>"
        for pid, seen in queries.sitemap_entries(conn)
    )
    parts.append("</urlset>")
    return Response("".join(parts), media_type="application/xml")


def _watch_status(
    request: Request, view: watches.WatchView | None, state: str, token: str
) -> HTMLResponse:
    if view is None:
        response = _render(request, "watch_status.html", state="not_found", watch=None, token=token)
        response.status_code = 404
        return response
    return _render(request, "watch_status.html", state=state, watch=view, token=token)


# Links in email render a page on GET and mutate on POST: mail scanners prefetch GETs.


@router.get("/watches/confirm/{token}", response_class=HTMLResponse)
def confirm_prompt(request: Request, conn: ReadConn, token: str) -> HTMLResponse:
    return _watch_status(request, watches.by_token(conn, token), "confirm_prompt", token)


@router.post("/watches/confirm/{token}", response_class=HTMLResponse)
def confirm_watch(request: Request, conn: WriteConn, token: str) -> HTMLResponse:
    return _watch_status(request, watches.confirm(conn, token), "confirmed", token)


@router.get("/watches/unsubscribe/{token}", response_class=HTMLResponse)
def unsubscribe_prompt(request: Request, conn: ReadConn, token: str) -> HTMLResponse:
    return _watch_status(request, watches.by_token(conn, token), "unsubscribe_prompt", token)


@router.post("/watches/unsubscribe/{token}", response_class=HTMLResponse)
def unsubscribe_watch(request: Request, conn: WriteConn, token: str) -> HTMLResponse:
    """Also the RFC 8058 one-click target: the form body is ignored."""
    return _watch_status(request, watches.delete_by_token(conn, token), "unsubscribed", token)
