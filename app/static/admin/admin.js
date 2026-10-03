import { get, post, put, patch, del, sendBlob, watchSession, watchBuild } from '/shared/api.js';
import { h, clear, fmtInt, fmtMoney, fmtPerRound, fmtPriceRange, fmtWhen, toast } from '/shared/dom.js';
import { renderLogin } from '/shared/login.js';

const app = document.getElementById('app');
let session = null;
let main;

const NAV = [
  ['dashboard', 'Dashboard'], ['inventory', 'Inventory'], ['details', 'Needs details'], ['products', 'Products'],
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

function labeled(text, el, cls, hint) {
  return h('label', { class: cls || '' }, text, el, hint ? h('small', { class: 'hint' }, hint) : null);
}

/** Type-ahead chooser: a text box that filters a drop-down list of {id, name} items as you type.
 *  o: placeholder, none (shown when nothing matches), empty (shown when there are no items), bad(text) and
 *  missing (error messages), optional (an empty box is allowed and gives null), onChange (called when the chosen item changes). */
function searchPicker(items, selectedId, o) {
  const squash = (t) => t.toLowerCase().replace(/[^a-z0-9]/g, '');
  const list = items.map((c) => ({ ...c, key: squash(c.name) }));
  let chosen = list.find((c) => c.id === selectedId) || null;
  let shown = [], active = -1;
  const input = h('input', { type: 'text', placeholder: o.placeholder, autocomplete: 'off', role: 'combobox', 'aria-autocomplete': 'list', 'aria-expanded': 'false', value: chosen ? chosen.name : '' });
  const box = h('div', { class: 'combo-list', role: 'listbox', hidden: true });
  const el = h('div', { class: 'combo' }, input, box);
  const close = () => { box.hidden = true; input.setAttribute('aria-expanded', 'false'); active = -1; };
  const pick = (c) => { chosen = c; input.value = c.name; close(); o.onChange?.(); };
  const paint = () => {
    const q = squash(input.value);
    shown = list.filter((c) => c.key.includes(q)).sort((a, b) => (b.key.startsWith(q) - a.key.startsWith(q)));
    if (active >= shown.length) active = shown.length - 1;
    clear(box, shown.length
      ? shown.map((c, i) => h('div', { class: 'opt' + (i === active ? ' on' : ''), role: 'option', onmousedown: (e) => { e.preventDefault(); pick(c); } }, c.name + (c.active === false ? ' (inactive)' : '')))
      : h('div', { class: 'opt none' }, list.length ? o.none : o.empty));
    box.hidden = false;
    input.setAttribute('aria-expanded', 'true');
    box.querySelector('.on')?.scrollIntoView({ block: 'nearest' });
  };
  input.addEventListener('input', () => { chosen = list.find((c) => c.key === squash(input.value)) || null; active = -1; paint(); o.onChange?.(); });
  input.addEventListener('focus', () => { if (chosen) input.select(); }); // the list opens on click, typing or arrow keys, not on the dialog's own autofocus
  input.addEventListener('click', () => { if (box.hidden) paint(); });
  input.addEventListener('blur', () => {
    if (!chosen && input.value.trim() && shown.length === 1) pick(shown[0]); // one match left: take it
    close();
  });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (box.hidden) paint();
      active = shown.length ? (active + (e.key === 'ArrowDown' ? 1 : -1) + shown.length) % shown.length : -1;
      paint();
    } else if (e.key === 'Enter' && !box.hidden) {
      e.preventDefault(); e.stopPropagation();
      const c = shown[active >= 0 ? active : (shown.length === 1 ? 0 : -1)];
      if (c) pick(c);
    } else if (e.key === 'Escape' && !box.hidden) {
      e.stopPropagation(); close();
    }
  });
  return {
    el,
    value() {
      if (chosen) return chosen.id;
      if (!input.value.trim() && o.optional) return null;
      throw new Error(input.value.trim() ? o.bad(input.value.trim()) : o.missing);
    },
    set(id) { const c = list.find((x) => x.id === id); if (c) { chosen = c; input.value = c.name; } },
  };
}

/** Type-ahead product chooser. optional: an empty box is allowed (value() is null). */
function productPicker(products, { optional = false, placeholder = 'Type to search by caliber, manufacturer or product', onChange } = {}) {
  return searchPicker(products.map((p) => ({ id: p.id, name: `${p.caliber} — ${p.label} (${specOf(p)})` })), null, {
    placeholder, none: 'No matching product', empty: 'No products yet.', optional, onChange,
    bad: (t) => `"${t}" is not one of your products. Pick one from the list${optional ? ', or clear the box' : ''}.`, missing: 'Pick a product',
  });
}

function caliberPicker(calibers, selectedId) {
  return searchPicker(calibers, selectedId, {
    placeholder: 'Type to search, e.g. 9mm or 45', none: 'No matching caliber', empty: 'No calibers yet. Add one on the Calibers page.',
    bad: (t) => `"${t}" is not one of your calibers. Pick one from the list.`, missing: 'Pick a caliber (add one on the Calibers page first)',
  });
}

/** Free-text box that suggests values already used (manufacturers). Anything can still be typed. */
function suggestInput({ value = '', placeholder = '', maxlength }) {
  let options = [], shown = [], active = -1;
  const input = h('input', { type: 'text', value, placeholder, maxlength, autocomplete: 'off', role: 'combobox', 'aria-autocomplete': 'list', 'aria-expanded': 'false' });
  const box = h('div', { class: 'combo-list', role: 'listbox', hidden: true });
  const el = h('div', { class: 'combo' }, input, box);
  const close = () => { box.hidden = true; input.setAttribute('aria-expanded', 'false'); active = -1; };
  const pick = (t) => { input.value = t; close(); };
  const paint = () => {
    const q = input.value.trim().toLowerCase();
    shown = options.filter((o) => o.toLowerCase().includes(q) && o.toLowerCase() !== q)
      .sort((a, b) => (b.toLowerCase().startsWith(q) - a.toLowerCase().startsWith(q))).slice(0, 8);
    if (!shown.length) return close();
    if (active >= shown.length) active = shown.length - 1;
    clear(box, shown.map((o, i) => h('div', { class: 'opt' + (i === active ? ' on' : ''), role: 'option', onmousedown: (e) => { e.preventDefault(); pick(o); } }, o)));
    box.hidden = false;
    input.setAttribute('aria-expanded', 'true');
  };
  input.addEventListener('input', () => { active = -1; paint(); });
  input.addEventListener('click', () => { if (box.hidden) paint(); });
  input.addEventListener('blur', () => {
    const same = options.find((o) => o.toLowerCase() === input.value.trim().toLowerCase());
    if (same) input.value = same; // "federal" becomes "Federal"
    close();
  });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (box.hidden) paint();
      active = shown.length ? (active + (e.key === 'ArrowDown' ? 1 : -1) + shown.length) % shown.length : -1;
      paint();
    } else if (e.key === 'Enter' && !box.hidden && active >= 0) {
      e.preventDefault(); e.stopPropagation(); pick(shown[active]);
    } else if (e.key === 'Escape' && !box.hidden) {
      e.stopPropagation(); close();
    }
  });
  return { el, input, setOptions(list) { options = list; } };
}

