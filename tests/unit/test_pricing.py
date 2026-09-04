from datetime import date
from decimal import Decimal

import pytest

from pricepulse.domain.models import ProductSnapshot
from pricepulse.domain.pricing import discount_pct


@pytest.mark.parametrize(
    ("price", "reference", "expected"),
    [
        (Decimal("79.99"), Decimal("95.00"), Decimal("15.8")),
        (Decimal("19.9"), None, Decimal("0.0")),
        (Decimal("50"), Decimal("50"), Decimal("0.0")),
        (Decimal("70.00"), Decimal("100.00"), Decimal("30.0")),
        (Decimal("120"), Decimal("100"), Decimal("0.0")),
    ],
)
def test_discount_pct(price: Decimal, reference: Decimal | None, expected: Decimal) -> None:
    assert discount_pct(price, reference) == expected


def _snapshot(**overrides: object) -> ProductSnapshot:
    base: dict = {
        "source": "ikea",
        "external_id": "1",
        "name": "X",
        "category": None,
        "url": "https://example.com",
        "image_url": None,
        "currency": "USD",
        "price": Decimal("10.00"),
        "list_price": None,
        "retailer_sale_flag": False,
        "retailer_tag": None,
        "valid_to": date(2026, 9, 7),
    }
    base.update(overrides)
    return ProductSnapshot(**base)


def test_snapshot_drops_list_price_below_price() -> None:
    snap = _snapshot(price=Decimal("10.00"), list_price=Decimal("9.00"))
    assert snap.list_price is None


def test_snapshot_rejects_negative_price() -> None:
    with pytest.raises(ValueError, match="negative price"):
        _snapshot(price=Decimal("-1"))
