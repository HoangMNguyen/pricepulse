// Loaded with `defer` after htmx; Chart.js (product page only) is also deferred and earlier.
// No inline handlers anywhere: the CSP has no 'unsafe-inline'.

// Sortable column headers toggle the hidden sort select and re-run the filter form.
document.addEventListener('click', e => {
  const b = e.target.closest('button.sort');
  if (!b) return;
  const f = document.getElementById('filters');
  f.sort.value = (f.sort.value === b.dataset.sort && b.dataset.alt) ? b.dataset.alt : b.dataset.sort;
  htmx.trigger(f, 'change');
});

// Keep pushed URLs shareable: blank inputs are "unset", not "".
document.addEventListener('htmx:configRequest', e => { const p = e.detail.parameters; for (const [k, v] of [...p.entries()]) if (v === '') p.delete(k); });

const page = document.getElementById('product');
if (page) {
  const id = page.dataset.productId;
  fetch(`/v1/products/${id}/history?days=180`).then(r => r.json()).then(points => {
    const labels = points.map(p => p.observed_at.slice(0, 10));
    new Chart(document.getElementById('history'), {
      type: 'line',
      data: { labels, datasets: [
        { label: 'Price', data: points.map(p => Number(p.price)), borderColor: '#1a7f37', tension: .2 },
        { label: 'List price', data: points.map(p => p.list_price === null ? null : Number(p.list_price)), borderColor: '#999', borderDash: [4, 4], spanGaps: true },
      ]},
      options: { scales: { y: { ticks: { callback: v => '$' + v } } }, plugins: { legend: { position: 'bottom' } } },
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
    if (res.status === 202) {
      out.append('Check ', Object.assign(document.createElement('strong'), { textContent: body.email }), ' for a confirmation link.');
    } else {
      out.textContent = typeof body.detail === 'string' ? body.detail : `Error ${res.status}`;
    }
  }
  document.getElementById('watch').addEventListener('submit', createWatch);
}
