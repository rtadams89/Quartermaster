import { get, post, put, patch, del, ApiError, watchSession } from '/shared/api.js';
import { h, clear, fmtInt, fmtWhen, toast } from '/shared/dom.js';
import { renderLogin } from '/shared/login.js';

const app = document.getElementById('app');
let session = null;
let main;

const NAV = [
  ['dashboard', 'Dashboard'], ['inventory', 'Inventory'], ['unidentified', 'Unidentified'], ['products', 'Products'],
  ['calibers', 'Calibers'], ['history', 'History'], ['labels', 'Labels'], ['settings', 'Settings'],
];

// ------------------------------------------------------------------ helpers
const th = (t, cls) => h('th', { class: cls || '' }, t);
const td = (c, cls) => h('td', { class: cls || '' }, c);
const table = (heads, rows) => h('table', {}, h('thead', {}, h('tr', {}, heads.map((x) => (Array.isArray(x) ? th(x[0], x[1]) : th(x))))), h('tbody', {}, rows));
const empty = (msg) => h('div', { class: 'empty' }, msg);
const boxesCell = (n) => td(h('span', { class: n < 0 ? 'neg' : '' }, fmtInt(n)), 'num');
const specOf = (p) => [p.bullet_weight_gr ? `${p.bullet_weight_gr} gr` : '', p.bullet_type, `${p.rounds_per_box}/box`].filter(Boolean).join(' · ');
const codesOf = (cs) => cs.map((c) => h('span', { class: 'code' }, c.code ?? c));

function dialog({ title, body, ok = 'Save', danger = false, onOk, wide = false }) {
  const err = h('div', { class: 'err', role: 'alert' });
  const okBtn = h('button', { class: 'btn ' + (danger ? 'danger' : 'primary'), type: 'button' }, ok);
  const close = () => { bg.remove(); window.removeEventListener('keydown', onKey); };
  const onKey = (e) => { if (e.key === 'Escape') close(); };
  const bg = h('div', { class: 'modal-bg', onmousedown: (e) => { if (e.target === bg) close(); } },
    h('form', { class: 'modal', style: wide ? { maxWidth: '680px' } : null, onsubmit: (e) => { e.preventDefault(); okBtn.click(); } },
      h('h2', {}, title), body, err,
      h('div', { class: 'actions' }, h('button', { class: 'btn', type: 'button', onclick: close }, 'Cancel'), okBtn)));
  okBtn.addEventListener('click', async () => {
    err.textContent = '';
    okBtn.disabled = true;
    try {
      await onOk();
      close();
    } catch (e) {
      err.textContent = e.message || String(e);
    } finally {
      okBtn.disabled = false;
    }
  });
  window.addEventListener('keydown', onKey);
  document.body.append(bg);
  bg.querySelector('input,select,textarea')?.focus();
  return { close };
}

function confirmBox(title, text, ok, onOk) {
  return dialog({ title, body: h('p', {}, text), ok, danger: true, onOk });
}

function labeled(text, el, cls) {
  return h('label', { class: cls || '' }, text, el);
}

function productFields(calibers, p = {}) {
  const cal = h('select', {}, calibers.filter((c) => c.active || c.id === p.caliber_id).map((c) => h('option', { value: c.id, selected: c.id === p.caliber_id }, c.name + (c.active ? '' : ' (inactive)'))));
  const brand = h('input', { value: p.brand ?? '', placeholder: 'e.g. Federal', maxlength: 80 });
  const name = h('input', { value: p.name ?? '', placeholder: 'e.g. American Eagle', maxlength: 120 });
  const weight = h('input', { type: 'number', step: 'any', min: 0, value: p.bullet_weight_gr ?? '', placeholder: 'grains' });
  const type = h('input', { value: p.bullet_type ?? '', list: 'bullet-types', placeholder: 'FMJ, JHP…', maxlength: 40 });
  const rpb = h('input', { type: 'number', min: 1, step: 1, required: true, value: p.rounds_per_box ?? '', placeholder: 'e.g. 50' });
  const cost = h('input', { type: 'number', min: 0, step: 'any', value: p.cost_per_box ?? '', placeholder: 'optional' });
  const notes = h('textarea', { rows: 2 }, p.notes ?? '');
  const el = h('div', { class: 'form' },
    labeled('Caliber', cal), labeled('Rounds per box', rpb),
    labeled('Brand', brand), labeled('Product / line', name),
    labeled('Bullet weight (gr)', weight), labeled('Bullet type', type),
    labeled('Cost per box', cost), h('span'),
    labeled('Notes', notes, 'full'),
    h('datalist', { id: 'bullet-types' }, ['FMJ', 'TMJ', 'JHP', 'HP', 'SP', 'LRN', 'LSWC', 'BTHP', 'SMK', 'Birdshot', 'Buckshot', 'Slug'].map((t) => h('option', { value: t }))));
  const value = () => {
    if (!cal.value) throw new Error('Pick a caliber (add one on the Calibers page first)');
    if (!rpb.value || Number(rpb.value) < 1) throw new Error('Rounds per box is required');
    return {
      caliber_id: Number(cal.value), brand: brand.value.trim(), name: name.value.trim(),
      bullet_weight_gr: weight.value === '' ? null : Number(weight.value), bullet_type: type.value.trim(),
      rounds_per_box: Number(rpb.value), cost_per_box: cost.value === '' ? null : Number(cost.value), notes: notes.value,
    };
  };
  return { el, value };
}