/** US-dollar amount box. Accepts "18.5", "$18.50" or "1,234", and tidies it to $18.50 when you leave it. */
const parseMoney = (text) => {
  const t = text.replace(/[\s$,]/g, '');
  if (t === '') return null;
  if (!/^\d*\.?\d+$|^\d+\.$/.test(t)) throw new Error('Cost per box must be a dollar amount, like $18.50');
  return Math.round(parseFloat(t) * 100) / 100;
};
function moneyInput(initial) {
  const input = h('input', { type: 'text', inputmode: 'decimal', placeholder: '$0.00', autocomplete: 'off', value: fmtMoney(initial) });
  input.addEventListener('blur', () => { try { input.value = fmtMoney(parseMoney(input.value)); } catch { /* left as typed; save reports it */ } });
  return { el: input, value: () => parseMoney(input.value) };
}

function productFields(calibers, p = {}) {
  const cal = caliberPicker(calibers.filter((c) => c.active || c.id === p.caliber_id), p.caliber_id);
  const brandBox = suggestInput({ value: p.brand ?? '', placeholder: 'e.g. Federal', maxlength: 80 });
  const brand = brandBox.input;
  get('/api/brands').then((list) => brandBox.setOptions(list), () => {}); // suggestions are a nicety; the box works without
  const name = h('input', { value: p.name ?? '', placeholder: 'e.g. American Eagle', maxlength: 120 });
  const weight = h('input', { type: 'number', step: 'any', min: 0, value: p.bullet_weight_gr ?? '', placeholder: 'grains' });
  const type = h('input', { value: p.bullet_type ?? '', list: 'bullet-types', placeholder: 'FMJ, JHP…', maxlength: 40 });
  const rpb = h('input', { type: 'number', min: 1, step: 1, required: true, value: p.rounds_per_box ?? '', placeholder: 'e.g. 50' });
  const cost = moneyInput(p.cost_per_box);
  const minr = h('input', { type: 'number', min: 0, step: 1, value: p.min_rounds ?? '', placeholder: 'blank = no alert' });
  const notes = h('textarea', { rows: 2 }, p.notes ?? '');
  const el = h('div', { class: 'form' },
    labeled('Caliber', cal.el, '', 'Start typing to search; pick one from the list.'), labeled('Rounds per box', rpb),
    labeled('Manufacturer', brandBox.el), labeled('Product / line', name),
    labeled('Bullet weight (gr)', weight), labeled('Bullet type', type),
    labeled('Cost per box (US dollars)', cost.el, '', 'Optional. The $ is optional when typing; it is shown as $0.00.'),
    labeled('Alert when below (rounds)', minr, '', 'Optional. Leave blank for no alert. Otherwise this product is flagged as low when its rounds on hand drop under this number.'),
    labeled('Notes', notes, 'full'),
    h('datalist', { id: 'bullet-types' }, ['FMJ', 'TMJ', 'JHP', 'HP', 'SP', 'LRN', 'LSWC', 'BTHP', 'SMK', 'Birdshot', 'Buckshot', 'Slug'].map((t) => h('option', { value: t }))));
  const value = () => {
    const caliberId = cal.value();
    if (!rpb.value || Number(rpb.value) < 1) throw new Error('Rounds per box is required');
    return {
      caliber_id: caliberId, brand: brand.value.trim(), name: name.value.trim(),
      bullet_weight_gr: weight.value === '' ? null : Number(weight.value), bullet_type: type.value.trim(),
      rounds_per_box: Number(rpb.value), cost_per_box: cost.value(), notes: notes.value,
      min_rounds: minr.value === '' ? null : Number(minr.value),
    };
  };
  /** Pre-fill from an online lookup. Only fields the lookup was sure about are touched. */
  const fill = (s) => {
    if (s.caliber_id) cal.set(s.caliber_id);
    if (s.rounds_per_box) rpb.value = s.rounds_per_box;
    if (s.brand) brand.value = s.brand;
    if (s.name) name.value = s.name;
    if (s.bullet_weight_gr) weight.value = s.bullet_weight_gr;
    if (s.bullet_type) type.value = s.bullet_type;
  };
  return { el, value, fill };
}

// ------------------------------------------------------------- box photos
const photoUrl = (code, thumbOnly) => `/api/barcodes/${encodeURIComponent(code)}/photo${thumbOnly ? '?thumb=true' : ''}`;

/** A small clickable thumbnail (or camera placeholder) that opens the photo dialog for a code. */
function thumb(code, has, onChange) {
  if (!code) return h('span');
  return h('button', { class: 'thumb-btn', type: 'button', title: has ? 'View or replace the photo' : 'Add a photo', onclick: () => photoDialog(code, has, onChange) },
    has ? h('img', { src: photoUrl(code, true), alt: 'Box photo', loading: 'lazy' }) : h('span', { class: 'ph' }, '📷'));
}

/** View, replace, or remove the photo of one box. onChange(hasPhotoNow) fires after a change. */
function photoDialog(code, has, onChange) {
  const holder = h('div', { class: 'photo-view' });
  const file = h('input', { type: 'file', accept: 'image/*' });
  let objectUrl = null;
  const show = (src) => clear(holder, src ? h('img', { src, alt: 'Box photo' }) : h('div', { class: 'empty' }, 'No photo yet. Choose an image to add one.'));
  show(has ? photoUrl(code) : null);
  file.addEventListener('change', () => {
    const f = file.files[0];
    if (!f) return;
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = URL.createObjectURL(f);
    show(objectUrl);
  });
  const removeBtn = has ? h('button', { type: 'button', class: 'btn danger', onclick: () => confirmBox('Remove photo?', 'The photo of this box will be deleted.', 'Remove', async () => {
    await del(photoUrl(code));
    toast('Photo removed', 'ok');
    d.close();
    onChange?.(false);
  }) }, 'Remove photo') : null;
  const d = dialog({
    title: `Box photo · ${code}`, wide: true, ok: 'Save photo',
    body: h('div', {}, holder, h('div', { class: 'toolbar', style: { marginTop: '14px' } }, file, removeBtn),
      h('p', { class: 'sub', style: { margin: 0 } }, 'Pick a new image to replace the current photo. It is resized and stored with your inventory data.')),
    onOk: async () => {
      if (!file.files[0]) throw new Error('Choose an image file first');
      await sendBlob('PUT', photoUrl(code), file.files[0]);
      toast('Photo saved', 'ok');
      onChange?.(true);
    },
  });
  return d;
}

async function refreshBadge() {
  try {
    const d = await get('/api/needs-details');
    const link = document.querySelector('nav a[href="#/details"]');
    if (!link) return;
    link.querySelector('.badge')?.remove();
    if (d.count) link.append(h('span', { class: 'badge' }, d.count));
  } catch { /* ignore */ }
}

