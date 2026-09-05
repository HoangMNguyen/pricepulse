"""Re-record trimmed API fixtures from the live endpoints.

    uv run python scripts/record_fixtures.py [ikea|uniqlo]

IKEA: one request per endpoint, the first three products (item 00473546 first when present) and
only the OFFERS/CATEGORIES filters. UNIQLO: walks the MEN path and keeps at most eight items
that together cover a style listed under two price groups, a `limitedOffer`, a multi-colour
multi-size item, a `discount`-flagged item and a plain one, with their `colors`/`sizes`/`plds`.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

from pricepulse.config import Settings
from pricepulse.sources import ikea, uniqlo
from pricepulse.sources.http import get_json, make_client

MULTI = 3  # "multi-colour / multi-size" threshold for the UNIQLO fixture

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
IKEA_ANCHOR = "00473546"


def trim_ikea(body: dict[str, Any], keep: int) -> dict[str, Any]:
    out = copy.deepcopy(body)
    products = out["searchResultPage"]["products"]
    items = [i for i in products["main"]["items"] if i.get("type") == "PRODUCT"]
    items.sort(key=lambda i: i["product"]["itemNo"] != IKEA_ANCHOR)
    products["main"]["items"] = items[:keep]
    products["main"]["end"] = products["main"]["max"] = len(products["main"]["items"])
    products["filters"] = [
        f for f in products["filters"] if f.get("id") in ("OFFERS", "CATEGORIES")
    ]
    out["searchResultPage"] = {"products": products}
    return out


def _price_flags(item: dict[str, Any]) -> set[str]:
    return {f["code"] for f in item["representative"]["flags"]["priceFlags"]}


def _product_flags(item: dict[str, Any]) -> set[str]:
    return {f["code"] for f in item["representative"]["flags"].get("productFlags", [])}


def trim_uniqlo(pages: list[dict[str, Any]], keep: int) -> dict[str, Any]:
    items = [i for page in pages for i in page["result"]["items"]]
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        groups.setdefault(item["productId"], []).append(item)
    chosen: list[dict[str, Any]] = []
    # A style split into price groups, preferably one whose groups are priced differently.
    split = [rows for rows in groups.values() if len({r["priceGroup"] for r in rows}) > 1]
    split.sort(key=lambda rows: len({r["prices"]["base"]["value"] for r in rows}), reverse=True)
    if split:
        chosen.extend(split[0])
    wanted = [
        lambda i: "limitedOffer" in _price_flags(i),
        lambda i: len(i.get("colors") or []) >= MULTI and len(i.get("sizes") or []) >= MULTI,
        lambda i: "colorSizeLimitedPrice" in _price_flags(i),
        lambda i: "extraLargeStoreOnly" in _product_flags(i),
        lambda i: "discount" in _price_flags(i),
        lambda i: "discount" not in _price_flags(i),
    ]
    for want in wanted:
        pick = next((i for i in items if want(i) and i not in chosen), None)
        if pick is not None and len(chosen) < keep:
            chosen.append(pick)
    out = copy.deepcopy(pages[0])
    out["result"]["items"] = copy.deepcopy(chosen)
    out["result"]["pagination"] = {"total": len(chosen), "offset": 0, "count": len(chosen)}
    return out


def _uniqlo_pages(client: Any, path: str) -> list[dict[str, Any]]:
    pages, offset = [], 0
    while True:
        params = {"path": path, "limit": 100, "offset": offset, "httpFailure": "true"}
        _, _, body = get_json(client, uniqlo.BASE, params)
        pages.append(body)
        pagination = body["result"]["pagination"]
        offset += pagination["count"]
        if pagination["count"] == 0 or offset >= pagination["total"]:
            return pages


def main(only: str | None) -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    with make_client(Settings()) as client:
        if only in (None, "ikea"):
            _, _, index = get_json(client, ikea.BASE, {"size": 1, "types": "PRODUCT"})
            _, _, offers = get_json(
                client,
                ikea.BASE,
                {
                    "f-offers": "FAMILY_PRICE",
                    "size": 500,
                    "types": "PRODUCT",
                    "sort": "PRICE_LOW_TO_HIGH",
                },
            )
            (FIXTURES / "ikea_index.json").write_text(json.dumps(trim_ikea(index, 0), indent=1))
            (FIXTURES / "ikea_offers.json").write_text(json.dumps(trim_ikea(offers, 3), indent=1))
        if only in (None, "uniqlo"):
            men = _uniqlo_pages(client, "22211")
            (FIXTURES / "uniqlo_men_page0.json").write_text(
                json.dumps(trim_uniqlo(men, 8), indent=1)
            )
    print(f"wrote fixtures to {FIXTURES}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
