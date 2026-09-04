# ADR-0007: Data access etiquette — robots.txt, rate limits, minimal footprint

## Context

The project fetches public product listings from two retailers' JSON endpoints for personal,
non-commercial price tracking.

## Decision

- Honour `robots.txt`: UNIQLO disallows the filter query parameters (`flagCodes`,
  `categoryIds`, `priceRanges`, ...), so the adapter sends only `path`, `limit`, `offset` and
  filters client-side. IKEA's search host allows everything.
- Identify ourselves: `User-Agent: pricepulse/0.1`. (UNIQLO's edge resets connections for
  agents containing a URL or parenthetical, so the UA is the bare product token.)
- Be light: one run per retailer per day (~40 requests total), 500 ms pause between requests,
  bounded retries (3 attempts, exponential backoff, only on 429/5xx/transport errors).
- Store only what the feature needs: product id, name, category, URL, image URL, prices, sale
  flags. No images, reviews, or personal data are downloaded or mirrored.

## Consequences

- Both retailers could change or restrict these endpoints at any time; the raw payloads in S3
  preserve history and the adapters are isolated behind `Source.fetch/parse` so a change is a
  one-file fix.
- Total daily request volume is far below what a single human browsing session generates.