async function refreshBadge() {
  try {
    const u = await get('/api/inventory/unidentified');
    const link = document.querySelector('nav a[href="#/unidentified"]');
    if (!link) return;
    link.querySelector('.badge')?.remove();
    const open = u.filter((x) => x.boxes !== 0 || x.transactions).length;
    if (open) link.append(h('span', { class: 'badge' }, open));
  } catch { /* ignore */ }
}

// ---------------------------------------------------------------- dashboard
async function dashboard() {
  const [top, unid, prods, recent] = await Promise.all([
    get('/api/inventory/drill'), get('/api/inventory/unidentified'), get('/api/products'), get('/api/transactions?limit=8'),
  ]);
  const cal = top.rows.filter((r) => r.key !== 'unidentified');
  const max = Math.max(1, ...cal.map((r) => r.rounds));
  const unBoxes = unid.reduce((n, u) => n + u.boxes, 0);
  clear(main,
    h('h1', {}, 'Dashboard'),
    h('div', { class: 'cards' },
      card('Rounds on hand', fmtInt(top.total_rounds)),
      card('Boxes (identified)', fmtInt(cal.reduce((n, r) => n + r.boxes, 0))),
      card('Products', fmtInt(prods.length)),
      h('div', { class: 'card' + (unid.length ? ' warn' : '') }, h('a', { href: '#/unidentified' }, h('div', { class: 'k' }, 'Needs details'), h('div', { class: 'v' }, fmtInt(unid.length)), h('div', { class: 'sub', style: { margin: '4px 0 0' } }, unid.length ? `${fmtInt(unBoxes)} boxes under unknown codes` : 'All codes identified')))),
    h('h2', {}, 'By caliber'),
    cal.length ? table(['Caliber', ['Boxes', 'num'], ['Rounds', 'num'], ''], cal.map((r) =>
      h('tr', {}, td(r.label), boxesCell(r.boxes), td(fmtInt(r.rounds), 'num'), td(h('div', { class: 'bar-cell' }, h('div', { class: 'bar', style: { width: `${Math.round((r.rounds / max) * 100)}%` } })), ''))))
      : empty('Nothing in stock yet.'),
    h('h2', {}, 'Recent activity'),
    recent.length ? txTable(recent) : empty('No activity yet. Check some boxes in from the kiosk.'));
}
const card = (k, v) => h('div', { class: 'card' }, h('div', { class: 'k' }, k), h('div', { class: 'v' }, v));

function txTable(rows) {
  return table(['When', 'Type', 'Item', ['Boxes', 'num'], 'Note'], rows.map((t) =>
    h('tr', {}, td(fmtWhen(t.ts)), td(h('span', { class: 'tag ' + t.kind }, t.kind)),
      td([t.product ? `${t.product}${t.caliber ? ' (' + t.caliber + ')' : ''}` : h('em', { class: 'muted' }, 'Unidentified'), h('span', { class: 'code' }, t.code)]),
      td(h('span', { class: t.boxes < 0 ? 'neg' : '' }, (t.boxes > 0 ? '+' : '') + t.boxes), 'num'), td(t.note))));
}

