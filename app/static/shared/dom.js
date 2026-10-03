// Tiny DOM helpers shared by the kiosk and admin UIs (no framework, no build step).

export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  if (attrs && (typeof attrs !== 'object' || attrs instanceof Node || Array.isArray(attrs))) {
    children.unshift(attrs);
    attrs = null;
  }
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v == null) continue;
    if (k === 'class') el.className = v;
    else if (k === 'dataset') Object.assign(el.dataset, v);
    else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else if (v === true) el.setAttribute(k, '');
    else el.setAttribute(k, v);
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
}

export function clear(el, ...children) {
  el.replaceChildren();
  append(el, children);
  return el;
}

/** Dollars and cents, always two decimals, half-cents rounding up: 0.285 -> "0.29", 18.5 -> "18.50". */
function cents(n) {
  return (Math.round(n * 100 + 1e-9) / 100).toFixed(2);
}

/** Money to the cent with thousands separators: 1234.5 -> "$1,234.50". Empty for null. */
export function fmtMoney(n) {
  if (n == null) return '';
  return '$' + Number(cents(n)).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/** Cost per round, to the cent like every other price: 0.37 -> $0.37, 0.285 -> $0.29. */
export function fmtPerRound(n) {
  return n == null ? '' : '$' + cents(n);
}

/** {low, high} -> "$0.31–$0.45", or "$0.37" when they match; empty when there is no cost. */
export function fmtPriceRange(r) {
  if (!r) return '';
  const lo = fmtPerRound(r.low), hi = fmtPerRound(r.high);
  return lo === hi ? lo : `${lo}–${hi}`;
}

export function fmtInt(n) {
  return n == null ? '—' : Number(n).toLocaleString('en-US');
}

export function fmtWhen(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleString([], { year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
}

let toastTimer;
export function toast(msg, kind = 'info') {
  let t = document.getElementById('qm-toast');
  if (!t) {
    t = h('div', { id: 'qm-toast', role: 'status' });
    // The kiosk wraps its UI in #stage (which may be rotated); the toast must rotate with it.
    (document.getElementById('stage') || document.body).append(t);
  }
  t.textContent = msg;
  t.className = 'show ' + kind;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (t.className = ''), kind === 'error' ? 4500 : 2200);
}
