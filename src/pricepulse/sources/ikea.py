"""IKEA US offers and last-chance items via the public search-result-page JSON endpoint.

The offer tags (e.g. FAMILY_PRICE) are discovered from the OFFERS filter on every run rather
than hardcoded, because they change seasonally. `size=1000` (the server maximum) returns all
offers in one page today; if the server ever caps the page (`end < max`) we re-query per
top-level category.

Last-chance items (`f-last-chance=true`: discontinued, "while supply lasts") are not in the
OFFERS filter. The endpoint has no offset parameter and its `max` under-reports (1,480 while
four sort orders together return ~1,900 distinct items), so that set is fetched as the union of
a few sort orders at the maximum page size — bounded, and complete as far as measurable.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Any

import httpx

from pricepulse.domain.models import ProductSnapshot
from pricepulse.sources.base import SourceError, new_raw_payload
from pricepulse.sources.http import get_json

BASE = "https://sik.search.blue.cdtapps.com/us/en/search-result-page"
PAGE_SIZE = 1000  # server maximum
LAST_CHANCE_SORTS = ("PRICE_LOW_TO_HIGH", "PRICE_HIGH_TO_LOW", "NEWEST", "RELEVANCE")


def _filter_values(body: dict[str, Any], filter_id: str) -> list[str]:
    filters = body["searchResultPage"]["products"].get("filters", [])
    for f in filters:
        if f.get("id") == filter_id:
            return [v["id"] for v in f.get("values", []) if v.get("count", 0) > 0]
    return []


def _main(body: dict[str, Any]) -> dict[str, Any]:
    try:
        return body["searchResultPage"]["products"]["main"]
    except (KeyError, TypeError) as exc:
        raise SourceError("ikea: response lacks searchResultPage.products.main") from exc


class IkeaSource:
    code = "ikea"
    name = "IKEA US"
    base_url = "https://www.ikea.com/us/en/"
    layout = "list_price"

    def fetch(self, client: httpx.Client) -> dict[str, Any]:
        raw = new_raw_payload(self.code)
        url, status, index = get_json(client, BASE, {"size": 1, "types": "PRODUCT"})
        # Discovery only (offer tags); its one arbitrary item is not an offer.
        raw["requests"].append({"url": url, "status": status, "role": "index", "body": index})
        tags = _filter_values(index, "OFFERS")
        for tag in tags:
            params = {
                "f-offers": tag,
                "size": PAGE_SIZE,
                "types": "PRODUCT",
                "sort": "PRICE_LOW_TO_HIGH",
            }
            url, status, body = get_json(client, BASE, params)
            raw["requests"].append({"url": url, "status": status, "body": body})
            main = _main(body)
            if main.get("end", 0) < main.get("max", 0):
                for key in _filter_values(body, "CATEGORIES"):
                    url, status, sub = get_json(client, BASE, {**params, "f-subcategories": key})
                    raw["requests"].append({"url": url, "status": status, "body": sub})
        for sort in LAST_CHANCE_SORTS:
            params = {"f-last-chance": "true", "size": PAGE_SIZE, "types": "PRODUCT", "sort": sort}
            url, status, body = get_json(client, BASE, params)
            raw["requests"].append(
                {"url": url, "status": status, "role": "last_chance", "body": body}
            )
            main = _main(body)
            if main.get("end", 0) >= main.get("max", 0):
                break
        return raw

    def parse(self, raw: dict[str, Any]) -> list[ProductSnapshot]:
        """One snapshot per itemNo. An item listed both as an offer and as last chance keeps
        its first record and the union of the labels."""
        seen: dict[str, ProductSnapshot] = {}
        for request in raw["requests"]:
            if request.get("role") == "index":
                continue
            body = request["body"]
            main = body.get("searchResultPage", {}).get("products", {}).get("main")
            if not main:
                continue
            for item in main.get("items", []):
                if item.get("type") != "PRODUCT":
                    continue
                snapshot = _to_snapshot(item["product"])
                if snapshot is None:
                    continue
                existing = seen.get(snapshot.external_id)
                if existing is None:
                    seen[snapshot.external_id] = snapshot
                elif not set(snapshot.labels) <= set(existing.labels):
                    merged = existing.labels + tuple(
                        label for label in snapshot.labels if label not in existing.labels
                    )
                    seen[snapshot.external_id] = replace(existing, labels=merged)
        return list(seen.values())


def _money(part: dict[str, Any] | None) -> Decimal | None:
    """Rebuild a Decimal from IKEA's display parts; `wholeNumber` may carry thousands
    separators ("1,049")."""
    if not part or part.get("wholeNumber") in (None, ""):
        return None
    whole = "".join(ch for ch in str(part["wholeNumber"]) if ch.isdigit())
    decimals = "".join(ch for ch in str(part.get("decimals") or "00") if ch.isdigit()) or "00"
    return Decimal(f"{whole}.{decimals}")


def _price_tag(sales: dict[str, Any]) -> str | None:
    """First price tag that is not the informational NEW_PRODUCT; `tags[]` is multi-valued,
    `tag` the legacy single value."""
    tags = [t for t in sales.get("tags") or [] if t and t != "NEW_PRODUCT"]
    tag = tags[0] if tags else sales.get("tag")
    return tag if tag not in (None, "", "NONE") else None


def _labels(p: dict[str, Any], sales: dict[str, Any]) -> tuple[str, ...]:
    labels = []
    if sales.get("tag") == "DISCONTINUED_PRODUCT" or p.get("lastChance"):
        labels.append("last_chance")
    if (p.get("badge") or {}).get("type") == "IN_STORE_OFFER_ONLY":
        labels.append("in_store_only")
    return tuple(labels)


def _to_snapshot(p: dict[str, Any]) -> ProductSnapshot | None:
    if p.get("onlineSellable") is False:
        return None
    sales = p["salesPrice"]
    tag = _price_tag(sales)
    valid_to = sales.get("validTo")
    category_path = p.get("categoryPath") or []
    return ProductSnapshot(
        source="ikea",
        external_id=p["itemNo"],
        name=f"{p.get('name', '')} {p.get('typeName', '')}".strip(),
        category=category_path[0]["name"] if category_path else p.get("typeName"),
        url=p["pipUrl"],
        image_url=p.get("mainImageUrl"),
        currency=sales.get("currencyCode", "USD"),
        price=Decimal(str(sales["numeral"])),
        list_price=_money(sales.get("previous")),
        retailer_sale_flag=tag is not None,
        retailer_tag=tag,
        valid_to=date.fromisoformat(valid_to) if valid_to else None,
        labels=_labels(p, sales),
    )