// ---------------------------------------------------------------- dashboard
async function dashboard() {
  const [top, need, prods, recent, low, gone] = await Promise.all([
    get('/api/inventory/drill'), get('/api/needs-details'), get('/api/products'), get('/api/transactions?limit=8'), get('/api/low-stock'), get('/api/out-of-stock'),
  ]);
  const cal = top.rows.filter((r) => r.key !== 'unidentified');
  const nameList = (names) => names.length <= 3 ? names.join(', ') : `${names.slice(0, 3).join(', ')} and ${names.length - 3} more`;
  clear(main,
    h('h1', {}, 'Dashboard'),
    h('div', { class: 'cards' },
      card('Rounds on hand', fmtInt(top.total_rounds)),
      card('Products', fmtInt(prods.length)),
      h('div', { class: 'card' + (low.count ? ' warn' : '') }, h('div', { class: 'k' }, 'Running low'), h('div', { class: 'v' }, fmtInt(low.count)),
        h('div', { class: 'sub', style: { margin: '4px 0 0' } }, low.count ? 'Below the level you set' : 'Nothing below its level')),
      h('div', { class: 'card' + (gone.length ? ' warn' : '') }, h('a', { href: '#/inventory', onclick: () => { Object.assign(invNav, { view: 'browse', caliber: null, weight: null, manufacturer: null }); } },
        h('div', { class: 'k' }, 'Out of stock'), h('div', { class: 'v' }, fmtInt(gone.length)),
        h('div', { class: 'sub', style: { margin: '4px 0 0' } }, gone.length ? nameList(gone.map((c) => c.name)) : 'No caliber is empty'))),
      h('div', { class: 'card' + (need.count ? ' warn' : '') }, h('a', { href: '#/details' }, h('div', { class: 'k' }, 'Needs details'), h('div', { class: 'v' }, fmtInt(need.count)),
        h('div', { class: 'sub', style: { margin: '4px 0 0' } }, need.count ? [need.unidentified.length && `${fmtInt(need.unidentified.length)} unidentified code${need.unidentified.length === 1 ? '' : 's'}`, need.products.length && `${fmtInt(need.products.length)} product${need.products.length === 1 ? '' : 's'} missing details`].filter(Boolean).join(' · ') : 'Everything is filled in')))),
    low.count ? [h('h2', {}, 'Running low'), table(['Item', ['On hand (rounds)', 'num'], ['Alert below', 'num']], [
      ...low.calibers.map((c) => h('tr', {}, td([h('b', {}, c.name), ' ', h('span', { class: 'muted' }, 'caliber')]), td(fmtInt(c.rounds), 'num'), td(fmtInt(c.min_rounds), 'num'))),
      ...low.products.map((p) => h('tr', {}, td([h('b', {}, p.label), ' ', h('span', { class: 'muted' }, p.caliber || '')]), td(fmtInt(p.rounds), 'num'), td(fmtInt(p.min_rounds), 'num'))),
    ])] : null,
    h('h2', {}, 'By caliber'),
    cal.length ? [
      table(['Caliber', ['Boxes', 'num'], ['Rounds', 'num'], ['Value', 'num'], ['Cost per round', 'num']], [
        ...cal.map((r) => h('tr', {}, td(r.label), boxesCell(r.boxes), td(fmtInt(r.rounds), 'num'), valueCell(r), td(fmtPriceRange(r.price) || '—', 'num'))),
        h('tr', { class: 'total' }, td('Total'), boxesCell(cal.reduce((n, r) => n + r.boxes, 0)), td(fmtInt(cal.reduce((n, r) => n + r.rounds, 0)), 'num'),
          td(fmtMoney(cal.reduce((n, r) => n + r.value, 0)), 'num'), td(fmtPriceRange(rangeOf(cal)) || '—', 'num')),
      ]),
      top.unpriced ? h('p', { class: 'sub', style: { marginTop: '8px' } }, `Value counts only products with a cost entered. ${fmtInt(top.unpriced)} product${top.unpriced === 1 ? '' : 's'} in stock ${top.unpriced === 1 ? 'has' : 'have'} none.`) : null,
    ] : empty('Nothing in stock yet.'),
    h('h2', {}, 'Recent activity'),
    recent.length ? txTable(recent) : empty('No activity yet. Scan some ammo in from the kiosk.'));
}
/** Overall {low, high} cost per round across rows that each carry their own range. */
/** A row's stock value, or a dash when it has stock but none of it has a cost entered. */
const valueCell = (r) => td(r.value === 0 && r.unpriced ? '—' : fmtMoney(r.value), 'num');
const rangeOf = (rows) => {
  const rs = rows.map((r) => r.price).filter(Boolean);
  return rs.length ? { low: Math.min(...rs.map((r) => r.low)), high: Math.max(...rs.map((r) => r.high)) } : null;
};
const card = (k, v) => h('div', { class: 'card' }, h('div', { class: 'k' }, k), h('div', { class: 'v' }, v));

function txTable(rows) {
  return table(['When', 'Type', 'Item', ['Boxes', 'num'], 'Note'], rows.map((t) =>
    h('tr', {}, td(fmtWhen(t.ts)), td(h('span', { class: 'tag ' + t.kind }, t.kind)),
      td([t.product ? `${t.product}${t.caliber ? ' (' + t.caliber + ')' : ''}` : h('em', { class: 'muted' }, 'Unidentified'), h('span', { class: 'code' }, t.code)]),
      td(h('span', { class: t.boxes < 0 ? 'neg' : '' }, (t.boxes > 0 ? '+' : '') + t.boxes), 'num'), td(t.note))));
}

// ---------------------------------------------------------------- inventory
const valueOf = (p) => (p.boxes > 0 && p.cost_per_box != null ? p.boxes * p.cost_per_box : null);
const INV_HEAD = ['', 'Caliber', 'Product', 'Code(s)', ['Boxes', 'num'], ['Rounds', 'num'], ['Value', 'num'], ['Cost per round', 'num'], 'Last activity', ''];

function productRow(p, load) {
  return h('tr', {},
    td(thumb(p.photo_code || p.codes[0]?.code, !!p.photo_code, load)),
    td(p.caliber), td([h('b', {}, p.label), p.low && h('span', { class: 'low-tag' }, 'LOW'), h('span', { class: 'code' }, p.spec)]), td(codesOf(p.codes)),
    boxesCell(p.boxes), td(fmtInt(p.rounds), 'num'), td(fmtMoney(valueOf(p)) || '—', 'num'), td(fmtPerRound(p.cost_per_round) || '—', 'num'), td(fmtWhen(p.last_activity)),
    td(p.codes.length ? h('button', { class: 'btn sm', onclick: () => adjustDialog(p, load) }, 'Adjust') : '', 'actions'));
}

function unidentifiedRow(u, load) {
  return h('tr', {},
    td(thumb(u.code, u.has_photo, load)),
    td(h('em', { class: 'muted' }, '—')), td(h('em', {}, 'Unidentified')), td(codesOf([u.code])),
    boxesCell(u.boxes), td('—', 'num'), td('—', 'num'), td('—', 'num'), td(fmtWhen(u.last_activity)),
    td(h('button', { class: 'btn sm primary', onclick: () => identifyDialog(u.code, load, u.has_photo) }, 'Identify'), 'actions'));
}

