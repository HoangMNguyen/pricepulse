"""UNIQLO US catalog via the commerce v5 products endpoint.

Only `path`, `limit`, `offset` are sent: uniqlo.com/robots.txt disallows the filter query
parameters (`flagCodes`, `categoryIds`, `priceRanges`, ...), so sale detection is client-side.

Prices are per (productId, priceGroup): a style whose clearance colours sell cheaper is listed
twice, once per group (`/products/{id}/00` and `/01`), so every group is its own product here
(`external_id` = `E424873-000` for group 00, `E424873-000/01` otherwise). The API exposes a
`discount` flag but the original price only through such splits — group 00's `base` becomes
the sibling groups' `list_price`; otherwise (base == promo) the percentage off is derived from
our own price history downstream. `colors[]`/`sizes[]` on the list are stock-filtered by the
retailer (a listed colour has at least one buyable SKU in that group).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
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
        """One snapshot per (productId, priceGroup). Items listed under several gender paths
        (unisex basics) are kept once and categorised UNISEX."""
        base_prices: dict[str, Decimal] = {}
        for request in raw["requests"]:
            for item in request["body"]["result"]["items"]:
                if _price_group(item) == "00":
                    base_prices[item["productId"]] = _decimal(item["prices"]["base"]["value"])
        seen: dict[str, ProductSnapshot] = {}
        for request in raw["requests"]:
            category = GENDER_PATHS.get(str(request.get("path")), None)
            for item in request["body"]["result"]["items"]:
                snapshot = _to_snapshot(item, category, base_prices)
                existing = seen.get(snapshot.external_id)
                if existing is None:
                    seen[snapshot.external_id] = snapshot
                elif existing.category not in (category, "UNISEX"):
                    seen[snapshot.external_id] = replace(existing, category="UNISEX")
        return list(seen.values())


PRODUCT_FLAG_LABELS = {
    "extraLargeStoreOnly": "xl_store_only",
    "onlineOnly": "online_only",
    "comingSoon": "coming_soon",
}
PRICE_FLAG_LABELS = {"colorSizeLimitedPrice": "select_variants"}


def _decimal(value: Any) -> Decimal:
    return Decimal(str(value))


def _price_group(item: dict[str, Any]) -> str:
    return str(item.get("priceGroup") or "00")


def _labels(flags: list[dict[str, Any]], mapping: dict[str, str]) -> tuple[str, ...]:
    return tuple(mapping[f["code"]] for f in flags if f.get("code") in mapping)


def _limited_offer_end(price_flags: list[dict[str, Any]]) -> date | None:
    """`limitedOffer.effectiveTime.end` is epoch seconds (UTC)."""
    for flag in price_flags:
        if flag.get("code") != "limitedOffer":
            continue
        end = (flag.get("effectiveTime") or {}).get("end")
        if end:
            return datetime.fromtimestamp(int(end), UTC).date()
    return None


def _variants(item: dict[str, Any]) -> dict[str, Any] | None:
    colors = item.get("colors")
    sizes = item.get("sizes")
    if colors is None and sizes is None:
        return None
    images = item.get("images") or {}
    main, chip = images.get("main") or {}, images.get("chip") or {}
    colours = []
    for c in colors or []:
        code = str(c.get("displayCode") or c.get("code") or "")
        hero = main.get(code)
        colours.append(
            {
                "code": code,
                "name": c.get("name"),
                "image": hero.get("image") if isinstance(hero, dict) else hero,
                "chip": chip.get(code),
            }
        )
    variants: dict[str, Any] = {
        "colours": colours,
        "sizes": [s.get("name") for s in sizes or []],
        "colour_total": max(len(chip), len(colours)),
    }
    lengths = [p.get("name") for p in item.get("plds") or []]
    if lengths and lengths != ["-"]:
        variants["lengths"] = lengths
    return variants


def _first_image(item: dict[str, Any], variants: dict[str, Any] | None) -> str | None:
    """The first listed (buyable) colour's hero image, else the first main image."""
    for colour in (variants or {}).get("colours") or []:
        if colour.get("image"):
            return colour["image"]
    main = (item.get("images") or {}).get("main") or {}
    for entry in main.values():
        if isinstance(entry, dict) and entry.get("image"):
            return entry["image"]
        if isinstance(entry, str):
            return entry
    return None


def _to_snapshot(
    item: dict[str, Any], category: str | None, base_prices: dict[str, Decimal]
) -> ProductSnapshot:
    prices = item["prices"]
    base = _decimal(prices["base"]["value"])
    promo = prices.get("promo")
    price, list_price = base, None
    if promo and _decimal(promo["value"]) < base:
        price, list_price = _decimal(promo["value"]), base
    product_id = item["productId"]
    price_group = _price_group(item)
    if price_group != "00" and list_price is None:
        list_price = base_prices.get(product_id)
    flags = (item.get("representative") or {}).get("flags") or {}
    price_flags = flags.get("priceFlags") or []
    product_flags = flags.get("productFlags") or []
    flagged = any(f.get("code") == "discount" for f in price_flags)
    labels = _labels(product_flags, PRODUCT_FLAG_LABELS) + _labels(price_flags, PRICE_FLAG_LABELS)
    currency = prices["base"].get("currency")
    currency_code = currency.get("code", "USD") if isinstance(currency, dict) else "USD"
    variants = _variants(item)
    return ProductSnapshot(
        source="uniqlo",
        external_id=product_id if price_group == "00" else f"{product_id}/{price_group}",
        name=item["name"],
        category=category or item.get("genderCategory"),
        url=f"https://www.uniqlo.com/us/en/products/{product_id}/{price_group}",
        image_url=_first_image(item, variants),
        currency=currency_code,
        price=price,
        list_price=list_price,
        retailer_sale_flag=flagged,
        retailer_tag="discount" if flagged else None,
        valid_to=_limited_offer_end(price_flags),
        variants=variants,
        labels=labels,
    )