// ---------------------------------------------------------------- inventory
async function inventory() {
  const calibers = await get('/api/calibers');
  const calSel = h('select', {}, h('option', { value: '' }, 'All calibers'), calibers.map((c) => h('option', { value: c.id }, c.name)));
  const q = h('input', { type: 'search', placeholder: 'Search brand, product, code…', size: 28 });
  const zero = h('input', { type: 'checkbox' });
  const holder = h('div');
  const load = async () => {
    const qs = new URLSearchParams();
    if (calSel.value) qs.set('caliber_id', calSel.value);
    if (q.value.trim()) qs.set('q', q.value.trim());
    if (zero.checked) qs.set('include_zero', 'true');
    const d = await get('/api/inventory/items?' + qs);
    const rows = [
      ...d.products.map((p) => h('tr', {},
        td(p.caliber), td([h('b', {}, p.label), h('span', { class: 'code' }, p.spec)]), td(codesOf(p.codes)),
        boxesCell(p.boxes), td(fmtInt(p.rounds), 'num'), td(fmtWhen(p.last_activity)),
        td(p.codes.length ? h('button', { class: 'btn sm', onclick: () => adjustDialog(p, load) }, 'Adjust') : '', 'actions'))),
      ...d.unidentified.map((u) => h('tr', {},
        td(h('em', { class: 'muted' }, '—')), td(h('em', {}, 'Unidentified')), td(codesOf([u.code])),
        boxesCell(u.boxes), td('—', 'num'), td(fmtWhen(u.last_activity)),
        td(h('button', { class: 'btn sm primary', onclick: () => identifyDialog(u.code, load) }, 'Identify'), 'actions'))),
    ];
    clear(holder, rows.length ? table(['Caliber', 'Product', 'Code(s)', ['Boxes', 'num'], ['Rounds', 'num'], 'Last activity', ''], rows) : empty('Nothing matches.'),
      h('p', { class: 'sub', style: { marginTop: '10px' } }, `${fmtInt(d.total_boxes)} boxes · ${fmtInt(d.total_rounds)} identified rounds`));
  };
  let t;
  q.addEventListener('input', () => { clearTimeout(t); t = setTimeout(load, 200); });
  calSel.addEventListener('change', load);
  zero.addEventListener('change', load);
  clear(main, h('h1', {}, 'Inventory'),
    h('div', { class: 'toolbar' }, calSel, q, h('label', { class: 'chk' }, zero, 'Show zero stock'), h('span', { class: 'grow' }),
      h('a', { class: 'btn', href: '/api/export/inventory.csv' }, 'Export CSV')), holder);
  await load();
}

function adjustDialog(p, done) {
  const sel = h('select', {}, p.codes.map((c) => h('option', { value: c.code }, `${c.code}  (${c.boxes} on hand)`)));
  const count = h('input', { type: 'number', step: 1, value: p.codes[0].boxes });
  sel.addEventListener('change', () => { count.value = p.codes.find((c) => c.code === sel.value).boxes; });
  const note = h('input', { placeholder: 'e.g. recount, gave away, miscounted', maxlength: 300 });
  dialog({
    title: `Correct count: ${p.label}`,
    body: h('div', { class: 'form' }, p.codes.length > 1 ? labeled('Code', sel, 'full') : null, labeled('Boxes actually on hand', count, 'full'), labeled('Reason (optional)', note, 'full'),
      h('p', { class: 'sub full', style: { margin: 0 } }, 'This adds a correction entry to the history. Nothing is overwritten.')),
    onOk: async () => {
      const code = sel.value, cur = p.codes.find((c) => c.code === code).boxes;
      const delta = Number(count.value) - cur;
      if (!Number.isInteger(delta)) throw new Error('Enter a whole number');
      if (delta === 0) throw new Error('That is already the count');
      await post('/api/adjustments', { code, boxes: delta, note: note.value });
      toast('Count corrected', 'ok');
      done();
    },
  });
}

