"""UNIQLO US catalog via the commerce v5 products endpoint.

Only `path`, `limit`, `offset` are sent: uniqlo.com/robots.txt disallows the filter query
parameters (`flagCodes`, `categoryIds`, `priceRanges`, ...), so sale detection is client-side.
The API exposes a `discount` flag but never the original price (base == promo), so the
percentage off for UNIQLO is derived from our own price history downstream.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Any

import httpx

from pricepulse.domain.models import ProductSnapshot
from pricepulse.sources.base import SourceError, new_raw_payload
from pricepulse.sources.http import get_json

BASE = "https://www.uniqlo.com/us/api/commerce/v5/en/products"
GENDER_PATHS = {"22210": "WOMEN", "22211": "MEN", "22212": "KIDS", "22213": "BABY"}
PAGE_SIZE = 100  # server maximum


class UniqloSource:
    code = "uniqlo"
    name = "UNIQLO US"
    base_url = "https://www.uniqlo.com/us/en/"
    layout = "history"

    def fetch(self, client: httpx.Client) -> dict[str, Any]:
        raw = new_raw_payload(self.code)
        for path in GENDER_PATHS:
            offset = 0
            while True:
                params = {"path": path, "limit": PAGE_SIZE, "offset": offset, "httpFailure": "true"}
                url, status, body = get_json(client, BASE, params)
                if body.get("status") != "ok":
                    raise SourceError(f"uniqlo: status={body.get('status')!r} for path {path}")
                raw["requests"].append({"url": url, "status": status, "path": path, "body": body})
                pagination = body["result"]["pagination"]
                count = pagination.get("count", 0)
                if count == 0 or offset + count >= pagination.get("total", 0):
                    break
                offset += count
        return raw

    def parse(self, raw: dict[str, Any]) -> list[ProductSnapshot]:
        """One snapshot per productId. Items listed under several gender paths (unisex basics)
        are kept once and categorised UNISEX."""
        seen: dict[str, ProductSnapshot] = {}
        for request in raw["requests"]:
            category = GENDER_PATHS.get(str(request.get("path")), None)
            for item in request["body"]["result"]["items"]:
                snapshot = _to_snapshot(item, category)
                existing = seen.get(snapshot.external_id)
                if existing is None:
                    seen[snapshot.external_id] = snapshot
                elif existing.category not in (category, "UNISEX"):
                    seen[snapshot.external_id] = replace(existing, category="UNISEX")
        return list(seen.values())


def _first_image(item: dict[str, Any]) -> str | None:
    main = (item.get("images") or {}).get("main") or {}
    for entry in main.values():
        if isinstance(entry, dict) and entry.get("image"):
            return entry["image"]
        if isinstance(entry, str):
            return entry
    return None


def _to_snapshot(item: dict[str, Any], category: str | None) -> ProductSnapshot:
    prices = item["prices"]
    base = Decimal(str(prices["base"]["value"]))
    promo = prices.get("promo")
    price, list_price = base, None
    if promo and Decimal(str(promo["value"])) < base:
        price, list_price = Decimal(str(promo["value"])), base
    flags = (item.get("representative") or {}).get("flags") or {}
    flagged = any(f.get("code") == "discount" for f in flags.get("priceFlags") or [])
    product_id = item["productId"]
    currency = prices["base"].get("currency")
    currency_code = currency.get("code", "USD") if isinstance(currency, dict) else "USD"
    return ProductSnapshot(
        source="uniqlo",
        external_id=product_id,
        name=item["name"],
        category=category or item.get("genderCategory"),
        url=f"https://www.uniqlo.com/us/en/products/{product_id}/{item.get('priceGroup', '00')}",
        image_url=_first_image(item),
        currency=currency_code,
        price=price,
        list_price=list_price,
        retailer_sale_flag=flagged,
        retailer_tag="discount" if flagged else None,
        valid_to=None,
    )
