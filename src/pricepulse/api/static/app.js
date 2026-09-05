// Loaded with `defer` after htmx; Chart.js (product page only) is also deferred and earlier.
// No inline handlers anywhere: the CSP has no 'unsafe-inline'.

// Thumbnail size (S/M/L) lives in localStorage and is applied as <html data-thumbs>; the CSS
// picks the size from that attribute. Runs first to keep the flash to a single frame.
const THUMBS_KEY = 'pp.thumbs';
const THUMBS = ['s', 'm', 'l'];
const storage = { get: k => { try { return localStorage.getItem(k); } catch { return null; } },
                  set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* private mode */ } } };

function applyThumbs(size) {
  document.documentElement.dataset.thumbs = size;
  for (const b of document.querySelectorAll('button[data-thumbs]')) b.setAttribute('aria-pressed', String(b.dataset.thumbs === size));
}
applyThumbs(THUMBS.includes(storage.get(THUMBS_KEY)) ? storage.get(THUMBS_KEY) : 'm');

document.addEventListener('click', e => {
  const b = e.target.closest('button[data-thumbs]');
  if (!b) return;
  storage.set(THUMBS_KEY, b.dataset.thumbs);
  applyThumbs(b.dataset.thumbs);
});

function syncToolbar(name) {
  const form = document.getElementById('filters');
  if (!form) return;

  const params = new URLSearchParams(location.search);
  const controls = name ? [form.elements.namedItem(name)] : form.elements;
  for (const control of controls) {
    if (!control?.name) continue;
    const value = params.get(control.name);
    if (control.type === 'checkbox') {
      control.checked = value === 'true';
    } else {
      control.value = value ?? (control.name === 'sort' ? 'discount' :
        control.name === 'min_discount' ? '0' : '');
    }
  }
}

// The toolbar is outside #deals-wrap, so history snapshots can restore stale form attributes.
document.addEventListener('htmx:historyRestore', () => syncToolbar());
window.addEventListener('popstate', () => syncToolbar());

// Column headers are plain links that swap #deals-wrap; mirror the pushed sort into the toolbar.
document.addEventListener('htmx:afterSwap', () => syncToolbar('sort'));

// Keep pushed URLs shareable: blank inputs and the 0 % default are "unset", not "".
document.addEventListener('htmx:configRequest', e => { const p = e.detail.parameters; for (const [k, v] of [...p.entries()]) if (v === '' || (k === 'min_discount' && v === '0')) p.delete(k); });

const TN = { accent: '#7aa2f7', fgDark: '#565f89', muted: '#414868', fg: '#a9b1d6', fgBright: '#c0caf5', bg: '#1a1b26' };

const page = document.getElementById('product');
if (page) {
  const id = page.dataset.productId;
  fetch(`/v1/products/${id}/history?days=180`).then(r => r.json()).then(points => {
    const labels = points.map(p => p.observed_at.slice(0, 10));
    new Chart(document.getElementById('history'), {
      type: 'line',
      data: { labels, datasets: [
        { label: 'Price', data: points.map(p => Number(p.price)), borderColor: TN.accent, backgroundColor: TN.accent, pointRadius: 2, borderWidth: 2, tension: .2 },
        { label: 'List price', data: points.map(p => p.list_price === null ? null : Number(p.list_price)), borderColor: TN.fgDark, backgroundColor: TN.fgDark, pointRadius: 0, borderWidth: 1.5, borderDash: [4, 4], spanGaps: true },
      ]},
      options: {
        maintainAspectRatio: false,
        color: TN.fg,
        scales: {
          x: { grid: { color: TN.muted }, ticks: { color: TN.fg, maxTicksLimit: 8, maxRotation: 0 }, border: { color: TN.muted } },
          y: { grid: { color: TN.muted }, ticks: { color: TN.fg, callback: v => '$' + v }, border: { color: TN.muted } },
        },
        plugins: {
          legend: { position: 'bottom', labels: { color: TN.fg, boxWidth: 12, boxHeight: 2 } },
          tooltip: { backgroundColor: TN.bg, titleColor: TN.fgBright, bodyColor: TN.fg, borderColor: TN.muted, borderWidth: 1 },
        },
      },
    });
  });

  // Colour swatches: the server renders every colour (data-image, data-sizes = in-stock size
  // names when per-SKU stock was fetched) and every size; selecting a colour swaps the hero and
  // re-marks the size chips, all in-page.
  const swatches = [...document.querySelectorAll('#swatches .swatch')];
  if (swatches.length) {
    const hero = document.getElementById('hero');
    const name = document.getElementById('colour-name');
    const buy = document.getElementById('buy-colour');
    const chips = [...document.querySelectorAll('#sizes .chip')];
    const anyColour = chips.map(c => !c.classList.contains('out'));  // server-rendered `in_stock`
    const stockAt = document.querySelector('#sizes-note time');
    if (stockAt) stockAt.textContent = new Date(stockAt.dateTime).toLocaleDateString();
    const sizesOf = b => 'sizes' in b.dataset ? JSON.parse(b.dataset.sizes) : null;
    const buyUrl = new URL(buy.href);

    function select(b) {
      for (const s of swatches) s.setAttribute('aria-pressed', String(s === b));
      if (hero && b.dataset.image) hero.src = b.dataset.image;
      buyUrl.searchParams.set('colorDisplayCode', b.dataset.colour);
      buy.href = buyUrl.href;
      const sizes = sizesOf(b);  // null: no per-colour stock, fall back to the product-wide list
      let n = 0;
      chips.forEach((c, i) => {
        const inStock = sizes ? sizes.includes(c.dataset.size) : anyColour[i];
        c.classList.toggle('out', !inStock);
        if (inStock) { n++; c.removeAttribute('aria-disabled'); c.removeAttribute('title'); }
        else { c.setAttribute('aria-disabled', 'true'); c.title = 'Sold out'; }
      });
      name.textContent = chips.length ? `${b.title} · ${n} of ${chips.length} sizes` : b.title;
    }
    document.getElementById('swatches').addEventListener('click', e => {
      const b = e.target.closest('.swatch');
      if (b) select(b);
    });
    select(swatches.find(b => (sizesOf(b) || []).length) || swatches[0]);
  }

  async function createWatch(ev) {
    ev.preventDefault();
    const f = new FormData(ev.target);
    const res = await fetch('/v1/watches', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ product_id: Number(id), email: f.get('email'), min_discount_pct: Number(f.get('min_discount_pct')) }),
    });
    const body = await res.json();
    const out = document.getElementById('watch-result');
    out.textContent = '';
    out.classList.toggle('err', res.status !== 202);
    if (res.status === 202) {
      out.append('Check ', Object.assign(document.createElement('strong'), { textContent: body.email }), ' for a confirmation link.');
    } else {
      out.textContent = typeof body.detail === 'string' ? body.detail : `Error ${res.status}`;
    }
  }
  document.getElementById('watch').addEventListener('submit', createWatch);
}
