import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx

from pricepulse.config import Settings
from pricepulse.sources.http import make_client
from pricepulse.sources.ikea import LAST_CHANCE_SORTS, IkeaSource
from pricepulse.sources.uniqlo import UniqloSource

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def ikea_raw() -> dict:
    return {
        "source": "ikea",
        "fetched_at": "2026-09-04T13:00:00+00:00",
        "requests": [
            {"url": "index", "status": 200, "body": _load("ikea_index.json")},
            {"url": "offers", "status": 200, "body": _load("ikea_offers.json")},
        ],
    }


def uniqlo_raw() -> dict:
    return {
        "source": "uniqlo",
        "fetched_at": "2026-09-04T13:10:00+00:00",
        "requests": [
            {"url": "p0", "status": 200, "path": "22211", "body": _load("uniqlo_men_page0.json")}
        ],
    }


def test_ikea_parse_maps_fields() -> None:
    snaps = {s.external_id: s for s in IkeaSource().parse(ikea_raw())}
    assert len(snaps) == 5
    s = snaps["00473546"]
    assert s.price == Decimal("79.99")
    assert s.list_price == Decimal("95.00")
    assert s.retailer_sale_flag is True
    assert s.retailer_tag == "FAMILY_PRICE"
    assert s.valid_to == date(2026, 9, 7)
    assert s.currency == "USD"
    assert s.url.startswith("https://www.ikea.com/us/en/p/")
    assert s.name and s.category
    assert s.variants is None and s.labels == ()


def test_ikea_parse_labels_and_price_tag() -> None:
    snaps = {s.external_id: s for s in IkeaSource().parse(ikea_raw())}
    discontinued = snaps["40599596"]
    assert discontinued.retailer_tag == "DISCONTINUED_PRODUCT"
    assert discontinued.labels == ("last_chance",)
    assert (discontinued.price, discontinued.list_price) == (Decimal("0.99"), Decimal("1.99"))
    assert snaps["00434277"].labels == ("last_chance", "in_store_only")
    raw = ikea_raw()
    item = raw["requests"][1]["body"]["searchResultPage"]["products"]["main"]["items"][0]
    item["product"]["salesPrice"]["tags"] = ["NEW_PRODUCT", "FAMILY_PRICE"]
    item["product"]["salesPrice"]["tag"] = "NEW_PRODUCT"
    s = {x.external_id: x for x in IkeaSource().parse(raw)}["00473546"]
    assert s.retailer_tag == "FAMILY_PRICE"


def test_ikea_parse_merges_last_chance_page() -> None:
    """An item in both the offers page and the last-chance page keeps its first record with
    the union of the labels; items only on the last-chance page are added."""
    raw = ikea_raw()
    offers = raw["requests"][1]["body"]
    page = json.loads(json.dumps(offers))
    items = page["searchResultPage"]["products"]["main"]["items"]
    items[0]["product"]["lastChance"] = True
    items[1]["product"]["itemNo"] = "99999999"
    items[1]["product"]["lastChance"] = True
    page["searchResultPage"]["products"]["main"]["items"] = items[:2]
    raw["requests"].append({"url": "lc", "status": 200, "role": "last_chance", "body": page})
    snaps = {s.external_id: s for s in IkeaSource().parse(raw)}
    assert len(snaps) == 6
    assert snaps["00473546"].labels == ("last_chance",)
    assert snaps["00473546"].retailer_tag == "FAMILY_PRICE"
    assert snaps["99999999"].labels == ("last_chance",)


def test_ikea_parse_skips_not_online_sellable_and_dedupes() -> None:
    raw = ikea_raw()
    items = raw["requests"][1]["body"]["searchResultPage"]["products"]["main"]["items"]
    items[1]["product"]["onlineSellable"] = False
    raw["requests"].append(raw["requests"][1])  # duplicated page
    snaps = IkeaSource().parse(raw)
    assert len(snaps) == 4
    assert items[1]["product"]["itemNo"] not in {s.external_id for s in snaps}


