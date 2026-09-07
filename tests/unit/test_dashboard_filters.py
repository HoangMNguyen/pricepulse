from decimal import Decimal

import pytest
from fastapi import HTTPException

from pricepulse.api.routes.dashboard import _number


def test_blank_is_unset_and_plain_numbers_parse() -> None:
    assert _number("", "min_price") is None
    assert _number("  ", "min_price") is None
    assert _number("12.50", "min_price") == Decimal("12.50")
    assert _number("100", "min_discount", upper=100) == Decimal("100")


@pytest.mark.parametrize("value", ["bad", "NaN", "sNaN", "Infinity", "-Infinity", "inf"])
def test_non_numbers_and_non_finite_are_422(value: str) -> None:
    with pytest.raises(HTTPException) as exc:
        _number(value, "min_price")
    assert exc.value.status_code == 422
    assert exc.value.detail == "min_price must be a number"


@pytest.mark.parametrize("value", ["-1", "101"])
def test_out_of_range_is_422(value: str) -> None:
    with pytest.raises(HTTPException) as exc:
        _number(value, "min_discount", upper=100)
    assert exc.value.status_code == 422
    assert exc.value.detail == "min_discount out of range"
