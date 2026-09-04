from datetime import date
from decimal import Decimal

from pricepulse.db.repo import PrevObservation, WatchRow
from pricepulse.domain.models import ProductSnapshot
from pricepulse.services.ingest import classify_alerts

T = Decimal("20")


def snap(price: str, list_price: str | None = None, flag: bool = False) -> ProductSnapshot:
    return ProductSnapshot(
        source="ikea",
        external_id="x",
        name="Chair",
        category=None,
        url="https://example.com/x",
        image_url=None,
        currency="USD",
        price=Decimal(price),
        list_price=Decimal(list_price) if list_price else None,
        retailer_sale_flag=flag,
        retailer_tag="FAMILY_PRICE" if flag else None,
        valid_to=date(2026, 9, 7),
    )


def kinds(alerts: list) -> list[str]:
    return sorted(a.kind for a in alerts)


def test_new_deal_requires_list_price_and_threshold() -> None:
    assert kinds(classify_alerts([(1, snap("79.99", "95.00"))], {}, {}, T)) == []
    out = classify_alerts([(1, snap("70.00", "100.00"))], {}, {}, T)
    assert kinds(out) == ["new_deal"]
    assert out[0].discount_pct == Decimal("30.0")
    assert out[0].old_price == Decimal("100.00")


def test_price_drop_against_previous_price() -> None:
    prev = {1: PrevObservation(Decimal("100.00"), False)}
    out = classify_alerts([(1, snap("70.00"))], prev, {}, T)
    assert kinds(out) == ["price_drop"]
    assert (out[0].old_price, out[0].discount_pct) == (Decimal("100.00"), Decimal("30.0"))
    assert classify_alerts([(1, snap("90.00"))], prev, {}, T) == []


def test_retailer_flag_transition() -> None:
    prev = {1: PrevObservation(Decimal("49.90"), False)}
    out = classify_alerts([(1, snap("49.90", flag=True))], prev, {}, T)
    assert kinds(out) == ["retailer_flag"]
    assert out[0].discount_pct == Decimal("0.0")
    prev_flagged = {1: PrevObservation(Decimal("49.90"), True)}
    assert classify_alerts([(1, snap("49.90", flag=True))], prev_flagged, {}, T) == []


def test_watch_hit_uses_per_watch_threshold() -> None:
    prev = {1: PrevObservation(Decimal("100.00"), False)}
    watches = {
        1: [
            WatchRow(1, "a@example.com", Decimal("5"), "tok-a"),
            WatchRow(1, "b@example.com", Decimal("50"), "tok-b"),
        ]
    }
    out = classify_alerts([(1, snap("90.00"))], prev, watches, T)
    assert kinds(out) == ["watch_hit"]
    assert out[0].emails == ["a@example.com"]
    assert out[0].watch_tokens == {"a@example.com": "tok-a"}