def test_ikea_parse_skips_index_request() -> None:
    """The size=1 discovery request is metadata: its one arbitrary item is not an offer."""
    raw = ikea_raw()
    offers_item = raw["requests"][1]["body"]["searchResultPage"]["products"]["main"]["items"][0]
    stray = json.loads(json.dumps(offers_item))
    stray["product"]["itemNo"] = "99999999"
    raw["requests"][0]["body"]["searchResultPage"]["products"]["main"]["items"] = [stray]
    assert "99999999" in {s.external_id for s in IkeaSource().parse(raw)}  # legacy payload
    raw["requests"][0]["role"] = "index"
    assert "99999999" not in {s.external_id for s in IkeaSource().parse(raw)}


def test_uniqlo_parse_flagged_item_has_no_list_price() -> None:
    snaps = {s.external_id: s for s in UniqloSource().parse(uniqlo_raw())}
    assert len(snaps) == 8
    flagged = snaps["E484249-000/01"]  # group 00 is not in the payload: no list price
    assert flagged.retailer_sale_flag is True
    assert flagged.retailer_tag == "discount"
    assert flagged.list_price is None
    assert flagged.price == Decimal("49.9")
    assert flagged.currency == "USD"
    assert flagged.category == "MEN"
    assert flagged.url == "https://www.uniqlo.com/us/en/products/E484249-000/01"
    assert flagged.image_url and flagged.image_url.startswith("https://image.uniqlo.com/")
    assert flagged.labels == ("select_variants",)
    plain = snaps["E484610-000"]
    assert plain.retailer_sale_flag is False and plain.retailer_tag is None
    assert plain.url == "https://www.uniqlo.com/us/en/products/E484610-000/00"


def test_uniqlo_parse_splits_price_groups_with_group_00_as_list_price() -> None:
    snaps = {s.external_id: s for s in UniqloSource().parse(uniqlo_raw())}
    full = snaps["E450544-000"]
    assert (full.price, full.list_price) == (Decimal("99.9"), None)
    assert full.labels == ("coming_soon",)
    clearance = snaps["E450544-000/02"]
    assert (clearance.price, clearance.list_price) == (Decimal("5.9"), Decimal("99.9"))
    assert clearance.retailer_tag == "discount"
    assert clearance.url == "https://www.uniqlo.com/us/en/products/E450544-000/02"
    assert snaps["E450544-000/01"].list_price == Decimal("99.9")


def test_uniqlo_parse_limited_offer_sets_valid_to() -> None:
    snaps = {s.external_id: s for s in UniqloSource().parse(uniqlo_raw())}
    assert snaps["E491096-000"].valid_to == date(2026, 9, 11)
    assert snaps["E484610-000"].valid_to is None


def test_uniqlo_parse_variants_and_labels() -> None:
    snaps = {s.external_id: s for s in UniqloSource().parse(uniqlo_raw())}
    v = snaps["E450544-000"].variants
    assert v is not None
    assert set(v) == {"colours", "sizes", "colour_total"}  # lengths omitted when just "-"
    assert len(v["colours"]) == 5 and v["colour_total"] == 17
    colour = v["colours"][0]
    assert set(colour) == {"code", "name", "image", "chip"}
    assert colour["code"] and colour["name"]
    assert colour["image"] == snaps["E450544-000"].image_url
    assert colour["chip"].startswith("https://image.uniqlo.com/")
    assert v["sizes"] == ["XS", "S", "M", "L", "XL", "XXL", "3XL"]
    assert snaps["E484610-000"].labels == ("xl_store_only",)


def test_uniqlo_parse_dual_price_uses_promo() -> None:
    raw = uniqlo_raw()
    item = raw["requests"][0]["body"]["result"]["items"][2]
    assert (item["productId"], item["priceGroup"]) == ("E450544-000", "00")
    item["prices"]["promo"] = {"currency": {"code": "USD"}, "value": 29.9}
    s = {x.external_id: x for x in UniqloSource().parse(raw)}["E450544-000"]
    assert (s.price, s.list_price) == (Decimal("29.9"), Decimal("99.9"))