/** "412 boxes · 9,800 rounds · $1,234.50 · $0.25–$0.40 per round" */
function inventoryFooter(boxes, rounds, value, price, unpriced, note) {
  return h('p', { class: 'sub', style: { marginTop: '10px' } },
    [`${fmtInt(boxes)} boxes`, `${fmtInt(rounds)} ${note || 'rounds'}`, `${fmtMoney(value)} value`, price && `${fmtPriceRange(price)} per round (low to high, items in stock)`].filter(Boolean).join(' · ')
    + (unpriced ? `. Value leaves out ${fmtInt(unpriced)} product${unpriced === 1 ? '' : 's'} with no cost entered.` : ''));
}

// Where the Browse view is: caliber, then bullet weight, then manufacturer (null = not chosen yet).
const invNav = { view: 'browse', caliber: null, weight: null, manufacturer: null };

async function inventory() {
  const calibers = await get('/api/calibers');
  const holder = h('div');
  const seg = h('div', { class: 'seg' });
  const show = async () => {
    clear(seg,
      h('button', { type: 'button', class: invNav.view === 'browse' ? 'on' : '', onclick: () => { invNav.view = 'browse'; show(); } }, 'Browse'),
      h('button', { type: 'button', class: invNav.view === 'list' ? 'on' : '', onclick: () => { invNav.view = 'list'; show(); } }, 'All items'));
    if (invNav.view === 'browse') await inventoryBrowse(holder);
    else await inventoryList(calibers, holder);
  };
  clear(main, h('h1', {}, 'Inventory'),
    h('div', { class: 'toolbar' }, seg, h('span', { class: 'grow' }),
      h('button', { class: 'btn primary', onclick: () => addStockDialog(calibers, show) }, '+ Add stock'),
      h('a', { class: 'btn', href: '/api/export/inventory.csv' }, 'Export CSV')), holder);
  await show();
}

/** Drill down like the kiosk: caliber, then bullet weight, then manufacturer, then the product itself. */
async function inventoryBrowse(holder) {
  const load = async () => {
    const qs = new URLSearchParams({ by_manufacturer: 'true', include_empty: 'true' });
    if (invNav.caliber !== null) qs.set('caliber', invNav.caliber);
    if (invNav.weight !== null) qs.set('weight', invNav.weight);
    if (invNav.manufacturer !== null) qs.set('manufacturer', invNav.manufacturer);
    const d = await get('/api/inventory/drill?' + qs);
    const go = (caliber, weight, manufacturer) => { Object.assign(invNav, { caliber, weight, manufacturer }); load(); };
    // Each crumb jumps back to that level; the last one is where you are now.
    const steps = [
      ['All calibers', () => go(null, null, null)],
      ...d.breadcrumb.map((label, i) => [label, [
        () => go(invNav.caliber, null, null), () => go(invNav.caliber, invNav.weight, null), () => {}][i]]),
    ];
    const crumbs = h('div', { class: 'crumbs' }, steps.map(([label, fn], i) => [
      i > 0 && h('span', { class: 'sep' }, '›'),
      i === steps.length - 1 && i > 0 ? h('b', {}, label) : h('a', { href: '#', onclick: (e) => { e.preventDefault(); fn(); } }, label)]));
    const open = (r) => {
      if (d.level === 'caliber') go(r.key, null, null);
      else if (d.level === 'weight') go(invNav.caliber, r.key, null);
      else if (d.level === 'manufacturer') go(invNav.caliber, invNav.weight, r.key);
    };
    let body;
    if (!d.rows.length) body = empty(d.level === 'caliber' ? 'Nothing in stock yet.' : 'Nothing in stock here.');
    else if (d.level === 'product') {
      body = table(INV_HEAD, d.rows.map((r) => (r.item.codes ? productRow(r.item, load) : unidentifiedRow(r.item, load))));
    } else {
      const what = { caliber: 'Caliber', weight: 'Bullet weight', manufacturer: 'Manufacturer' }[d.level];
      body = table([what, ['Boxes', 'num'], ['Rounds', 'num'], ['Value', 'num'], ['Cost per round', 'num'], ''], d.rows.map((r) => {
        const row = h('tr', r.drillable ? { class: 'drill-row', tabindex: 0, onclick: () => open(r), onkeydown: (e) => { if (e.key === 'Enter') open(r); } } : {},
          td([h('b', {}, r.label), r.out ? h('span', { class: 'low-tag out-tag' }, 'OUT') : r.low && h('span', { class: 'low-tag' }, 'LOW'), r.sublabel && h('span', { class: 'code' }, r.sublabel)]),
          boxesCell(r.boxes), td(r.rounds === null ? '—' : fmtInt(r.rounds), 'num'), (r.rounds === null ? td('—', 'num') : valueCell(r)),
          td(fmtPriceRange(r.price) || '—', 'num'), td(r.drillable ? '›' : '', 'chev'));
        return row;
      }));
    }
    const known = d.rows.filter((r) => r.rounds !== null);
    clear(holder, crumbs, body,
      d.rows.length ? inventoryFooter(known.reduce((n, r) => n + r.boxes, 0), d.total_rounds, d.total_value, rangeOf(known), d.unpriced, 'identified rounds') : null);
  };
  await load();
}

/** Every product in one searchable table. */
async function inventoryList(calibers, holder) {
  const calSel = h('select', {}, h('option', { value: '' }, 'All calibers'), calibers.map((c) => h('option', { value: c.id }, c.name)));
  const q = h('input', { type: 'search', placeholder: 'Search manufacturer, product, code…', size: 28 });
  const zero = h('input', { type: 'checkbox' });
  const results = h('div');
  const load = async () => {
    const qs = new URLSearchParams();
    if (calSel.value) qs.set('caliber_id', calSel.value);
    if (q.value.trim()) qs.set('q', q.value.trim());
    if (zero.checked) qs.set('include_zero', 'true');
    const d = await get('/api/inventory/items?' + qs);
    const rows = [...d.products.map((p) => productRow(p, load)), ...d.unidentified.map((u) => unidentifiedRow(u, load))];
    const unpriced = d.products.filter((p) => p.boxes > 0 && p.cost_per_box == null).length;
    clear(results, rows.length ? table(INV_HEAD, rows) : empty('Nothing matches.'),
      inventoryFooter(d.total_boxes, d.total_rounds, d.total_value, d.price, unpriced, 'identified rounds'));
  };
  let t;
  q.addEventListener('input', () => { clearTimeout(t); t = setTimeout(load, 200); });
  calSel.addEventListener('change', load);
  zero.addEventListener('change', load);
  clear(holder, h('div', { class: 'toolbar' }, calSel, q, h('label', { class: 'chk' }, zero, 'Show zero stock')), results);
  await load();
}

