# ADR-0005: History-derived discounts; one DB role for the API

## Context

IKEA's search endpoint exposes both the current and the previous ("regular") price for offers.
UNIQLO's commerce API exposes only a `discount` flag: for every flagged item `base == promo`
and no original price is returned. A "% off" number for UNIQLO therefore cannot come from the
retailer.

## Decision

`discount_pct` is defined uniformly as
`100 × (1 − price / reference)` where `reference = list_price ?? mode(price over prior 90 days)`.
For IKEA this equals the retailer's own figure; for UNIQLO it emerges from our history after
the second observation. The retailer flag is stored separately (`retailer_sale_flag`,
`retailer_tag`) and surfaced as `is_on_sale = discount_pct > 0 OR retailer_sale_flag`.

Alert kinds map onto this: `new_deal` (first sighting with a retailer list price, `discount_pct`
as above), `retailer_flag` (flag turned on). `price_drop` and `watch_hit` are a different
quantity: they fire when the price fell since the previous observation and report the drop
from the peak price of the prior 90 days (`repo.PEAK_WINDOW_DAYS`), which is what the product
page promises ("email me when it drops by at least"). The list price never enters a drop: an
$80 → $79 dip on an item listed at $100 is a 1.25 % drop, not a 21 % one (2026-09-07 review,
finding 3).

The API Lambda connects as a single DB role, `app_rw`. Read routes set
`postgresql_readonly=True` on the connection (Postgres enforces `SET TRANSACTION READ ONLY`);
write routes (`/v1/watches`) use a plain connection. `app_ro` exists for humans via `psql`.

## Consequences

- UNIQLO items show `discount_pct = 0` until history accumulates; the README says so.
- The definition is auditable in one place (`domain/pricing.py` mirrors the SQL in the view).
- A separate read replica / read-only Lambda role would add an instance and a second
  `rds-db:connect` grant for no benefit at this scale.
