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

// Column headers are plain links that swap #deals-wrap; mirror the resulting sort into the toolbar.
document.addEventListener('htmx:afterSwap', e => {
  const form = document.getElementById('filters');
  const section = e.detail.target.querySelector?.('[data-sort]');
  if (form && section && form.sort.value !== section.dataset.sort) form.sort.value = section.dataset.sort;
});

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