def test_uniqlo_fetch_paginates_and_uses_only_allowed_params() -> None:
    seen: list[dict] = []
    page = _load("uniqlo_men_page0.json")

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        seen.append(params)
        body = json.loads(json.dumps(page))
        offset = int(params["offset"])
        body["result"]["pagination"] = {
            "total": 5,
            "offset": offset,
            "count": 3 if offset == 0 else 2,
        }
        return httpx.Response(200, json=body)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    raw = UniqloSource().fetch(client)
    assert {r["path"] for r in raw["requests"]} == {"22210", "22211", "22212", "22213"}
    assert all(set(p) == {"path", "limit", "offset", "httpFailure"} for p in seen)
    assert [p["offset"] for p in seen if p["path"] == "22211"] == ["0", "3"]


def test_ikea_fetch_discovers_tags_and_splits_when_capped() -> None:
    index = _load("ikea_index.json")
    offers = _load("ikea_offers.json")
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        calls.append(params)
        if params.get("size") == "1":
            return httpx.Response(200, json=index)
        body = json.loads(json.dumps(offers))
        if "f-subcategories" not in params and "f-last-chance" not in params:
            body["searchResultPage"]["products"]["main"]["max"] = 9999  # simulate capped page
        return httpx.Response(200, json=body)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    raw = IkeaSource().fetch(client)
    tags = [c["f-offers"] for c in calls if "f-offers" in c and "f-subcategories" not in c]
    assert tags == ["FAMILY_PRICE"]
    assert any("f-subcategories" in c for c in calls)
    assert len(raw["requests"]) == len(calls)
    # The last-chance page fit (end >= max): one request, sorted low to high, tagged.
    last_chance = [c for c in calls if c.get("f-last-chance") == "true"]
    assert [c["sort"] for c in last_chance] == ["PRICE_LOW_TO_HIGH"]
    assert [r for r in raw["requests"] if r.get("role") == "last_chance"][0]["body"] == offers


def test_ikea_fetch_last_chance_walks_sort_orders_while_capped() -> None:
    index = _load("ikea_index.json")
    offers = _load("ikea_offers.json")
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        calls.append(params)
        if params.get("size") == "1":
            return httpx.Response(200, json=index)
        body = json.loads(json.dumps(offers))
        if params.get("f-last-chance") == "true":
            body["searchResultPage"]["products"]["main"]["max"] = 1480
        return httpx.Response(200, json=body)

    raw = IkeaSource().fetch(httpx.Client(transport=httpx.MockTransport(handler)))
    last_chance = [c for c in calls if c.get("f-last-chance") == "true"]
    assert [c["sort"] for c in last_chance] == list(LAST_CHANCE_SORTS)
    assert all(c["size"] == "1000" for c in last_chance)
    assert sum(r.get("role") == "last_chance" for r in raw["requests"]) == len(LAST_CHANCE_SORTS)


def test_client_sends_configured_user_agent() -> None:
    client = make_client(Settings(user_agent="pricepulse/0.1", _env_file=None))
    assert client.headers["User-Agent"] == "pricepulse/0.1"


def test_ikea_money_handles_thousands_separator() -> None:
    from pricepulse.sources.ikea import _money

    assert _money({"wholeNumber": "1,049", "decimals": "99"}) == Decimal("1049.99")
    assert _money({"wholeNumber": "95", "decimals": ""}) == Decimal("95.00")
    assert _money(None) is None


def test_uniqlo_parse_marks_multi_gender_products_unisex() -> None:
    raw = uniqlo_raw()
    women = json.loads(json.dumps(raw["requests"][0]))
    women["path"] = "22210"
    raw["requests"].insert(0, women)
    snaps = UniqloSource().parse(raw)
    assert len(snaps) == 8
    assert {s.category for s in snaps} == {"UNISEX"}
