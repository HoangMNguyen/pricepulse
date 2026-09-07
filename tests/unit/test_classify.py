from datetime import date
from decimal import Decimal

from pricepulse.db.repo import PrevObservation
from pricepulse.db.watches import WatchRow
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


def prev(price: str, peak: str | None = None, flag: bool = False) -> PrevObservation:
    return PrevObservation(Decimal(price), flag, Decimal(peak or price))


def test_new_deal_requires_list_price_and_threshold() -> None:
    assert kinds(classify_alerts([(1, snap("79.99", "95.00"))], {}, {}, T)) == []
    out = classify_alerts([(1, snap("70.00", "100.00"))], {}, {}, T)
    assert kinds(out) == ["new_deal"]
    assert out[0].discount_pct == Decimal("30.0")
    assert out[0].old_price == Decimal("100.00")


def test_price_drop_measured_from_peak() -> None:
    before = {1: prev("100.00")}
    out = classify_alerts([(1, snap("70.00"))], before, {}, T)
    assert kinds(out) == ["price_drop"]
    assert (out[0].old_price, out[0].discount_pct) == (Decimal("100.00"), Decimal("30.0"))
    assert classify_alerts([(1, snap("90.00"))], before, {}, T) == []
    # 100 -> 80 -> 79: the event is the dip below 80, the size is 21% from the 100 peak.
    out = classify_alerts([(1, snap("79.00"))], {1: prev("80.00", peak="100.00")}, {}, T)
    assert (out[0].old_price, out[0].discount_pct) == (Decimal("100.00"), Decimal("21.0"))


def test_list_price_does_not_inflate_a_drop() -> None:
    """80 -> 79 with a $100 list price is a 1.25% drop, not a 21% one: the retailer's discount
    is a different number from the change we observed."""
    watches = {1: [WatchRow(1, "a@example.com", Decimal("10"), "tok-a")]}
    out = classify_alerts([(1, snap("79.00", "100.00"))], {1: prev("80.00")}, watches, T)
    assert out == []
    # ...and a rise is never a drop, whatever the list price says.
    out = classify_alerts([(1, snap("85.00", "100.00"))], {1: prev("80.00")}, watches, T)
    assert out == []


def test_retailer_flag_transition() -> None:
    before = {1: prev("49.90")}
    out = classify_alerts([(1, snap("49.90", flag=True))], before, {}, T)
    assert kinds(out) == ["retailer_flag"]
    assert out[0].discount_pct == Decimal("0.0")
    assert (
        classify_alerts([(1, snap("49.90", flag=True))], {1: prev("49.90", flag=True)}, {}, T) == []
    )


def test_watch_hit_uses_per_watch_threshold() -> None:
    watches = {
        1: [
            WatchRow(1, "a@example.com", Decimal("5"), "tok-a"),
            WatchRow(1, "b@example.com", Decimal("50"), "tok-b"),
        ]
    }
    out = classify_alerts([(1, snap("90.00"))], {1: prev("100.00")}, watches, T)
    assert kinds(out) == ["watch_hit"]
    assert out[0].emails == ["a@example.com"]
    assert out[0].watch_tokens == {"a@example.com": "tok-a"}
    # Below the 20% global threshold but a 10.5% drop from the peak: the watcher at 10% hears.
    watches = {1: [WatchRow(1, "a@example.com", Decimal("10"), "tok-a")]}
    out = classify_alerts([(1, snap("89.50"))], {1: prev("90.00", peak="100.00")}, watches, T)
    assert kinds(out) == ["watch_hit"] and out[0].discount_pct == Decimal("10.5")