async function addStockDialog(calibers, done) {
  const products = (await get('/api/products')).filter((p) => p.codes.length);
  if (!products.length) {
    toast('Create a product with a barcode first', 'error');
    return productDialog(calibers, null, () => { done(); });
  }
  const pick = productPicker(products, { onChange: () => paintCodes() });
  const code = h('select');
  const paintCodes = () => {
    let p = null;
    try { p = products.find((x) => x.id === pick.value()); } catch { /* nothing chosen yet */ }
    clear(code, p ? p.codes.map((c) => h('option', { value: c }, c)) : []);
    codeRow.style.display = p && p.codes.length > 1 ? '' : 'none';
  };
  const codeRow = labeled('Barcode', code, 'full');
  const boxes = h('input', { type: 'number', min: 1, step: 1, value: 1 });
  const note = h('input', { placeholder: 'e.g. bought at gun show', maxlength: 300 });
  paintCodes();
  const dlg = dialog({
    title: 'Add stock', ok: 'Add to inventory',
    body: h('div', { class: 'form' }, labeled('Product', pick.el, 'full'), codeRow, labeled('Boxes to add', boxes, 'full'), labeled('Note (optional)', note, 'full'),
      h('p', { class: 'sub full', style: { margin: 0 } }, ["Not listed? ", h('a', { href: '#', onclick: (e) => { e.preventDefault(); dlg.close(); productDialog(calibers, null, () => { done(); }); } }, 'Create the product first'), '.'])),
    onOk: async () => {
      pick.value();
      const n = Number(boxes.value);
      if (!Number.isInteger(n) || n < 1) throw new Error('Enter a whole number of boxes, 1 or more');
      await post('/api/stock', { code: code.value, boxes: n, note: note.value });
      toast(`Added ${n} box${n === 1 ? '' : 'es'}`, 'ok');
      done();
    },
  });
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

// ---------------------------------------------------------------- identify
async function identifyDialog(code, done, hasPhoto = false) {
  const [calibers, products] = await Promise.all([get('/api/calibers'), get('/api/products')]);
  let mode = products.length ? 'existing' : 'new';
  const pick = productPicker(products);
  const newForm = productFields(calibers);
  const existingBox = labeled('Product', pick.el, 'full');
  const wrap = h('div');
  const seg = h('div', { class: 'seg', style: { marginBottom: '14px' } });
  const paint = () => {
    clear(seg,
      h('button', { type: 'button', class: mode === 'existing' ? 'on' : '', disabled: !products.length, onclick: () => { mode = 'existing'; paint(); } }, 'Existing product'),
      h('button', { type: 'button', class: mode === 'new' ? 'on' : '', onclick: () => { mode = 'new'; paint(); } }, 'New product'));
    clear(wrap, mode === 'existing' ? h('div', { class: 'form' }, existingBox) : newForm.el);
  };
  paint();
  // Ask the online UPC database what this code is; the answer only ever pre-fills the new-product form.
  const hint = h('div', { class: 'lookup-hint', hidden: true });
  let listingPhoto = false;
  get(`/api/lookup/${encodeURIComponent(code)}`).then((r) => {
    if (!r.enabled) return;
    hint.hidden = false;
    if (!r.found) { clear(hint, h('span', { class: 'muted' }, 'Not found in the online UPC database.')); return; }
    const s = r.suggestion || {};
    listingPhoto = !!r.image && !hasPhoto;
    const got = [s.caliber_id && 'caliber', s.rounds_per_box && 'rounds per box', s.bullet_weight_gr && 'weight', s.bullet_type && 'bullet type'].filter(Boolean);
    clear(hint,
      r.image && h('img', { src: r.image, alt: '', referrerpolicy: 'no-referrer' }),
      h('div', {}, h('b', {}, 'Online lookup: '), r.title,
        h('div', { class: 'muted' }, (got.length ? `Guessed ${got.join(', ')}. Check everything before saving.` : 'Could not guess caliber or rounds per box; fill those in.') + (listingPhoto ? ' The listing photo becomes the box photo when you save, if it looks like a real product photo.' : ''))),
      h('button', { type: 'button', class: 'btn sm', onclick: () => { newForm.fill(s); mode = 'new'; paint(); } }, 'Use these details'));
  }).catch((e) => { hint.hidden = false; clear(hint, h('span', { class: 'muted' }, 'Online lookup: ' + e.message)); });
  dialog({
    title: 'Identify code', wide: true, ok: 'Save',
    body: h('div', {},
      hasPhoto && h('img', { class: 'id-photo', src: photoUrl(code), alt: 'Photo of the box' }), hint,
      h('p', { class: 'sub', style: { margin: '0 0 12px' } }, ['Barcode ', h('b', {}, code), '. Every ammo in and ammo out already logged for it will pick up these details.']), seg, wrap),
    onOk: async () => {
      let pid;
      if (mode === 'existing') pid = pick.value();
      else pid = (await post('/api/products', newForm.value())).id;
      await put(`/api/barcodes/${encodeURIComponent(code)}`, { product_id: pid });
      // Best effort. The server never replaces a photo you already have.
      if (listingPhoto) await post(`/api/lookup/${encodeURIComponent(code)}/photo`).catch(() => {});
      toast('Saved', 'ok');
      await done();
      refreshBadge();
    },
  });
}

/** Scanned codes with no product, and products missing a cost, manufacturer, bullet type or weight. */
async function needsDetails() {
  const [d, calibers] = await Promise.all([get('/api/needs-details'), get('/api/calibers')]);
  const reload = () => { needsDetails(); refreshBadge(); };
  clear(main, h('h1', {}, 'Needs details'),
    h('p', { class: 'sub' }, 'Barcodes scanned at the kiosk with no product yet, and products still missing a cost, manufacturer, bullet type or bullet weight. Boxes under unidentified codes are already counted; identify them to get rounds and calibers.'),
    d.count ? null : empty('Nothing needs attention. Every code has a product and every product is complete.'),
    d.unidentified.length ? [
      h('h2', {}, `Unidentified codes (${fmtInt(d.unidentified.length)})`),
      table(['Photo', 'Code', ['Boxes on hand', 'num'], ['Entries', 'num'], 'First seen', 'Last activity', ''], d.unidentified.map((u) =>
        h('tr', {}, td(thumb(u.code, u.has_photo, reload)), td(h('b', { class: 'code', style: { display: 'inline', color: 'inherit' } }, u.code)), boxesCell(u.boxes), td(u.transactions, 'num'), td(fmtWhen(u.first_seen_at)), td(fmtWhen(u.last_activity)),
          td([h('button', { class: 'btn sm primary', onclick: () => identifyDialog(u.code, reload, u.has_photo) }, 'Identify'), ' ',
            !u.transactions && h('button', { class: 'btn sm danger', onclick: () => confirmBox('Remove code?', `Remove ${u.code}? It has no history.`, 'Remove', async () => { await del(`/api/barcodes/${encodeURIComponent(u.code)}`); reload(); }) }, 'Remove')], 'actions')))),
    ] : null,
    d.products.length ? [
      h('h2', {}, `Products missing details (${fmtInt(d.products.length)})`),
      table(['Product', 'Missing', ['Boxes', 'num'], ''], d.products.map((p) =>
        h('tr', {}, td([h('b', {}, p.label), h('span', { class: 'code' }, `${p.caliber} · ${p.spec}`)]),
          td(p.missing.map((m) => h('span', { class: 'tag miss' }, m))), boxesCell(p.boxes),
          td(h('button', { class: 'btn sm primary', onclick: () => productDialog(calibers, p, reload) }, 'Edit'), 'actions')))),
    ] : null);
}

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
    clear(holder, list.length ? table(['', 'Caliber', 'Product', 'Details', ['Cost per round', 'num'], 'Codes', ''], list.map((p) =>
      h('tr', {}, td(thumb(p.photo_codes[0] || p.codes[0], p.photo_codes.length > 0, load)), td(p.caliber), td(h('b', {}, p.label)), td(specOf(p) + (p.cost_per_box != null ? ` · ${fmtMoney(p.cost_per_box)}/box` : '')), td(fmtPerRound(p.cost_per_round) || '—', 'num'), td(p.codes.length ? codesOf(p.codes) : h('em', { class: 'muted' }, 'none')),
        td([h('button', { class: 'btn sm', onclick: () => productDialog(calibers, p, load) }, 'Edit'), ' ',
          h('button', { class: 'btn sm danger', onclick: () => confirmBox('Delete product?', `Delete ${p.label}? Its codes keep their history and go back to "unidentified".`, 'Delete', async () => { await del(`/api/products/${p.id}`); load(); refreshBadge(); }) }, 'Delete')], 'actions'))))
      : empty('No products match.'));
  };
  let t;
  q.addEventListener('input', () => { clearTimeout(t); t = setTimeout(load, 200); });
  calSel.addEventListener('change', load);
  clear(main, h('h1', {}, 'Products'), h('div', { class: 'toolbar' }, calSel, q, h('span', { class: 'grow' }),
    h('a', { class: 'btn', href: '/api/export/products.csv' }, 'Export CSV'),
    h('button', { class: 'btn', onclick: () => importProducts(load) }, 'Import CSV'),
    h('button', { class: 'btn primary', onclick: () => productDialog(calibers, null, load) }, '+ New product')), holder);
  await load();
}