// ------------------------------------------------------------- unidentified
async function identifyDialog(code, done) {
  const [calibers, products] = await Promise.all([get('/api/calibers'), get('/api/products')]);
  let mode = products.length ? 'existing' : 'new';
  const pick = h('select', {}, products.map((p) => h('option', { value: p.id }, `${p.caliber} — ${p.label} (${specOf(p)})`)));
  const newForm = productFields(calibers);
  const existingBox = labeled('Product', pick, 'full');
  const wrap = h('div');
  const seg = h('div', { class: 'seg', style: { marginBottom: '14px' } });
  const paint = () => {
    clear(seg,
      h('button', { type: 'button', class: mode === 'existing' ? 'on' : '', disabled: !products.length, onclick: () => { mode = 'existing'; paint(); } }, 'Existing product'),
      h('button', { type: 'button', class: mode === 'new' ? 'on' : '', onclick: () => { mode = 'new'; paint(); } }, 'New product'));
    clear(wrap, mode === 'existing' ? h('div', { class: 'form' }, existingBox) : newForm.el);
  };
  paint();
  dialog({
    title: 'Identify code', wide: true, ok: 'Save',
    body: h('div', {}, h('p', { class: 'sub', style: { margin: '0 0 12px' } }, ['Barcode ', h('b', {}, code), '. Every check-in and check-out already logged for it will pick up these details.']), seg, wrap),
    onOk: async () => {
      let pid;
      if (mode === 'existing') pid = Number(pick.value);
      else pid = (await post('/api/products', newForm.value())).id;
      await put(`/api/barcodes/${encodeURIComponent(code)}`, { product_id: pid });
      toast('Saved', 'ok');
      await done();
      refreshBadge();
    },
  });
}

async function unidentified() {
  const rows = await get('/api/inventory/unidentified');
  clear(main, h('h1', {}, 'Unidentified codes'),
    h('p', { class: 'sub' }, 'Barcodes scanned at the kiosk that have no product details yet. Their boxes are already counted; identify them to get rounds and calibers.'),
    rows.length ? table(['Code', ['Boxes on hand', 'num'], ['Entries', 'num'], 'First seen', 'Last activity', ''], rows.map((u) =>
      h('tr', {}, td(h('b', { class: 'code', style: { display: 'inline', color: 'inherit' } }, u.code)), boxesCell(u.boxes), td(u.transactions, 'num'), td(fmtWhen(u.first_seen_at)), td(fmtWhen(u.last_activity)),
        td([h('button', { class: 'btn sm primary', onclick: () => identifyDialog(u.code, unidentified) }, 'Identify'), ' ',
          !u.transactions && h('button', { class: 'btn sm danger', onclick: () => confirmBox('Remove code?', `Remove ${u.code}? It has no history.`, 'Remove', async () => { await del(`/api/barcodes/${encodeURIComponent(u.code)}`); unidentified(); refreshBadge(); }) }, 'Remove')], 'actions'))))
      : empty('Nothing to identify. Every scanned code has a product.'));
}

// ----------------------------------------------------------------- products
async function products() {
  const calibers = await get('/api/calibers');
  const calSel = h('select', {}, h('option', { value: '' }, 'All calibers'), calibers.map((c) => h('option', { value: c.id }, c.name)));
  const q = h('input', { type: 'search', placeholder: 'Search…', size: 24 });
  const holder = h('div');
  const load = async () => {
    const qs = new URLSearchParams();
    if (calSel.value) qs.set('caliber_id', calSel.value);
    if (q.value.trim()) qs.set('q', q.value.trim());
    const list = await get('/api/products?' + qs);
    clear(holder, list.length ? table(['Caliber', 'Product', 'Details', 'Codes', ''], list.map((p) =>
      h('tr', {}, td(p.caliber), td(h('b', {}, p.label)), td(specOf(p) + (p.cost_per_box != null ? ` · $${p.cost_per_box}/box` : '')), td(p.codes.length ? codesOf(p.codes) : h('em', { class: 'muted' }, 'none')),
        td([h('button', { class: 'btn sm', onclick: () => productDialog(calibers, p, load) }, 'Edit'), ' ',
          h('button', { class: 'btn sm danger', onclick: () => confirmBox('Delete product?', `Delete ${p.label}? Its codes keep their history and go back to "unidentified".`, 'Delete', async () => { await del(`/api/products/${p.id}`); load(); refreshBadge(); }) }, 'Delete')], 'actions'))))
      : empty('No products match.'));
  };
  let t;
  q.addEventListener('input', () => { clearTimeout(t); t = setTimeout(load, 200); });
  calSel.addEventListener('change', load);
  clear(main, h('h1', {}, 'Products'), h('div', { class: 'toolbar' }, calSel, q, h('span', { class: 'grow' }), h('button', { class: 'btn primary', onclick: () => productDialog(calibers, null, load) }, '+ New product')), holder);
  await load();
}

