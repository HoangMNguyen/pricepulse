"""Re-record trimmed API fixtures from the live endpoints (one request per endpoint).

    uv run python scripts/record_fixtures.py

Keeps the first three products per response (IKEA: item 00473546 first when present) and only
the OFFERS/CATEGORIES filters so the fixtures stay small and reviewable.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from pricepulse.config import Settings
from pricepulse.sources import ikea, uniqlo
from pricepulse.sources.http import get_json, make_client

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


def trim_uniqlo(body: dict[str, Any], keep: int) -> dict[str, Any]:
    out = copy.deepcopy(body)
    items = out["result"]["items"]
    flagged = [
        i
        for i in items
        if any(f["code"] == "discount" for f in i["representative"]["flags"]["priceFlags"])
    ]
    plain = [i for i in items if i not in flagged]
    chosen = (flagged[:2] + plain)[:keep]
    for item in chosen:
        for heavy in ("colors", "sizes", "plds"):
            item.pop(heavy, None)
    out["result"]["items"] = chosen
    out["result"]["pagination"] = {"total": len(chosen), "offset": 0, "count": len(chosen)}
    return out


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    with make_client(Settings()) as client:
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
        _, _, men = get_json(
            client, uniqlo.BASE, {"path": "22211", "limit": 100, "offset": 0, "httpFailure": "true"}
        )
    (FIXTURES / "ikea_index.json").write_text(json.dumps(trim_ikea(index, 0), indent=1))
    (FIXTURES / "ikea_offers.json").write_text(json.dumps(trim_ikea(offers, 3), indent=1))
    (FIXTURES / "uniqlo_men_page0.json").write_text(json.dumps(trim_uniqlo(men, 3), indent=1))
    print(f"wrote 3 fixtures to {FIXTURES}")


if __name__ == "__main__":
    main()