/** Pick a CSV, show what importing it would do, and apply it only when the file has no problems. */
function importProducts(done) {
  const file = h('input', { type: 'file', accept: '.csv,text/csv,text/plain', style: { display: 'none' } });
  file.addEventListener('change', async () => {
    const f = file.files[0];
    file.remove();
    if (!f) return;
    let r;
    try { r = await sendBlob('POST', '/api/import/products?apply=false', f); } catch (e) { toast(e.message, 'error'); return; }
    const lines = [
      h('p', {}, h('b', {}, `${fmtInt(r.create)} new`), ` · ${fmtInt(r.update)} updated · ${fmtInt(r.unchanged)} unchanged`),
      r.new_calibers.length ? h('p', { class: 'sub' }, `New calibers will be added: ${r.new_calibers.join(', ')}`) : null,
      r.ignored_columns.length ? h('p', { class: 'sub' }, `Columns not used: ${r.ignored_columns.join(', ')}`) : null,
      r.error_count ? h('div', {}, h('p', { class: 'err', style: { margin: '8px 0 4px' } }, `${fmtInt(r.error_count)} row(s) have problems. Fix them in the file and choose it again; nothing is imported until the whole file is clean.`),
        h('ul', { class: 'import-errors' }, r.errors.map((e) => h('li', {}, `Row ${e.row}: ${e.error}`)),
          r.error_count > r.errors.length ? h('li', {}, `…and ${r.error_count - r.errors.length} more`) : null)) : null,
    ];
    dialog({
      title: `Import ${f.name}`, body: h('div', {}, lines), ok: 'Import',
      onOk: async () => {
        if (r.error_count) throw new Error('Fix the rows listed above first');
        await sendBlob('POST', '/api/import/products?apply=true', f);
        toast(`Imported: ${r.create} new, ${r.update} updated`, 'ok');
        done();
      },
    });
  });
  document.body.append(file);
  file.click();
}