function productDialog(calibers, p, done) {
  const f = productFields(calibers, p || {});
  const codes = p ? [...p.codes] : [];
  const list = h('div', { class: 'codes-list' });
  const addIn = h('input', { placeholder: 'Scan or type a barcode', size: 24 });
  const paintCodes = () => clear(list, codes.length ? codes.map((c) => h('span', { class: 'tag' }, c,
    h('button', { type: 'button', title: 'Remove', onclick: async () => {
      try { await put(`/api/barcodes/${encodeURIComponent(c)}`, { product_id: null }); codes.splice(codes.indexOf(c), 1); paintCodes(); } catch (e) { toast(e.message, 'error'); }
    } }, '×'))) : h('em', { class: 'muted' }, 'No barcodes yet'));
  paintCodes();
  const addCode = async () => {
    if (!p || !addIn.value.trim()) return;
    try {
      const r = await post(`/api/products/${p.id}/barcodes`, { code: addIn.value });
      if (!codes.includes(r.code)) codes.push(r.code);
      addIn.value = '';
      paintCodes();
    } catch (e) { toast(e.message, 'error'); }
  };
  addIn.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); addCode(); } });
  dialog({
    title: p ? `Edit ${p.label}` : 'New product', wide: true,
    body: h('div', {}, f.el,
      p ? h('div', { style: { marginTop: '16px' } }, h('div', { class: 'sub', style: { margin: '0 0 6px' } }, 'Barcodes for this product'), list,
        h('div', { class: 'toolbar', style: { marginTop: '10px' } }, addIn, h('button', { type: 'button', class: 'btn sm', onclick: addCode }, 'Add code')))
        : h('p', { class: 'sub', style: { margin: '14px 0 0' } }, 'After saving you can attach barcodes here, or use the Unidentified page to attach scanned ones.')),
    onOk: async () => {
      if (p) await put(`/api/products/${p.id}`, f.value());
      else await post('/api/products', f.value());
      toast('Saved', 'ok');
      done();
    },
  });
}

// ----------------------------------------------------------------- calibers
async function calibers() {
  const list = await get('/api/calibers');
  const move = async (i, d) => {
    const ids = list.map((c) => c.id);
    [ids[i], ids[i + d]] = [ids[i + d], ids[i]];
    await post('/api/calibers/reorder', { ids });
    calibers();
  };
  const nameIn = h('input', { placeholder: 'New caliber, e.g. .45 Colt', size: 26, maxlength: 64 });
  const add = async () => {
    if (!nameIn.value.trim()) return;
    try { await post('/api/calibers', { name: nameIn.value }); calibers(); } catch (e) { toast(e.message, 'error'); }
  };
  nameIn.addEventListener('keydown', (e) => { if (e.key === 'Enter') add(); });
  clear(main, h('h1', {}, 'Calibers'),
    h('p', { class: 'sub' }, 'This list feeds the caliber picker and the kiosk drill-down, in this order. Inactive calibers stay on existing products but can\'t be picked for new ones.'),
    h('div', { class: 'toolbar' }, nameIn, h('button', { class: 'btn primary', onclick: add }, 'Add')),
    table(['Order', 'Caliber', ['Products', 'num'], 'Active', ''], list.map((c, i) =>
      h('tr', {}, td([h('button', { class: 'btn sm', disabled: i === 0, onclick: () => move(i, -1), 'aria-label': 'Move up' }, '↑'), ' ',
        h('button', { class: 'btn sm', disabled: i === list.length - 1, onclick: () => move(i, 1), 'aria-label': 'Move down' }, '↓')]),
        td(h('b', {}, c.name)), td(c.products, 'num'),
        td(h('input', { type: 'checkbox', checked: c.active, onchange: async (e) => { try { await patch(`/api/calibers/${c.id}`, { active: e.target.checked }); } catch (er) { toast(er.message, 'error'); e.target.checked = !e.target.checked; } } })),
        td([h('button', { class: 'btn sm', onclick: () => renameCaliber(c) }, 'Rename'), ' ',
          h('button', { class: 'btn sm danger', onclick: () => confirmBox('Delete caliber?', c.products ? `${c.name} is used by ${c.products} product(s) and can't be deleted. Mark it inactive instead.` : `Delete ${c.name}?`, 'Delete', async () => { await del(`/api/calibers/${c.id}`); calibers(); }) }, 'Delete')], 'actions')))));
}

