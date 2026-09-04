"""Discount arithmetic. Mirrors the SQL in the `product_price_summary` materialized view."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

ZERO = Decimal("0.0")
_ONE_DP = Decimal("0.1")


def discount_pct(price: Decimal, reference: Decimal | None) -> Decimal:
    """Percent off `reference`, rounded to one decimal; 0.0 when no reference or no discount."""
    if reference is None or reference <= price or reference == 0:
        return ZERO
    return (Decimal(100) * (1 - price / reference)).quantize(_ONE_DP, rounding=ROUND_HALF_UP)