function productDialog(calibers, p, done) {
  const f = productFields(calibers, p || {});
  const codes = p ? [...p.codes] : [];
  const withPhoto = new Set(p ? p.photo_codes : []);
  const list = h('div', { class: 'codes-list' });
  const addIn = h('input', { placeholder: 'Scan or type a barcode', size: 24 });
  const paintCodes = () => clear(list, codes.length ? codes.map((c) => h('span', { class: 'tag code-tag' },
    thumb(c, withPhoto.has(c), (has) => { has ? withPhoto.add(c) : withPhoto.delete(c); paintCodes(); done?.(); }), c,
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
        : h('p', { class: 'sub', style: { margin: '14px 0 0' } }, 'After saving you can attach barcodes here, or use the Needs details page to attach scanned ones.')),
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
    h('p', { class: 'sub' }, 'This list feeds the caliber picker and the kiosk drill-down, in this order. Inactive calibers stay on existing products but can\'t be picked for new ones. "Alert below" flags a caliber as low when its rounds on hand drop under that number.'),
    h('div', { class: 'toolbar' }, nameIn, h('button', { class: 'btn primary', onclick: add }, 'Add')),
    table(['Order', 'Caliber', ['Products', 'num'], 'Active', 'Alert below (rounds)', ''], list.map((c, i) =>
      h('tr', {}, td([h('button', { class: 'btn sm', disabled: i === 0, onclick: () => move(i, -1), 'aria-label': 'Move up' }, '↑'), ' ',
        h('button', { class: 'btn sm', disabled: i === list.length - 1, onclick: () => move(i, 1), 'aria-label': 'Move down' }, '↓')]),
        td(h('b', {}, c.name)), td(c.products, 'num'),
        td(h('input', { type: 'checkbox', checked: c.active, onchange: async (e) => { try { await patch(`/api/calibers/${c.id}`, { active: e.target.checked }); } catch (er) { toast(er.message, 'error'); e.target.checked = !e.target.checked; } } })),
        td(h('input', { type: 'number', min: 0, step: 1, value: c.min_rounds ?? '', placeholder: 'none', style: { width: '110px' }, 'aria-label': `Alert below, rounds, for ${c.name}`,
          onchange: async (e) => { try { await patch(`/api/calibers/${c.id}`, { min_rounds: e.target.value === '' ? null : Number(e.target.value) }); toast('Saved', 'ok'); } catch (er) { toast(er.message, 'error'); } } })),
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
// The print job is kept while you move around the admin site, so you can leave and come back to it.
const labelJob = { items: [], cols: 2, type: 'code128' };

async function labels() {
  const prods = await get('/api/products');
  const pick = productPicker(prods, { optional: true, placeholder: 'Product: type to search, or leave empty to identify later' });
  const count = h('input', { type: 'number', min: 1, max: 100, value: 1, style: { width: '80px' }, 'aria-label': 'Number of labels' });
  const type = h('select', { 'aria-label': 'Label style', onchange: () => { labelJob.type = type.value; paint(); } },
    h('option', { value: 'code128' }, 'Code 128 (bar)'), h('option', { value: 'qr' }, 'QR code'));
  type.value = labelJob.type;
  const cols = h('select', { 'aria-label': 'Columns', onchange: () => { labelJob.cols = Number(cols.value); paint(); } },
    [1, 2, 3, 4, 5, 6].map((n) => h('option', { value: n }, n === 1 ? '1 column' : `${n} columns`)));
  cols.value = String(labelJob.cols);
  const reprint = h('input', { placeholder: 'e.g. QM000012', size: 14 });
  const sheet = h('div', { class: 'sheet' });
  const status = h('span', { class: 'muted' });
  const paint = () => {
    sheet.style.gridTemplateColumns = `repeat(${labelJob.cols}, minmax(0, 1fr))`;
    status.textContent = labelJob.items.length ? `${labelJob.items.length} label(s) on the sheet` : 'The sheet is empty. Add labels above.';
    clear(sheet, labelJob.items.map((it, i) => h('div', { class: 'label ' + labelJob.type },
      h('button', { class: 'x no-print', type: 'button', title: 'Remove this label from the sheet', 'aria-label': `Remove label ${it.code}`,
        onclick: () => { labelJob.items.splice(i, 1); paint(); } }, '×'),
      h('img', { src: `/api/labels/render?type=${labelJob.type}&code=${encodeURIComponent(it.code)}`, alt: it.code }),
      h('div', { class: 'c' }, it.code), it.text && h('div', { class: 'p' }, it.text))));
  };
  const add = (codes, text) => { for (const code of codes) labelJob.items.push({ code, text }); paint(); };
  clear(main, h('h1', {}, 'Labels'),
    h('p', { class: 'sub' }, 'For ammo with no UPC (or repacked boxes). Build a sheet from as many different labels as you like, choose how many columns, remove any you do not want, then print. A label is a code you can scan like any other barcode. A product keeps one label code: adding it again reprints the same code. With no product chosen, each label gets its own new code to identify later. Any 2D-capable scanner reads both styles.'),
    h('div', { class: 'toolbar no-print' }, h('div', { style: { flex: '1 1 460px', minWidth: '280px' } }, pick.el), h('span', {}, 'Labels'), count,
      h('button', { class: 'btn primary', onclick: async () => {
        try {
          const n = Number(count.value);
          const pid = pick.value();
          const r = await post('/api/labels/allocate', { count: n, product_id: pid });
          const p = prods.find((x) => x.id === pid);
          add(r.codes, p ? `${p.label}\n${p.caliber} ${specOf(p)}` : '');
          toast(!pid ? `Added ${r.codes.length} new code(s)` : r.reused ? `Added ${n} of ${r.codes[0]}, already this product's label` : `Created ${r.codes[0]} for this product and added ${n}`, 'ok');
        } catch (e) { toast(e.message, 'error'); }
      } }, 'Add to sheet')),
    h('div', { class: 'toolbar no-print' }, h('span', { class: 'muted' }, 'Or add an existing code:'), reprint,
      h('button', { class: 'btn', onclick: () => { if (reprint.value.trim()) { add([reprint.value.trim().toUpperCase()], ''); reprint.value = ''; } } }, 'Add')),
    h('div', { class: 'toolbar no-print' }, type, cols, status, h('span', { class: 'grow' }),
      h('button', { class: 'btn', onclick: () => { labelJob.items.length = 0; paint(); } }, 'Clear sheet'),
      h('button', { class: 'btn primary', onclick: () => window.print() }, 'Print')),
    sheet);
  paint();
}

// ----------------------------------------------------------------- settings
async function settings(restored) {
  const pin = (ph) => h('input', { type: 'password', inputmode: 'numeric', pattern: '\\d{4}', maxlength: 4, placeholder: ph, autocomplete: 'off', style: { width: '110px' } });
  const cur = pin('Current'), nw = pin('New'), cf = pin('Confirm');
  const [locks, prefs] = await Promise.all([get('/api/security/lockouts'), get('/api/settings')]);
  const photoToggle = h('input', { type: 'checkbox', checked: prefs.photo_prompt, onchange: async (e) => {
    try { await put('/api/settings', { photo_prompt: e.target.checked }); toast('Saved', 'ok'); } catch (er) { toast(er.message, 'error'); e.target.checked = !e.target.checked; }
  } });
  const restoreFile = h('input', { type: 'file', accept: '.db,application/octet-stream', style: { display: 'none' }, onchange: () => {
    const f = restoreFile.files[0];
    restoreFile.value = '';
    if (f) restoreDialog(f);
  } });
  clear(main, h('h1', {}, 'Settings'),
    restored && h('div', { class: 'banner ok' },
      h('b', {}, 'Restore complete. '),
      `Loaded ${fmtInt(restored.restored.products)} products, ${fmtInt(restored.restored.barcodes)} barcodes, ${fmtInt(restored.restored.transactions)} history entries and ${fmtInt(restored.restored.barcode_photos)} photos. `,
      `The previous data was saved on the server as ${restored.safety_copy}.`),
    h('h2', {}, 'Box photos'),
    h('label', { class: 'chk', style: { fontSize: '15px', color: 'var(--text)' } }, photoToggle,
      'Ask for a photo of the box at the kiosk when a brand-new barcode is scanned (needs a camera on the Pi)'),
    h('h2', {}, 'Backup & restore'),
    h('p', { class: 'sub' }, 'A backup holds everything: inventory history, products, calibers, box photos and the label counter. It does not include your PIN. Restoring replaces all current data with the backup; your PIN and sign-in are not touched.'),
    h('div', { class: 'toolbar' },
      h('a', { class: 'btn primary', href: '/api/backup' }, '⬇ Download backup'),
      h('button', { class: 'btn', type: 'button', onclick: () => restoreFile.click() }, '⬆ Restore from backup…'), restoreFile),
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
    h('div', { class: 'toolbar' }, h('a', { class: 'btn', href: '/api/export/inventory.csv' }, 'Inventory CSV'), h('a', { class: 'btn', href: '/api/export/transactions.csv' }, 'Full history CSV')),
    h('h2', {}, 'History'),
    h('p', { class: 'sub' }, 'Erases the history log (every in, out and correction entry) but keeps what is on hand: each code\'s current count is kept as a single "opening balance" entry. Products, barcodes, photos and alert levels are not touched. This cannot be undone, so download a backup first if you might want the log.'),
    h('div', { class: 'toolbar' }, h('button', { class: 'btn danger', type: 'button', onclick: clearHistoryDialog }, 'Clear history…')),
    h('h2', {}, 'Reset'),
    h('p', { class: 'sub' }, 'Returns Quartermaster to a fresh install: all history, products, barcodes, photos, calibers and preferences are erased, and so is the PIN, so you will choose a new one. No copy is kept, so download a backup first if you might want anything back.'),
    h('div', { class: 'toolbar' }, h('button', { class: 'btn danger', type: 'button', onclick: resetDialog }, 'Reset all data…')));
}

function clearHistoryDialog() {
  const word = h('input', { type: 'text', placeholder: 'CLEAR', autocomplete: 'off', style: { width: '110px' } });
  dialog({
    title: 'Clear the history?', ok: 'Clear history', danger: true,
    body: h('div', {},
      h('p', {}, 'Every entry in the history is erased. Your inventory stays exactly as it is: each code keeps its current box count as one "opening balance" entry.'),
      h('p', { class: 'sub', style: { margin: '0 0 12px' } }, ['No copy of the log is kept. ', h('a', { href: '/api/export/transactions.csv' }, 'Export the history CSV'), ' or ', h('a', { href: '/api/backup' }, 'download a backup'), ' first if you might want it.']),
      h('div', { class: 'toolbar' }, word),
      h('p', { class: 'sub', style: { margin: 0 } }, 'Type CLEAR to confirm.')),
    onOk: async () => {
      if (word.value.trim() !== 'CLEAR') throw new Error('Type CLEAR to confirm');
      const r = await post('/api/history/clear', { confirm: 'CLEAR' });
      toast(`Cleared ${fmtInt(r.removed)} history entr${r.removed === 1 ? 'y' : 'ies'}`, 'ok');
      refreshBadge();
      settings();
    },
  });
}

function resetDialog() {
  const pin = h('input', { type: 'password', inputmode: 'numeric', pattern: '\\d{4}', maxlength: 4, placeholder: 'PIN', autocomplete: 'off', style: { width: '110px' } });
  const word = h('input', { type: 'text', placeholder: 'RESET', autocomplete: 'off', style: { width: '110px' } });
  dialog({
    title: 'Reset everything?', ok: 'Erase everything', danger: true,
    body: h('div', {},
      h('p', {}, 'This erases every transaction, product, barcode, photo, caliber and setting, and removes the PIN. The starter caliber list is put back, and you will be asked to choose a new PIN, on this site and on the kiosk.'),
      h('p', { class: 'sub', style: { margin: '0 0 12px' } }, ['No copy of the data is saved. ', h('a', { href: '/api/backup' }, 'Download a backup first'), ' if you might want it back.']),
      h('div', { class: 'toolbar' }, pin, word),
      h('p', { class: 'sub', style: { margin: 0 } }, 'Enter your current PIN and type RESET to confirm.')),
    onOk: async () => {
      if (!/^\d{4}$/.test(pin.value)) throw new Error('Enter your 4-digit PIN');
      if (word.value.trim() !== 'RESET') throw new Error('Type RESET to confirm');
      await post('/api/reset', { pin: pin.value, confirm: 'RESET' });
      location.hash = '#/dashboard';
      location.reload(); // back to the first-run screen
    },
  });
}

function restoreDialog(file) {
  const kb = file.size >= 1048576 ? `${(file.size / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(file.size / 1024))} KB`;
  dialog({
    title: 'Restore from backup?', ok: 'Replace everything with this backup', danger: true,
    body: h('div', {},
      h('p', {}, ['Restore ', h('b', {}, file.name), ` (${kb})?`]),
      h('p', { class: 'sub', style: { margin: 0 } }, 'All current inventory, history, products, calibers and photos will be replaced by what is in the backup. A copy of the current data is saved on the server first. Your PIN is not changed.')),
    onOk: async () => {
      const r = await sendBlob('POST', '/api/restore', file);
      toast('Backup restored', 'ok');
      refreshBadge();
      settings(r);
    },
  });
}

// ------------------------------------------------------------------- router
const pages = { dashboard, inventory, details: needsDetails, products, calibers, history, labels, settings };

async function route() {
  const name = (location.hash.replace(/^#\//, '') || 'dashboard').split('?')[0];
  const page = pages[name] || dashboard;
  document.querySelectorAll('nav a[data-page]').forEach((a) => a.classList.toggle('active', a.dataset.page === name));
  const pageName = document.querySelector('.topbar .page');
  if (pageName) pageName.textContent = NAV.find(([k]) => k === name)?.[1] || 'Dashboard';
  try {
    await page();
  } catch (e) {
    if (e.status !== 401) clear(main, h('h1', {}, 'Something went wrong'), h('div', { class: 'empty' }, e.message || String(e)));
  }
}

let version = '';
const isMobile = () => document.documentElement.classList.contains('mobile');

/** Switch between the phone and desktop layouts. The choice is remembered in this browser. */
function toggleLayout() {
  try { localStorage.setItem('qm-layout', isMobile() ? 'desktop' : 'mobile'); } catch { /* not remembered */ }
  location.reload();
}

/** Phone layout shows each table row as a card, so every cell needs its column name and a wrapper for its content. */
function labelCells(root) {
  for (const t of root.querySelectorAll('table')) {
    const heads = [...t.querySelectorAll('thead th')].map((x) => x.textContent.trim());
    for (const tr of t.querySelectorAll('tbody tr')) {
      [...tr.children].forEach((cell, i) => {
        if (cell.tagName !== 'TD' || cell.dataset.label !== undefined || cell.colSpan > 1) return;
        cell.dataset.label = heads[i] || '';
        if (cell.firstElementChild?.classList.contains('cv') && cell.children.length === 1) return;
        const wrap = h('div', { class: 'cv' });
        wrap.append(...cell.childNodes);
        cell.append(wrap);
      });
    }
  }
}

function shell() {
  main = h('main');
  const menu = h('nav', {}, h('div', { class: 'brand' }, 'Quartermaster'),
    NAV.map(([k, label]) => h('a', { href: '#/' + k, 'data-page': k }, label)),
    h('div', { class: 'spacer' }),
    h('a', { href: '#', onclick: (e) => { e.preventDefault(); toggleLayout(); } }, isMobile() ? 'Use desktop layout' : 'Use mobile layout'),
    h('a', { href: '#', onclick: async (e) => { e.preventDefault(); try { await post('/api/auth/logout'); } catch { /* */ } lock(); } }, 'Lock'),
    h('footer', { class: 'ver' }, 'Quartermaster' + (version ? ' v' + version : '')));
  menu.addEventListener('click', (e) => { if (e.target.closest('a')) menu.classList.remove('open'); });
  const topbar = h('header', { class: 'topbar' },
    h('button', { class: 'menu', type: 'button', 'aria-label': 'Menu', onclick: () => menu.classList.toggle('open') }, '☰'),
    h('span', { class: 'brand' }, 'Quartermaster'), h('span', { class: 'page' }));
  clear(app, h('div', { class: 'shell' }, topbar, menu, main));
  let queued = false;
  new MutationObserver(() => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => { queued = false; labelCells(main); });
  }).observe(main, { childList: true, subtree: true });
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
  version = status.version || version;
  shell();
}

window.addEventListener('hashchange', () => { if (main?.isConnected) route(); });
// Pick up a new server version by itself, unless a dialog is open or you are typing.
watchBuild(() => !document.querySelector('.modal-bg') && !/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement?.tagName || ''));

boot();