function renameCaliber(c) {
  const name = h('input', { value: c.name, maxlength: 64 });
  dialog({ title: 'Rename caliber', body: h('div', { class: 'form' }, labeled('Name', name, 'full')), onOk: async () => { await patch(`/api/calibers/${c.id}`, { name: name.value }); calibers(); } });
}

// ------------------------------------------------------------------ history
async function history() {
  const kind = h('select', {}, h('option', { value: '' }, 'All types'), ['in', 'out', 'adjust'].map((k) => h('option', { value: k }, k)));
  const code = h('input', { type: 'search', placeholder: 'Filter by code', size: 18 });
  const body = h('tbody');
  const more = h('button', { class: 'btn', style: { marginTop: '12px' } }, 'Load more');
  let last = null;
  const load = async (reset) => {
    if (reset) { last = null; clear(body); }
    const qs = new URLSearchParams({ limit: 100 });
    if (kind.value) qs.set('kind', kind.value);
    if (code.value.trim()) qs.set('code', code.value.trim());
    if (last) qs.set('before_id', last);
    const rows = await get('/api/transactions?' + qs);
    const tmp = txTable(rows).querySelector('tbody');
    body.append(...tmp.children);
    if (rows.length) last = rows[rows.length - 1].id;
    more.style.display = rows.length === 100 ? '' : 'none';
    if (reset && !rows.length) body.append(h('tr', {}, td(h('em', { class: 'muted' }, 'No entries.')), td(''), td(''), td(''), td('')));
  };
  more.onclick = () => load(false);
  let t;
  code.addEventListener('input', () => { clearTimeout(t); t = setTimeout(() => load(true), 250); });
  kind.addEventListener('change', () => load(true));
  const tbl = table(['When', 'Type', 'Item', ['Boxes', 'num'], 'Note'], []);
  tbl.querySelector('tbody').replaceWith(body);
  clear(main, h('h1', {}, 'History'), h('div', { class: 'toolbar' }, kind, code, h('span', { class: 'grow' }), h('a', { class: 'btn', href: '/api/export/transactions.csv' }, 'Export CSV')), tbl, more);
  await load(true);
}

// ------------------------------------------------------------------- labels
async function labels() {
  const prods = await get('/api/products');
  const pick = h('select', {}, h('option', { value: '' }, 'No product yet (identify later)'), prods.map((p) => h('option', { value: p.id }, `${p.caliber} — ${p.label} (${specOf(p)})`)));
  const count = h('input', { type: 'number', min: 1, max: 100, value: 10, style: { width: '80px' } });
  const type = h('select', {}, h('option', { value: 'code128' }, 'Code 128 (bar)'), h('option', { value: 'qr' }, 'QR code'));
  const sheet = h('div', { class: 'sheet' });
  const reprint = h('input', { placeholder: 'e.g. QM000012', size: 14 });
  const show = (codes, text) => {
    const t = type.value;
    clear(sheet, codes.map((c) => h('div', { class: 'label ' + t },
      h('img', { src: `/api/labels/render?type=${t}&code=${encodeURIComponent(c)}`, alt: c }), h('div', { class: 'c' }, c), text && h('div', { class: 'p' }, text))));
  };
  const printBtn = h('button', { class: 'btn', onclick: () => window.print() }, 'Print');
  clear(main, h('h1', {}, 'Labels'),
    h('p', { class: 'sub' }, 'For ammo with no UPC (or repacked boxes). Each label is a new unique code that you can scan like any other barcode. Any 2D-capable scanner reads both styles.'),
    h('div', { class: 'toolbar no-print' }, pick, h('span', {}, 'Count'), count, type,
      h('button', { class: 'btn primary', onclick: async () => {
        try {
          const n = Number(count.value);
          const r = await post('/api/labels/allocate', { count: n, product_id: pick.value ? Number(pick.value) : null });
          const p = prods.find((x) => String(x.id) === pick.value);
          show(r.codes, p ? `${p.label}\n${p.caliber} ${specOf(p)}` : '');
          toast(`Created ${r.codes.length} code(s)`, 'ok');
        } catch (e) { toast(e.message, 'error'); }
      } }, 'Create labels'), printBtn),
    h('div', { class: 'toolbar no-print' }, h('span', { class: 'muted' }, 'Reprint an existing code:'), reprint,
      h('button', { class: 'btn', onclick: () => { if (reprint.value.trim()) show([reprint.value.trim().toUpperCase()], ''); } }, 'Show')),
    sheet);
}

