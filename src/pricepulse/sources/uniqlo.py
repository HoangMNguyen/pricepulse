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

Per-SKU stock comes from a second stage: one `l2s` call per (productId, priceGroup) — ~1,300 a
run — over `L2S_WORKERS` threads, each pausing `PAUSE_SECONDS` after its request (measured
3.8 calls/s, p95 0.5 s, all 200s at that rate), so the stage takes ~6 min and the run fits the
900 s Lambda timeout. The l2s body carries codes only; names come from the list feed, whose
options are stock-filtered, so an option sold out in every colour has no name and is skipped.
A pair whose l2s call fails after retries keeps its list-level variants (`in_stock` unknown →
true) rather than failing the run.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import httpx

from pricepulse.domain.models import ProductSnapshot
from pricepulse.sources.base import SourceError, new_raw_payload
from pricepulse.sources.http import get_json

log = logging.getLogger(__name__)

BASE = "https://www.uniqlo.com/us/api/commerce/v5/en/products"
GENDER_PATHS = {"22210": "WOMEN", "22211": "MEN", "22212": "KIDS", "22213": "BABY"}
PAGE_SIZE = 100  # server maximum
L2S_WORKERS = 3
L2S_PARAMS = {"withPrices": "true", "withStocks": "true"}
IN_STOCK_STATUSES = frozenset({"IN_STOCK", "LOW_STOCK"})

Pair = tuple[str, str]  # (productId, priceGroup)


class UniqloSource:
    code = "uniqlo"
    name = "UNIQLO US"
    base_url = "https://www.uniqlo.com/us/en/"
    layout = "history"

    def fetch(self, client: httpx.Client) -> dict[str, Any]:
        raw = new_raw_payload(self.code)
        pairs: dict[Pair, None] = {}  # insertion-ordered set
        for path in GENDER_PATHS:
            offset = 0
            while True:
                params = {"path": path, "limit": PAGE_SIZE, "offset": offset, "httpFailure": "true"}
                url, status, body = get_json(client, BASE, params)
                if body.get("status") != "ok":
                    raise SourceError(f"uniqlo: status={body.get('status')!r} for path {path}")
                raw["requests"].append({"url": url, "status": status, "path": path, "body": body})
                for item in body["result"]["items"]:
                    pairs[(item["productId"], _price_group(item))] = None
                pagination = body["result"]["pagination"]
                count = pagination.get("count", 0)
                if count == 0 or offset + count >= pagination.get("total", 0):
                    break
                offset += count
        with ThreadPoolExecutor(max_workers=L2S_WORKERS) as pool:
            for request in pool.map(lambda pair: _fetch_l2s(client, pair), pairs):
                if request is not None:
                    raw["requests"].append(request)
        return raw

    def parse(self, raw: dict[str, Any]) -> list[ProductSnapshot]:
        """One snapshot per (productId, priceGroup). Items listed under several gender paths
        (unisex basics) are kept once and categorised UNISEX."""
        pages = [r for r in raw["requests"] if r.get("role") != "l2s"]
        stock = {
            (r["product_id"], r["price_group"]): _stock(r["body"])
            for r in raw["requests"]
            if r.get("role") == "l2s"
        }
        stock_at = raw["fetched_at"] if stock else None
        base_prices: dict[str, Decimal] = {}
        for request in pages:
            for item in request["body"]["result"]["items"]:
                if _price_group(item) == "00":
                    base_prices[item["productId"]] = _decimal(item["prices"]["base"]["value"])
        seen: dict[str, ProductSnapshot] = {}
        for request in pages:
            category = GENDER_PATHS.get(str(request.get("path")), None)
            for item in request["body"]["result"]["items"]:
                pair = (item["productId"], _price_group(item))
                snapshot = _to_snapshot(item, category, base_prices, stock.get(pair), stock_at)
                existing = seen.get(snapshot.external_id)
                if existing is None:
                    seen[snapshot.external_id] = snapshot
                elif existing.category not in (category, "UNISEX"):
                    seen[snapshot.external_id] = replace(existing, category="UNISEX")
        return list(seen.values())


def _fetch_l2s(client: httpx.Client, pair: Pair) -> dict[str, Any] | None:
    product_id, price_group = pair
    url = f"{BASE}/{product_id}/price-groups/{price_group}/l2s"
    try:
        url, status, body = get_json(client, url, L2S_PARAMS)
        if body.get("status") != "ok":
            raise SourceError(f"status={body.get('status')!r}")
    except (httpx.HTTPError, ValueError, SourceError) as exc:
        # After retries: keep the run, the product falls back to list-level variants.
        log.warning("uniqlo: l2s %s/%s skipped: %s", product_id, price_group, exc)
        return None
    return {
        "url": url,
        "status": status,
        "role": "l2s",
        "product_id": product_id,
        "price_group": price_group,
        "body": body,
    }


def _stock(body: dict[str, Any]) -> dict[str, set[str]]:
    """Colour displayCode -> size displayCodes with at least one buyable SKU (any length)."""
    result = body["result"]
    stocks = result.get("stocks") or {}
    in_stock: dict[str, set[str]] = defaultdict(set)
    for sku in result.get("l2s") or []:
        if (stocks.get(sku.get("l2Id")) or {}).get("statusCode") in IN_STOCK_STATUSES:
            in_stock[str(sku["color"]["displayCode"])].add(str(sku["size"]["displayCode"]))
    return in_stock


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


def _variants(
    item: dict[str, Any], stock: dict[str, set[str]] | None, stock_at: str | None
) -> dict[str, Any] | None:
    colors = item.get("colors")
    sizes = item.get("sizes")
    if colors is None and sizes is None:
        return None
    images = item.get("images") or {}
    main, chip = images.get("main") or {}, images.get("chip") or {}
    # Retailer order = displayCode order; only named (list-feed) sizes exist for us.
    size_names = {
        str(s.get("displayCode") or s.get("code") or ""): s.get("name") for s in sizes or []
    }
    size_codes = sorted(code for code, name in size_names.items() if name)
    colours = []
    for c in colors or []:
        code = str(c.get("displayCode") or c.get("code") or "")
        hero = main.get(code)
        colour: dict[str, Any] = {
            "code": code,
            "name": c.get("name"),
            "image": hero.get("image") if isinstance(hero, dict) else hero,
            "chip": chip.get(code),
        }
        if stock is not None:
            available = stock.get(code, set())
            colour["sizes"] = [size_names[s] for s in size_codes if s in available]
        colours.append(colour)
    variants: dict[str, Any] = {
        "colours": colours,
        "sizes": [
            {
                "code": code,
                "name": size_names[code],
                "in_stock": stock is None or any(code in s for s in stock.values()),
            }
            for code in size_codes
        ],
        "colour_total": max(len(chip), len(colours)),
    }
    lengths = [p.get("name") for p in item.get("plds") or []]
    if lengths and lengths != ["-"]:
        variants["lengths"] = lengths
    if stock is not None:
        variants["stock_at"] = stock_at
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
    item: dict[str, Any],
    category: str | None,
    base_prices: dict[str, Decimal],
    stock: dict[str, set[str]] | None,
    stock_at: str | None,
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
    variants = _variants(item, stock, stock_at)
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
