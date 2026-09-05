"""Pure domain records shared by adapters, ingestion, and alerting."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

log = logging.getLogger(__name__)
CENTS = Decimal("0.01")

# Availability markers shared across retailers ("where / for how long can I buy it").
LABELS: tuple[str, ...] = (
    "last_chance",
    "in_store_only",
    "xl_store_only",
    "online_only",
    "select_variants",
    "coming_soon",
)


@dataclass(frozen=True, slots=True)
class ProductSnapshot:
    """One product as observed from a retailer at a single point in time."""

    source: str
    external_id: str
    name: str
    category: str | None
    url: str
    image_url: str | None
    currency: str
    price: Decimal
    list_price: Decimal | None
    retailer_sale_flag: bool
    retailer_tag: str | None
    valid_to: date | None
    # Retailer's current colour/size listing (`{"colours": [...], "sizes": [...], ...}`);
    # None when the retailer exposes no variant data.
    variants: dict[str, Any] | None = None
    labels: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "price", self.price.quantize(CENTS))
        if self.list_price is not None:
            object.__setattr__(self, "list_price", self.list_price.quantize(CENTS))
        if self.price < 0:
            raise ValueError(f"{self.source}:{self.external_id} negative price {self.price}")
        if self.list_price is not None and self.list_price < self.price:
            log.warning(
                "list_price %s below price %s for %s:%s; dropping list_price",
                self.list_price,
                self.price,
                self.source,
                self.external_id,
            )
            object.__setattr__(self, "list_price", None)
        unknown = set(self.labels) - set(LABELS)
        if unknown:
            raise ValueError(f"{self.source}:{self.external_id} unknown labels {sorted(unknown)}")
