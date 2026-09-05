# Dashboard UI

Server-rendered (Jinja2 + htmx 2 + Pico CSS 2). No inline CSS/JS: everything lives in
`static/app.css` / `static/app.js`; the CSP has no `'unsafe-inline'`.

## Pages

**Landing `/`** — retailer picker. `<h1>PricePulse</h1>` with a one-line tagline, then one card per
retailer from `stats` (adapter `name`, product count, on-sale count, "checked <UTC time>", last run
status when it is not `succeeded`). Each card links to `/?source=<code>`. No table, no filters, no
categories are rendered here. Canonical is `{base}/`.

**Retailer page `/?source=<code>`** — top to bottom:

1. Page head: `<h1>Current deals</h1>` and the retailer switcher (`role="tablist"`, pills with a
   brand dot; the current one has `aria-selected="true"`). Switching drops `category` and `cursor`
   and keeps `q`, `sort`, `min_discount`, `min_price`, `max_price`, `flagged_only`, `on_sale_only`.
2. Toolbar (`<form id="filters">`, one row on desktop, wraps on narrow screens; ~32px controls):
   search (300 ms debounce), category (this retailer's categories only), sort select, label, size
   (only when the retailer has in-stock sizes: names in retailer order, "Any" = unset), min
   discount %, min $, max $, "Flagged" and "On sale" checkbox chips, and the image-size segmented
   control.
   Every change re-renders `#deals-wrap` via htmx (`hx-select`, `hx-push-url`) so every view is a
   shareable URL; without JS the form submits as a normal GET. A hidden `source` input pins the
   filters to the current retailer.
3. Summary line (`#summary`, `aria-live="polite"`, muted): products, on sale, checked time, showing
   X of Y, current sort label.
4. Results table. Layout follows the adapter: `list_price` (IKEA: Now / Was / Off / Save / Tag /
   Until) or `history` (UNIQLO: Now / Usual / 90-day low / Off / Save / Sale). Sortable headers
   are plain links to `/?…&sort=<key>` with htmx attributes, so they work with or without JS and
   after any swap; "Now" toggles `price_asc`/`price_desc`, the active column shows ▲/▼ and
   `aria-sort`. The toolbar's sort select is re-synced from `data-sort` after each swap.
   "Load more" appends the next keyset page (`/partials/deals?cursor=…`).

**Product page `/products/{id}`** — retailer chip, "no longer listed" badge when not current,
name, category, retailer link; hero image (fixed 360px box, contained) beside price, discount,
usual price and 90-day summary. Variants (UNIQLO): colour swatches are `<button class="swatch">`
in a `role="group"`; clicking one (in-page, no navigation) sets `aria-pressed`, swaps the hero
to that colour's image, writes "GRAY · 3 of 7 sizes" to `#colour-name` (`aria-live`) and re-marks
the `#sizes` chips — every size is listed, sold-out ones are `.chip.out` (dimmed, struck,
`aria-disabled`, title "Sold out"). "Buy this colour ↗" points at the retailer with
`?colorDisplayCode=`. A muted "Sizes as of <local date>" line shows when per-SKU stock was
fetched, otherwise "Size availability per colour not available". Initial selection is the first
colour with an in-stock size; the server-rendered baseline (no JS) shows product-wide stock.
Then Chart.js price history (180 days) and the watch form (email + min % drop) with an
`aria-live` result. Error (404/500/503) and watch confirm/unsubscribe pages reuse the base shell.

## Image size

Segmented control "Images: S / M / L" (48 / 96 / 160 px thumbnails, `object-fit: contain`, rounded
tinted background; rows grow with the thumbnail). Stored in `localStorage` as `pp.thumbs`, applied
as `data-thumbs` on `<html>` at the top of `app.js` (deferred, so a one-frame flash is possible);
default `m`. CSS: `html[data-thumbs=s|m|l] { --thumb: … }`.

## Theme (Tokyo Night, forced `data-theme="dark"`)

Pico variables are overridden in `app.css`: page bg `#1a1b26`; cards, table header, toolbar
`#24283b`; borders `#414868`; text `#a9b1d6`; headings and prices `#c0caf5`; muted text `#565f89`;
links, accent, active sort `#7aa2f7`; discount %, savings and "new" badge `#9ece6a`; "sale"/"drop"
badge `#449dab`; "ends soon" `#eb927b`; errors `#f7768e`; zebra rows `#292e42`; hover `#292e42`
brightened. Retailer brand colours (IKEA `#ffdb00`/`#0058a3`, UNIQLO `#ff0000`/`#fff`) appear only
as small dots/chips. Chart.js: price line accent, list price `#565f89` dashed, grid `#414868`,
ticks `#a9b1d6` (set in `app.js`). Body: system UI stack at 15px; price columns use
`"JetBrainsMono Nerd Font", ui-monospace, monospace`. Container max-width 1200px; cells
`.45rem .6rem`.
