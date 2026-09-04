from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from fastapi import HTTPException

from pricepulse.api.queries import SORTS, decode_cursor, encode_cursor


@pytest.mark.parametrize(
    ("sort", "value"),
    [
        ("discount", Decimal("24.0")),
        ("savings", Decimal("24.00")),
        ("price_asc", Decimal("19.90")),
        ("price_desc", Decimal("98.00")),
        ("name", "KALLAX shelf 10"),
        ("newest", datetime(2026, 9, 4, 12, 30, 15, 123456, tzinfo=UTC)),
        ("ending_soon", date(2026, 9, 6)),
    ],
)
def test_cursor_round_trip(sort: str, value: object) -> None:
    cursor = encode_cursor(sort, value, 42)
    assert "=" not in cursor
    assert decode_cursor(cursor, sort) == (value, 42)


def test_every_sort_has_a_cursor_codec() -> None:
    assert set(SORTS) == {
        "discount",
        "savings",
        "price_asc",
        "price_desc",
        "name",
        "newest",
        "ending_soon",
    }


def test_tampered_or_mismatched_cursor_is_rejected() -> None:
    cursor = encode_cursor("discount", Decimal("10.0"), 1)
    with pytest.raises(HTTPException, match="cursor does not match sort"):
        decode_cursor(cursor, "savings")
    with pytest.raises(HTTPException, match="malformed cursor"):
        decode_cursor("!!!", "discount")
    with pytest.raises(HTTPException, match="malformed cursor"):
        decode_cursor(encode_cursor("newest", "not-a-date", 1), "newest")
