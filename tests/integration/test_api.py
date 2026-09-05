"""API contract tests against a seeded Postgres."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from pricepulse.api import queries
from pricepulse.api.app import create_app
from pricepulse.config import Settings

pytestmark = pytest.mark.integration

NO_INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)")


def test_health(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["db"] == "ok"


def test_deals_ordered_and_keyset_paginated(client: TestClient) -> None:
    page1 = client.get("/v1/deals", params={"limit": 5}).json()
    assert len(page1["items"]) == 5 and page1["next_cursor"]
    pcts = [Decimal(i["discount_pct"]) for i in page1["items"]]
    assert pcts == sorted(pcts, reverse=True)
    assert pcts[0] == Decimal("24.0")  # product 12: 76 vs 100
    page2 = client.get("/v1/deals", params={"limit": 5, "cursor": page1["next_cursor"]}).json()
    ids1 = {i["product_id"] for i in page1["items"]}
    ids2 = {i["product_id"] for i in page2["items"]}
    assert ids1.isdisjoint(ids2)
    assert Decimal(page2["items"][0]["discount_pct"]) <= pcts[-1]
    # walk to the end
    seen, cursor = set(), None
    while True:
        page = client.get("/v1/deals", params={"limit": 4, "cursor": cursor}).json()
        seen.update(i["product_id"] for i in page["items"])
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(seen) == 14


def test_deals_filters(client: TestClient) -> None:
    body = client.get("/v1/deals", params={"min_discount": 20}).json()
    assert {i["name"] for i in body["items"]} == {f"KALLAX shelf {i}" for i in (10, 11, 12)}
    uniqlo = client.get("/v1/deals", params={"source": "uniqlo"}).json()["items"]
    assert len(uniqlo) == 1
    assert uniqlo[0]["discount_pct"] == "0.0" and uniqlo[0]["is_on_sale"] is True
    assert client.get("/v1/deals", params={"q": "AIRism"}).json()["items"][0]["source"] == "uniqlo"
    nope = client.get("/v1/deals", params={"source": "nope"})  # unknown code matches nothing
    assert nope.status_code == 200 and nope.json()["total"] == 0
    assert client.get("/v1/deals", params={"cursor": "!!!"}).status_code == 400


def _walk(client: TestClient, **params: object) -> tuple[list[dict], list[int]]:
    items, totals, cursor = [], [], None
    while True:
        page = client.get("/v1/deals", params={**params, "limit": 4, "cursor": cursor}).json()
        items.extend(page["items"])
        totals.append(page["total"])
        cursor = page["next_cursor"]
        if not cursor:
            return items, totals


def _sort_key(sort: str, item: dict) -> object:
    col = queries.SORTS[sort][0].removeprefix("s.")
    value = item[col]
    if sort in ("discount", "savings", "price_asc", "price_desc"):
        return Decimal(value)
    if sort == "newest":
        return datetime.fromisoformat(value)
    if sort == "ending_soon":
        return date.fromisoformat(value)
    return value


@pytest.mark.parametrize("sort", list(queries.SORTS))
def test_every_sort_walks_all_pages_in_order(client: TestClient, sort: str) -> None:
    items, totals = _walk(client, sort=sort)
    ids = [i["product_id"] for i in items]
    assert len(ids) == len(set(ids)), "pages overlap"
    expected = {
        i["product_id"]
        for i in client.get("/v1/deals", params={"limit": 200}).json()["items"]
        if sort != "ending_soon" or i["valid_to"] is not None
    }
    assert set(ids) == expected
    assert set(totals) == {len(ids)}, "total must equal the walked count on every page"
    if sort == "ending_soon":
        assert len(ids) == 1
    desc = queries.SORTS[sort][1] == "DESC"
    for a, b in zip(items, items[1:], strict=False):
        ka, kb = _sort_key(sort, a), _sort_key(sort, b)
        if ka == kb:
            assert a["product_id"] < b["product_id"]
        else:
            assert (ka > kb) if desc else (ka < kb)


def test_cursor_is_bound_to_its_sort(client: TestClient) -> None:
    cursor = client.get("/v1/deals", params={"sort": "discount", "limit": 4}).json()["next_cursor"]
    r = client.get("/v1/deals", params={"sort": "savings", "limit": 4, "cursor": cursor})
    assert r.status_code == 400 and r.json()["detail"] == "cursor does not match sort"
    assert client.get("/v1/deals", params={"sort": "sideways"}).status_code == 422


def test_deals_filters_by_category_price_and_sale_state(client: TestClient) -> None:
    storage = client.get("/v1/deals", params={"category": "Storage & organization"}).json()
    assert storage["total"] == 7 and all(
        i["category"] == "Storage & organization" for i in storage["items"]
    )
    assert client.get("/v1/deals", params={"min_price": 90}).json()["total"] == 5
    cheap = client.get("/v1/deals", params={"max_price": 80}).json()
    assert {i["name"] for i in cheap["items"]} == {
        "KALLAX shelf 10",
        "KALLAX shelf 11",
        "KALLAX shelf 12",
        "BILLY bookcase",
        "AIRism tee",
    }
    everything = client.get("/v1/deals").json()
    on_sale = client.get("/v1/deals", params={"on_sale_only": "true"}).json()
    assert everything["total"] == 14 and on_sale["total"] == 13
    assert all(i["is_on_sale"] for i in on_sale["items"])
    assert client.get("/v1/deals", params={"flagged_only": "true"}).json()["total"] == 13


def test_categories_per_source(client: TestClient) -> None:
    cats = client.get("/v1/categories").json()
    assert cats == [
        {"source": "ikea", "category": "Baby & kids", "products": 6},
        {"source": "ikea", "category": "Storage & organization", "products": 7},
        {"source": "uniqlo", "category": "MEN", "products": 1},
    ]


def test_badges_on_seeded_rows(client: TestClient) -> None:
    by_name = {i["name"]: i for i in client.get("/v1/deals", params={"limit": 200}).json()["items"]}
    twelve, eleven, one = (
        by_name["KALLAX shelf 12"],
        by_name["KALLAX shelf 11"],
        by_name["KALLAX shelf 1"],
    )
    assert twelve["is_new"] is True and twelve["days_left"] == 2 and twelve["savings"] == "24.00"
    assert twelve["drop_vs_previous_pct"] == "0.0" and twelve["previous_price"] is None
    assert eleven["previous_price"] == "90.00" and eleven["drop_vs_previous_pct"] == "13.3"
    assert eleven["discount_pct"] == "22.0"
    assert client.get(f"/v1/products/{eleven['product_id']}").json()["observations_90d"] == 2
    assert one["is_new"] is False and one["days_left"] is None and one["savings"] == "2.00"
    assert by_name["BILLY bookcase"]["is_on_sale"] is False


def test_product_and_history(client: TestClient) -> None:
    pid = client.get("/v1/deals", params={"limit": 1}).json()["items"][0]["product_id"]
    product = client.get(f"/v1/products/{pid}").json()
    assert product["list_price"] == "100.00" and product["observations_90d"] == 1
    history = client.get(f"/v1/products/{pid}/history", params={"days": 30}).json()
    assert len(history) == 1 and history[0]["price"] == "76.00"
    assert client.get("/v1/products/999999").status_code == 404
    assert client.get("/v1/products/999999/history").status_code == 404


def test_runs_and_stats(client: TestClient) -> None:
    runs = client.get("/v1/runs").json()
    assert [r["source"] for r in runs] == ["uniqlo", "ikea"]  # newest first
    assert all(r["status"] == "succeeded" for r in runs)
    stats = {s["source"]: s for s in client.get("/v1/stats").json()}
    assert stats["ikea"]["products"] == 13 and stats["ikea"]["on_sale"] == 12
    assert stats["uniqlo"]["on_sale"] == 1


def test_watch_flow_public_post_keyed_admin(client: TestClient, api_headers: dict) -> None:
    pid = client.get("/v1/deals", params={"limit": 1}).json()["items"][0]["product_id"]
    body = {"product_id": pid, "email": "w@example.com", "min_discount_pct": 5}
    created = client.post("/v1/watches", json=body)  # no key needed
    assert created.status_code == 202
    assert created.json() == {"email": "w@example.com", "product_id": pid, "min_discount_pct": "5"}
    assert client.post("/v1/watches", json=body).status_code == 409
    assert client.post("/v1/watches", json={**body, "product_id": 999999}).status_code == 404
    assert client.get("/v1/watches", params={"email": "w@example.com"}).status_code == 401
    listed = client.get(
        "/v1/watches", params={"email": "w@example.com"}, headers=api_headers
    ).json()
    assert len(listed) == 1 and listed[0]["confirmed_at"] is None
    wid = listed[0]["id"]
    assert client.delete(f"/v1/watches/{wid}").status_code == 401
    assert client.delete(f"/v1/watches/{wid}", headers=api_headers).status_code == 204
    assert client.delete(f"/v1/watches/{wid}", headers=api_headers).status_code == 404


def test_landing_is_retailer_picker(client: TestClient) -> None:
    home = client.get("/")
    assert home.status_code == 200 and "<h1>PricePulse</h1>" in home.text
    assert "<table" not in home.text and "<select" not in home.text and "<form" not in home.text
    assert 'role="tablist"' not in home.text
    for code, name in (("ikea", "IKEA US"), ("uniqlo", "UNIQLO US")):
        card = rf'<a class="retailer-card" href="/\?source={code}">.*?{name}'
        assert re.search(card, home.text, re.S)
    assert '<link rel="canonical" href="http://localhost:8000/">' in home.text
    assert client.get("/partials/deals", params={"sort": "name"}).status_code == 422


def test_dashboard_renders(client: TestClient) -> None:
    home = client.get("/", params={"source": "ikea"})
    assert home.status_code == 200 and "Current deals" in home.text
    assert 'role="tablist"' in home.text and 'id="deals-ikea"' in home.text
    assert re.search(r'aria-selected="true"[^>]*>(<i[^>]*></i>)?IKEA US', home.text)
    assert '<input type="hidden" name="source" value="ikea">' in home.text
    assert "showing 13 of 13" in home.text and "Biggest % off" in home.text
    assert ">Until<" in home.text and "90-day low" not in home.text
    # column headers are links (no JS needed) that toggle the price direction
    now_head = '<th class="num"><a class="sort" href="/?source=ikea&amp;sort=price_asc"'
    assert now_head in home.text
    active = 'aria-sort="descending"><a class="sort active" href="/?source=ikea&amp;sort=discount'
    assert active in home.text
    asc = client.get("/", params={"source": "ikea", "sort": "price_asc"})
    flipped = 'aria-sort="ascending"><a class="sort active" href="/?source=ikea&amp;sort=price_desc'
    assert flipped in asc.text
    assert '<section id="deals-ikea" data-sort="price_asc">' in asc.text
    partial = client.get("/partials/deals", params={"source": "ikea", "min_discount": 20})
    assert partial.status_code == 200 and partial.text.count("<tr>") == 3
    assert "<table" not in partial.text
    pid = client.get("/v1/deals", params={"limit": 1}).json()["items"][0]["product_id"]
    page = client.get(f"/products/{pid}")
    assert page.status_code == 200 and "KALLAX shelf 12" in page.text
    assert '<meta property="og:title" content="KALLAX shelf 12">' in page.text
    assert '<link rel="canonical" href="http://localhost:8000/products/' in page.text
    assert client.get("/products/999999").status_code == 404


def test_dashboard_tabs(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    uniqlo = client.get("/", params={"source": "uniqlo", "category": "MEN", "q": "x"})
    assert uniqlo.status_code == 200 and 'id="deals-uniqlo"' in uniqlo.text
    assert "90-day low" in uniqlo.text and ">Until<" not in uniqlo.text
    assert '<link rel="canonical" href="http://localhost:8000/?source=uniqlo">' in uniqlo.text
    options = re.findall(r'<option value="([^"]+)"[^>]*>[^<]*\(\d+\)</option>', uniqlo.text)
    assert options == ["MEN"]
    # switching retailer drops the category (it belongs to the other retailer) but keeps the rest
    other = 'aria-selected="false" class="pill" href="/?source=ikea&amp;q=x&amp;sort=discount"'
    assert other in uniqlo.text
    filtered = client.get("/", params={"source": "uniqlo", "min_discount": 5})
    assert '<input type="hidden" name="source" value="uniqlo">' in filtered.text
    for code in ("nope", "acme"):
        missing = client.get("/", params={"source": code}, headers={"accept": "text/html"})
        assert missing.status_code == 404 and "404 · Not found" in missing.text
        assert "<table" not in missing.text and "/static/app.css?v=" in missing.text
    # an adapter that exists but has never run has no stats row: also 404, never a KeyError
    with monkeypatch.context() as m:
        m.setattr(
            queries,
            "stats",
            lambda conn: [
                {
                    "source": "ikea",
                    "products": 0,
                    "on_sale": 0,
                    "last_run_at": None,
                    "last_run_status": None,
                }
            ],
        )
        assert client.get("/", params={"source": "uniqlo"}).status_code == 404
        assert client.get("/", params={"source": "ikea"}).status_code == 200
    rows = client.get("/partials/deals", params={"source": "ikea", "sort": "name"})
    assert rows.status_code == 200 and "<tr" in rows.text and "<table" not in rows.text
    product = client.get("/v1/deals", params={"source": "uniqlo"}).json()["items"][0]
    assert "min_price_90d" in client.get(f"/v1/products/{product['product_id']}").json()


def test_variants_and_labels_on_api_and_pages(client: TestClient, seeded_variants: dict) -> None:
    variants, labels = seeded_variants["variants"], seeded_variants["labels"]
    items = client.get("/v1/deals", params={"limit": 200}).json()["items"]
    tee = next(i for i in items if i["name"] == "AIRism tee")
    assert tee["variants"] == variants
    assert tee["labels"] == labels
    shelf = next(i for i in items if i["name"] == "KALLAX shelf 1")
    assert shelf["variants"] is None and shelf["labels"] == []
    detail = client.get(f"/v1/products/{tee['product_id']}").json()
    assert detail["variants"] == variants and detail["labels"] == labels
    # ?label= filters on membership; unknown labels are rejected
    page = client.get("/v1/deals", params={"label": "last_chance"}).json()
    assert page["total"] == 1 and [i["name"] for i in page["items"]] == ["AIRism tee"]
    assert client.get("/v1/deals", params={"label": "in_store_only"}).json()["total"] == 0
    assert client.get("/v1/deals", params={"label": "bogus"}).status_code == 422
    assert client.get("/", params={"source": "uniqlo", "label": "bogus"}).status_code == 422
    # dashboard rows: counts line, label pills, and the toolbar select keeps the state
    rows = client.get("/", params={"source": "uniqlo", "label": "last_chance"}).text
    assert '<small class="variants">2 of 5 colours · 3 sizes</small>' in rows
    assert '<span class="badge label last_chance">last chance</span>' in rows
    assert '<span class="badge label select_variants">select colours/sizes</span>' in rows
    assert '<option value="last_chance" selected>last chance</option>' in rows
    none = client.get("/", params={"source": "uniqlo", "label": "coming_soon"}).text
    assert "AIRism tee" not in none
    # product page: swatches link to the retailer's colour, sizes as chips, hero from image_url
    page_html = client.get(f"/products/{tee['product_id']}").text
    assert 'href="https://uniqlo.example/E1?colorDisplayCode=64"' in page_html
    chip = '<img src="https://img.example/64_chip.jpg" alt="BLUE" title="BLUE" width="28"'
    assert chip in page_html
    assert 'class="swatch" title="BLACK">BLACK</a>' in page_html  # no chip: name fallback
    assert "<small>2 of 5 available</small>" in page_html
    assert page_html.count('<span class="size">') == 3
    assert 'class="badge label last_chance">last chance</span>' in page_html


def test_dashboard_sort_and_filters_are_url_state(client: TestClient) -> None:
    page = client.get("/", params={"sort": "price_asc", "source": "ikea"})
    assert page.status_code == 200
    assert '<option value="price_asc" selected>' in page.text
    now_cell = r'</td>\s*<td class="num">\$([\d.]+)</td>\s*<td class="num">'
    prices = re.findall(now_cell, page.text)
    assert len(prices) == 13 and Decimal(prices[0]) <= Decimal(prices[1])
    assert "showing 13 of 13 · Price: low to high" in page.text
    baby = client.get("/", params={"source": "ikea", "category": "Baby & kids"})
    assert "showing 6 of 6" in baby.text and baby.text.count("KALLAX shelf") == 6
    assert 'value="Baby &amp; kids" selected' in baby.text
    assert client.get("/", params={"sort": "sideways"}).status_code == 422
    # blank number inputs (what a browser submits for an empty <input type=number>) mean "unset"
    blank = client.get(
        "/", params={"source": "ikea", "min_price": "", "max_price": "", "min_discount": ""}
    )
    assert blank.status_code == 200 and "showing 13 of 13" in blank.text
    assert client.get("/", params={"min_price": "abc"}).status_code == 422
    assert client.get("/", params={"min_discount": "101"}).status_code == 422


def test_dashboard_load_more_uses_cursor(client: TestClient) -> None:
    first = client.get("/v1/deals", params={"sort": "name", "limit": 4, "source": "ikea"}).json()
    partial = client.get(
        "/partials/deals",
        params={"sort": "name", "source": "ikea", "cursor": first["next_cursor"]},
    )
    assert partial.status_code == 200 and "<table" not in partial.text
    names = re.findall(r'<a href="/products/\d+">([^<]+)</a>', partial.text)
    assert names and first["items"][-1]["name"] < names[0]
    assert "KALLAX shelf 1</a>" not in partial.text


def test_static_assets_and_no_inline_code(client: TestClient) -> None:
    js = client.get("/static/app.js")
    assert js.status_code == 200 and js.headers["cache-control"].startswith("public")
    pid = client.get("/v1/deals", params={"limit": 1}).json()["items"][0]["product_id"]
    for path in ("/", "/?source=ikea", f"/products/{pid}"):
        html = client.get(path).text
        assert "<style" not in html and "onclick=" not in html and "onsubmit=" not in html
        assert not NO_INLINE_SCRIPT.search(html), path
        assert "/static/app.js?v=" in html and "/static/app.css?v=" in html


def test_robots_and_sitemap(client: TestClient) -> None:
    robots = client.get("/robots.txt")
    assert robots.status_code == 200 and "Sitemap: http://localhost:8000/sitemap.xml" in robots.text
    assert robots.headers["cache-control"].startswith("public")
    sitemap = client.get("/sitemap.xml")
    assert sitemap.status_code == 200 and sitemap.headers["content-type"].startswith(
        "application/xml"
    )
    assert sitemap.headers["cache-control"].startswith("public")
    ids = [i["product_id"] for i in client.get("/v1/deals", params={"limit": 200}).json()["items"]]
    assert len(ids) == 14
    for pid in ids:
        assert sitemap.text.count(f"<loc>http://localhost:8000/products/{pid}</loc>") == 1
    assert sitemap.text.count("<loc>") == len(ids) + 3  # "/", "/?source=ikea", "/?source=uniqlo"
    assert "<loc>http://localhost:8000/?source=ikea</loc>" in sitemap.text
    assert "<loc>http://localhost:8000/?source=uniqlo</loc>" in sitemap.text


def test_delisted_product_hidden_from_deals_but_page_works(
    client: TestClient, conn: Engine
) -> None:
    """A newer IKEA run without ikea-1 makes it non-current: gone from deals, stats, sitemap;
    its product page and JSON stay reachable."""
    before = {s["source"]: s for s in client.get("/v1/stats").json()}["ikea"]["products"]
    with conn.begin() as c:
        run_id = c.execute(
            text(
                "INSERT INTO ingestion_run (source_id, raw_object_key, status, finished_at) "
                "VALUES (1, 'raw/seed-2', 'succeeded', now()) RETURNING id"
            )
        ).scalar()
        c.execute(
            text(
                "INSERT INTO price_observation (product_id, observed_at, run_id, price, "
                "list_price, retailer_sale_flag) "
                "SELECT p.id, now() + INTERVAL '1 minute', :run, o.price, o.list_price, true "
                "FROM product p JOIN price_observation o ON o.product_id = p.id "
                "WHERE p.source_id = 1 AND p.external_id <> 'ikea-1' AND o.run_id <> :run "
                "  AND o.observed_at = (SELECT max(observed_at) FROM price_observation "
                "                       WHERE product_id = p.id)"
            ),
            {"run": run_id},
        )
        c.execute(text("REFRESH MATERIALIZED VIEW product_price_summary"))
        gone = c.execute(text("SELECT id FROM product WHERE external_id = 'ikea-1'")).scalar_one()
    names = {
        i["name"]
        for i in client.get("/v1/deals", params={"limit": 200, "sort": "name"}).json()["items"]
    }
    assert "KALLAX shelf 1" not in names and "KALLAX shelf 2" in names
    after = {s["source"]: s for s in client.get("/v1/stats").json()}["ikea"]["products"]
    assert after == before - 1
    product = client.get(f"/v1/products/{gone}")
    assert product.status_code == 200 and product.json()["is_current"] is False
    page = client.get(f"/products/{gone}")
    assert page.status_code == 200 and "no longer listed" in page.text
    assert f"/products/{gone}</loc>" not in client.get("/sitemap.xml").text


def test_cache_headers(client: TestClient) -> None:
    assert client.get("/v1/deals").headers["cache-control"] == "public, max-age=300, s-maxage=86400"
    assert client.get("/").headers["cache-control"] == "public, max-age=300, s-maxage=86400"
    pid = client.get("/v1/deals", params={"limit": 1}).json()["items"][0]["product_id"]
    assert client.get(f"/products/{pid}").headers["cache-control"].startswith("public")
    head = client.head("/v1/deals")
    assert head.status_code == 200 and head.headers["cache-control"].startswith("public")
    assert head.content == b""
    assert client.get("/health").headers["cache-control"] == "no-store"
    assert client.get("/v1/products/999999").headers["cache-control"] == "no-store"
    assert client.post("/v1/watches", json={}).headers["cache-control"] == "no-store"
    for path in ("/watches/confirm/nope", "/watches/unsubscribe/nope"):
        assert client.get(path).headers["cache-control"] == "no-store"
        assert client.post(path).headers["cache-control"] == "no-store"


def test_html_error_pages(client: TestClient) -> None:
    html = {"Accept": "text/html,application/xhtml+xml"}
    missing = client.get("/products/999999", headers=html)
    assert missing.status_code == 404 and "<h2>404 · Not found</h2>" in missing.text
    assert client.get("/v1/products/999999", headers=html).json() == {"detail": "product not found"}
    assert client.get("/products/999999").json() == {"detail": "product not found"}


def test_unhandled_error_is_branded_500(
    conn: Engine, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(_conn: object) -> list:
        raise RuntimeError("boom")

    monkeypatch.setattr("pricepulse.api.queries.stats", boom)
    monkeypatch.setattr("pricepulse.api.deps.get_engine", lambda: conn)
    monkeypatch.setattr("pricepulse.api.deps.get_settings", lambda: settings)
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        home = client.get("/", headers={"Accept": "text/html"})
        assert home.status_code == 500 and "Something went wrong" in home.text
        assert home.headers["cache-control"] == "no-store"
        api = client.get("/v1/stats")
        assert api.status_code == 500 and api.json() == {"detail": "internal server error"}
        # the catch-all must not swallow HTTPException
        assert client.get("/products/999999", headers={"Accept": "text/html"}).status_code == 404


def test_database_unavailable_is_503_with_retry_after(
    conn: Engine, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy.exc import OperationalError

    def broken() -> Engine:
        raise OperationalError("connect", {}, ConnectionRefusedError("down"))

    monkeypatch.setattr("pricepulse.api.deps.get_engine", broken)
    monkeypatch.setattr("pricepulse.api.deps.get_settings", lambda: settings)
    with TestClient(create_app()) as client:
        health = client.get("/health")
        assert health.status_code == 503 and health.headers["retry-after"] == "5"
        assert health.json() == {"detail": "database unavailable, retry shortly"}
        assert health.headers["cache-control"] == "no-store"
        home = client.get("/", headers={"Accept": "text/html"})
        assert home.status_code == 503 and "Database unavailable" in home.text
        assert home.headers["retry-after"] == "5"
