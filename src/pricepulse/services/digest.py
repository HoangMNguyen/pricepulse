"""Turn a ProcessResult into one digest per recipient (default recipients + watchers)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from pricepulse.services.ingest import AlertOut, ProcessResult

RETAILER_FLAG_CAP = 50


@dataclass(slots=True)
class Digest:
    source: str
    date: str
    recipient: str
    base_url: str
    new_deals: list[AlertOut] = field(default_factory=list)
    price_drops: list[AlertOut] = field(default_factory=list)
    retailer_flags: list[AlertOut] = field(default_factory=list)
    retailer_flags_overflow: int = 0
    watch_hits: list[AlertOut] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not (self.new_deals or self.price_drops or self.retailer_flags or self.watch_hits)

    @property
    def watcher_only(self) -> bool:
        return bool(self.watch_hits) and not (
            self.new_deals or self.price_drops or self.retailer_flags
        )

    @property
    def subject(self) -> str:
        return (
            f"PricePulse: {len(self.new_deals)} new deals, {len(self.price_drops)} price drops "
            f"({self.source}, {self.date})"
        )


def _sorted(alerts: list[AlertOut]) -> list[AlertOut]:
    return sorted(alerts, key=lambda a: (-a.discount_pct, a.name))


def build_digests(
    result: ProcessResult, default_recipients: list[str], base_url: str
) -> dict[str, Digest]:
    if result.skipped or not result.alerts:
        return {}
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    by_kind: dict[str, list[AlertOut]] = {}
    for alert in result.alerts:
        by_kind.setdefault(alert.kind, []).append(alert)
    flags = _sorted(by_kind.get("retailer_flag", []))

    digests: dict[str, Digest] = {}

    def digest_for(email: str) -> Digest:
        return digests.setdefault(
            email, Digest(source=result.source, date=today, recipient=email, base_url=base_url)
        )

    for email in default_recipients:
        d = digest_for(email)
        d.new_deals = _sorted(by_kind.get("new_deal", []))
        d.price_drops = _sorted(by_kind.get("price_drop", []))
        d.retailer_flags = flags[:RETAILER_FLAG_CAP]
        d.retailer_flags_overflow = max(0, len(flags) - RETAILER_FLAG_CAP)

    for alert in by_kind.get("watch_hit", []):
        for email in alert.emails:
            digest_for(email).watch_hits.append(alert)
    for d in digests.values():
        d.watch_hits = _sorted(d.watch_hits)

    return {email: d for email, d in digests.items() if not d.is_empty}