// ----------------------------------------------------------------- settings
async function settings() {
  const pin = (ph) => h('input', { type: 'password', inputmode: 'numeric', pattern: '\\d{4}', maxlength: 4, placeholder: ph, autocomplete: 'off', style: { width: '110px' } });
  const cur = pin('Current'), nw = pin('New'), cf = pin('Confirm');
  const locks = await get('/api/security/lockouts');
  clear(main, h('h1', {}, 'Settings'),
    h('h2', {}, 'Change PIN'),
    h('p', { class: 'sub' }, 'One 4-digit PIN unlocks both the kiosk and this site. Both lock after a period of inactivity.'),
    h('form', { class: 'toolbar', onsubmit: async (e) => {
      e.preventDefault();
      if (!/^\d{4}$/.test(cur.value)) return toast('Enter your current 4-digit PIN', 'error');
      if (!/^\d{4}$/.test(nw.value)) return toast('New PIN must be exactly 4 digits', 'error');
      if (nw.value !== cf.value) return toast("New PINs don't match", 'error');
      try { await post('/api/auth/change-pin', { current: cur.value, new: nw.value }); toast('PIN changed', 'ok'); cur.value = nw.value = cf.value = ''; } catch (er) { toast(er.message, 'error'); }
    } }, cur, nw, cf, h('button', { class: 'btn primary', type: 'submit' }, 'Change PIN')),
    h('h2', {}, 'Failed sign-in attempts'),
    h('p', { class: 'sub' }, 'Tracked per source IP. Only a locked IP is blocked; other devices can still sign in.'),
    locks.length ? table(['Source IP', ['Failures', 'num'], 'Status', 'Last failure', ''], locks.map((l) =>
      h('tr', {}, td(h('span', { class: 'code', style: { display: 'inline' } }, l.ip)), td(l.failures, 'num'),
        td(l.locked ? h('span', { class: 'neg' }, `Locked until ${fmtWhen(l.locked_until)}`) : 'Not locked'), td(fmtWhen(l.last_failure_at)),
        td(h('button', { class: 'btn sm', onclick: async () => { await post('/api/security/lockouts/clear', { ip: l.ip }); settings(); } }, 'Clear'), 'actions'))))
      : empty('No failed attempts recorded.'),
    h('h2', {}, 'Data'),
    h('div', { class: 'toolbar' }, h('a', { class: 'btn', href: '/api/export/inventory.csv' }, 'Inventory CSV'), h('a', { class: 'btn', href: '/api/export/transactions.csv' }, 'Full history CSV')));
}

// ------------------------------------------------------------------- router
const pages = { dashboard, inventory, unidentified, products, calibers, history, labels, settings };

async function route() {
  const name = (location.hash.replace(/^#\//, '') || 'dashboard').split('?')[0];
  const page = pages[name] || dashboard;
  document.querySelectorAll('nav a[data-page]').forEach((a) => a.classList.toggle('active', a.dataset.page === name));
  try {
    await page();
  } catch (e) {
    if (e.status !== 401) clear(main, h('h1', {}, 'Something went wrong'), h('div', { class: 'empty' }, e.message || String(e)));
  }
}

function shell() {
  main = h('main');
  clear(app, h('div', { class: 'shell' },
    h('nav', {}, h('div', { class: 'brand' }, 'Quartermaster'),
      NAV.map(([k, label]) => h('a', { href: '#/' + k, 'data-page': k }, label)),
      h('div', { class: 'spacer' }),
      h('a', { href: '#', onclick: async (e) => { e.preventDefault(); try { await post('/api/auth/logout'); } catch { /* */ } lock(); } }, 'Lock')),
    main));
  route();
  refreshBadge();
}

function lock() {
  session?.stop();
  session = null;
  boot();
}

async function boot() {
  let status;
  try { status = await get('/api/auth/status'); } catch (e) {
    clear(app, h('div', { class: 'empty', style: { margin: '40px' } }, 'Cannot reach the Quartermaster server.'));
    return;
  }
  if (!status.authenticated) {
    renderLogin(app, status, boot, 'Quartermaster Admin');
    return;
  }
  session?.stop();
  session = watchSession({ idleMinutes: status.idle_minutes, onLock: () => { toast(`Locked after ${status.idle_minutes} minutes of inactivity`); lock(); } });
  shell();
}

window.addEventListener('hashchange', () => { if (main?.isConnected) route(); });
boot();
