import { get, post, patch, sendBlob, watchSession } from '/shared/api.js';
import { h, clear, fmtInt, toast } from '/shared/dom.js';
import { renderLogin } from '/shared/login.js';
import { detectCamera, createCamera } from '/kiosk/camera.js';

const app = document.getElementById('app');

// Orientation. The UI has a landscape layout (800x480) and a portrait one (480x800). A portrait
// screen, or the ?rotate=90 / ?rotate=270 fallback that turns the page in the browser, selects the latter.
(() => {
  const root = document.documentElement;
  const rotate = new URLSearchParams(location.search).get('rotate');
  if (rotate === '90' || rotate === '270') root.classList.add('rot', 'rot-' + rotate);
  const portrait = matchMedia('(orientation: portrait)');
  const apply = () => root.classList.toggle('portrait', root.classList.contains('rot') || portrait.matches);
  portrait.addEventListener('change', apply);
  apply();
})();

const S = {
  photoPrompt: true,  // ask for a box photo when a brand-new barcode is scanned (admin setting)
  cam: null,          // { kind, at } cached camera detection
  warnedNoCam: false,
  batch: null,        // the draft check-in/out being built (lives on the server)
  last: null,         // most recently scanned item, shown on the scan screen
  inv: { caliber: null, weight: null },
  screen: 'lock',
  session: null,
  version: '',        // from /api/auth/status; shown small on the home screen
};

document.addEventListener('contextmenu', (e) => e.preventDefault());

// ------------------------------------------------------------------ helpers
let audio;
function beep(freq = 880, ms = 90) {
  try {
    audio ??= new AudioContext();
    const o = audio.createOscillator(), g = audio.createGain();
    o.frequency.value = freq; g.gain.value = 0.08;
    o.connect(g); g.connect(audio.destination);
    o.start(); o.stop(audio.currentTime + ms / 1000);
  } catch { /* no audio available: fine */ }
}

function clock() {
  const el = h('span', {});
  const tick = () => { el.textContent = new Date().toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }); };
  tick();
  const t = setInterval(() => (el.isConnected ? tick() : clearInterval(t)), 20_000);
  return el;
}

function bar({ kind, left, title, right }) {
  return h('div', { class: 'bar ' + (kind || '') },
    h('div', { class: 'side' }, left),
    h('h1', {}, title),
    h('div', { class: 'side end' }, right ?? clock()));
}

function screen(...parts) {
  const el = h('div', { class: 'body' }, ...parts);
  return el;
}

function mount(...children) {
  clear(app, ...children);
}

const boxesOf = (b) => b.items.reduce((n, i) => n + i.quantity, 0);
const plural = (n, w) => `${fmtInt(n)} ${n === 1 ? w : w + (w.endsWith('x') ? 'es' : 's')}`;

function describe(item) {
  const p = item.product;
  return p
    ? { name: p.label, sub: `${p.caliber} · ${p.spec}`, known: true }
    : { name: 'Unknown item', sub: 'Not identified yet. It will be logged, and you can fill in details on the admin site.', known: false };
}

// ------------------------------------------------------------------- modals
function modal(content) {
  const ov = h('div', { class: 'overlay' }, content);
  app.append(ov);
  return () => ov.remove();
}

function confirmDialog({ title, text, ok = 'OK', cancel = 'Cancel', danger = false }) {
  return new Promise((resolve) => {
    const close = modal(h('div', { class: 'dialog' },
      h('h2', {}, title), text && h('p', {}, text),
      h('div', { class: 'actions' },
        h('button', { class: 'btn', onclick: () => { close(); resolve(false); } }, cancel),
        h('button', { class: 'btn ' + (danger ? 'danger' : 'primary'), onclick: () => { close(); resolve(true); } }, ok))));
  });
}

function keypad({ title, value = '', maxLen = 3, allowCode = false, okLabel = 'OK', onOk }) {
  let v = String(value);
  let fresh = v !== ''; // a pre-filled value is replaced by the first digit typed
  const disp = h('div', { class: 'numdisplay' });
  const paint = () => { disp.textContent = v || '—'; };
  const key = (label, fn, cls) => h('button', { type: 'button', class: cls || '', onclick: () => { fn(); paint(); } }, label);
  const digit = (d) => key(d, () => {
    if (fresh) { v = ''; fresh = false; }
    if (v.length < maxLen) v = v === '0' ? d : v + d;
  });
  const pad = h('div', { class: 'numpad' + (allowCode ? ' wide' : '') },
    ...['1', '2', '3'].map(digit), allowCode ? key('QM', () => { if (!v.startsWith('QM')) v = 'QM' + v; }) : null,
    ...['4', '5', '6'].map(digit), allowCode ? key('Clear', () => { v = ''; }) : null,
    ...['7', '8', '9'].map(digit), allowCode ? key('⌫', () => { v = v.slice(0, -1); }) : null,
    allowCode ? null : key('Clear', () => { v = ''; }), digit('0'), allowCode ? null : key('⌫', () => { v = v.slice(0, -1); }));
  const close = modal(h('div', { class: 'dialog' },
    h('h2', {}, title), disp, pad,
    h('div', { class: 'actions' },
      h('button', { class: 'btn', onclick: () => close() }, 'Cancel'),
      h('button', { class: 'btn primary', onclick: () => { if (v) { close(); onOk(v); } } }, okLabel))));
  paint();
}

// --------------------------------------------------------------------- lock
function showLock(note) {
  S.screen = 'lock';
  S.session?.stop();
  S.session = null;
  boot();
  if (note) toast(note);
}

async function boot() {
  let status;
  try {
    status = await get('/api/auth/status');
  } catch (e) {
    mount(h('div', { class: 'empty' }, 'Cannot reach the Quartermaster server.', h('div', {}, h('button', { class: 'btn primary', style: { marginTop: '18px' }, onclick: boot }, 'Retry'))));
    return;
  }
  S.version = status.version || S.version;
  if (!status.authenticated) {
    S.screen = 'lock';
    renderLogin(app, status, boot);
    return;
  }
  S.session?.stop();
  S.session = watchSession({
    idleMinutes: status.idle_minutes,
    onLock: () => { if (S.screen !== 'lock') showLock(`Locked after ${status.idle_minutes} minutes of inactivity`); },
  });
  S.batch = await get('/api/batches/current').catch(() => null);
  S.photoPrompt = await get('/api/settings').then((s) => s.photo_prompt).catch(() => true);
  showHome();
}

// --------------------------------------------------------------------- home
function showHome() {
  S.screen = 'home';
  S.last = null;
  const b = S.batch && S.batch.items.length ? S.batch : null;
  mount(
    bar({
      title: ['Quartermaster', S.version && h('span', { class: 'ver' }, 'v' + S.version)],
      left: null,
      right: [clock(), h('button', { class: 'btn', 'aria-label': 'Lock', onclick: lockNow }, '🔒')],
    }),
    b && h('div', { class: 'resume' },
      h('div', { class: 'grow' }, `Unfinished check ${b.kind}: ${plural(b.items.length, 'item')}, ${plural(boxesOf(b), 'box')}`),
      h('button', { class: 'btn primary', onclick: () => showScan() }, 'Resume'),
      h('button', { class: 'btn danger', onclick: discardDraft }, 'Discard')),
    h('div', { class: 'home' },
      h('button', { class: 'tile in', onclick: () => startBatch('in') }, h('span', { class: 'ico' }, '⬇'), 'Check In', h('small', {}, 'Add boxes')),
      h('button', { class: 'tile out', onclick: () => startBatch('out') }, h('span', { class: 'ico' }, '⬆'), 'Check Out', h('small', {}, 'Remove boxes')),
      h('button', { class: 'tile inv', onclick: () => { S.inv = { caliber: null, weight: null }; showInventory(); } }, h('span', { class: 'ico' }, '☰'), 'Inventory', h('small', {}, 'See what you have'))));
}

async function lockNow() {
  try { await post('/api/auth/logout'); } catch { /* ignore */ }
  showLock();
}

async function discardDraft() {
  if (!(await confirmDialog({ title: 'Discard unfinished batch?', text: 'The scanned items will not be recorded.', ok: 'Discard', danger: true }))) return;
  await post(`/api/batches/${S.batch.id}/cancel`).catch(() => {});
  S.batch = null;
  showHome();
}

async function startBatch(kind) {
  try {
    S.batch = await post('/api/batches', { kind });
    showScan();
  } catch (e) {
    if (e.status === 409) toast('Finish or discard the unfinished batch first', 'error');
    else toast(e.message, 'error');
  }
}

// --------------------------------------------------------------------- scan
// The scanner is a USB keyboard: it "types" the code very fast, then presses Enter.
// A burst only counts if the scan screen was showing for ALL of it, so a scan that
// starts while the screen is still loading can never be recorded as a truncated code.
let buf = '', lastKeyAt = 0, burstOk = false;
let scanChain = Promise.resolve();

window.addEventListener('keydown', (e) => {
  const onScan = S.screen === 'scan' && !app.querySelector('.overlay');
  if (e.key === 'Enter') {
    if (onScan && burstOk && buf.length >= 3) { enqueueScan(buf); e.preventDefault(); }
    buf = ''; burstOk = false; lastKeyAt = 0; // next character starts a fresh burst
  } else if (e.key.length === 1) {
    const now = performance.now();
    if (now - lastKeyAt > 120) { buf = ''; burstOk = true; } // slow typing starts a fresh burst
    if (!onScan) burstOk = false;
    buf += e.key;
    lastKeyAt = now;
  }
});

function enqueueScan(code) {
  scanChain = scanChain.then(() => doScan(code)).catch(() => {});
}

function flash(kind) {
  const el = app.querySelector('.scan');
  if (!el) return;
  el.classList.remove('flash-ok', 'flash-warn', 'flash-err');
  void el.offsetWidth;
  el.classList.add('flash-' + kind);
  setTimeout(() => el.classList.remove('flash-' + kind), 350);
}

async function doScan(code) {
  try {
    const r = await post(`/api/batches/${S.batch.id}/scan`, { code });
    const i = S.batch.items.findIndex((x) => x.id === r.item.id);
    if (i >= 0) S.batch.items[i] = r.item; else S.batch.items.push(r.item);
    S.last = r.item;
    if (S.screen === 'scan') paintScan();
    flash(r.known ? 'ok' : 'warn');
    beep(r.known ? 880 : 520);
    // First time this barcode has ever been seen: offer to photograph the box.
    // (Awaited, so any further scans queue up behind it.)
    if (r.new_code && S.photoPrompt && S.screen === 'scan') {
      if (await takeBoxPhoto(r.item.code)) {
        for (const it of S.batch.items) if (it.code === r.item.code) it.has_photo = true;
      }
      if (S.screen === 'scan') paintScan();
    }
  } catch (e) {
    flash('err');
    beep(200, 220);
    toast(e.message, 'error');
  }
}

function showScan() {
  S.screen = 'scan';
  buf = '';
  paintScan();
}

// ------------------------------------------------------------- box photo
const thumbUrl = (code) => `/api/barcodes/${encodeURIComponent(code)}/photo?thumb=true`;
const thumbEl = (it, cls = 'thumb') => (it.has_photo ? h('img', { class: cls, src: thumbUrl(it.code), alt: '' }) : null);

async function cameraKind() {
  // Re-check now and then, so plugging in the camera or starting the helper later just works.
  if (!S.cam || (!S.cam.kind && Date.now() - S.cam.at > 30_000)) S.cam = { kind: await detectCamera(), at: Date.now() };
  return S.cam.kind;
}

/** Full-screen "photograph this box" step. Resolves true if a photo was saved. */
async function takeBoxPhoto(code) {
  const kind = await cameraKind();
  if (!kind) {
    if (!S.warnedNoCam) {
      S.warnedNoCam = true;
      toast('No camera found, so box photos are skipped', 'error');
    }
    return false;
  }
  return new Promise((resolve) => {
    const cam = createCamera(kind);
    let shot = null, shotUrl = null, busy = false, ready = false;
    const stage = h('div', { class: 'cam-stage' });
    const msg = h('div', { class: 'cam-msg' });
    const actions = h('div', { class: 'cam-actions' });
    const root = h('div', { class: 'overlay cam' }, h('div', { class: 'cam-panel' },
      h('div', { class: 'cam-title' }, 'New barcode ', h('b', {}, code)),
      h('div', { class: 'cam-sub' }, 'Take a photo of the box so you can identify it later.'),
      stage, msg, actions));
    app.append(root);

    const end = (saved) => {
      cam.stop();
      if (shotUrl) URL.revokeObjectURL(shotUrl);
      root.remove();
      resolve(saved);
    };
    const btn = (label, cls, fn, disabled) => h('button', { class: 'btn big ' + cls, disabled: disabled || busy, onclick: fn }, label);
    const skip = () => end(false);

    let takeBtn = null;
    const live = () => {
      shot = null;
      clear(stage, cam.el);
      cam.el.play?.()?.catch?.(() => {}); // a <video> that left the page may have paused
      takeBtn = btn('📷  Take photo', 'primary', take, !ready);
      clear(actions, btn('Skip', '', skip), takeBtn);
    };
    const review = () => {
      if (shotUrl) URL.revokeObjectURL(shotUrl);
      shotUrl = URL.createObjectURL(shot);
      clear(stage, h('img', { class: 'cam-shot', src: shotUrl, alt: 'Photo of the box' }));
      clear(actions, btn('Skip', '', skip), btn('Retake', '', live), btn('✓  Use photo', 'in', use));
    };
    const take = async () => {
      busy = true; msg.textContent = 'Capturing…'; live_buttons_disabled();
      try {
        shot = await cam.snapshot();
        msg.textContent = '';
        busy = false;
        review();
      } catch (e) {
        busy = false;
        msg.textContent = e.message;
        live();
      }
    };
    const use = async () => {
      busy = true; msg.textContent = 'Saving…'; live_buttons_disabled();
      try {
        await sendBlob('PUT', `/api/barcodes/${encodeURIComponent(code)}/photo`, shot);
        toast('Photo saved', 'ok');
        end(true);
      } catch (e) {
        busy = false;
        msg.textContent = e.message;
        review();
      }
    };
    const live_buttons_disabled = () => actions.querySelectorAll('button').forEach((b) => (b.disabled = true));

    live();
    msg.textContent = 'Starting camera…';
    cam.start().then(
      () => { ready = true; msg.textContent = ''; if (takeBtn && !shot && !busy) takeBtn.disabled = false; },
      (e) => {
        msg.textContent = `Camera unavailable: ${e.message || e.name}`;
        clear(actions, btn('Skip', 'primary', skip));
      });
  });
}

async function setQty(item, qty) {
  try {
    S.batch = await patch(`/api/batches/${S.batch.id}/items/${item.id}`, { quantity: qty });
    S.last = S.batch.items.find((x) => x.id === item.id) || null;
    S.screen === 'review' ? paintReview() : paintScan();
  } catch (e) {
    toast(e.message, 'error');
  }
}

function paintScan() {
  const kind = S.batch.kind;
  const n = S.batch.items.length, boxes = boxesOf(S.batch);
  const it = S.last && S.batch.items.find((x) => x.id === S.last.id);
  let main;
  if (!it) {
    main = h('div', { class: 'scan-prompt' },
      h('div', { class: 'big' }, kind === 'in' ? 'Scan boxes to check in' : 'Scan boxes to check out'),
      h('div', { class: 'pulse' }, 'Ready for scanner…'));
  } else {
    const d = describe(it);
    const over = kind === 'out' && it.quantity > it.on_hand;
    main = h('div', { class: 'last' },
      thumbEl(it, 'thumb lg'),
      h('div', { class: 'mid' },
        h('div', { class: 'name' + (d.known ? '' : ' unknown') }, d.name),
        h('div', { class: 'sub' }, d.known ? d.sub : it.code),
        !d.known && h('div', { class: 'note warn' }, 'Unknown barcode. It will be logged so you can describe it later.'),
        over && h('div', { class: 'note warn' }, `Only ${it.on_hand} on hand`)),
      h('div', { class: 'stepper' },
        h('button', { 'aria-label': 'Fewer', onclick: () => setQty(it, it.quantity - 1) }, '−'),
        h('button', { class: 'n', onclick: () => editQty(it) }, String(it.quantity)),
        h('button', { 'aria-label': 'More', onclick: () => setQty(it, it.quantity + 1) }, '+')));
  }
  mount(
    bar({
      kind,
      title: kind === 'in' ? 'CHECK IN' : 'CHECK OUT',
      left: h('button', { class: 'btn', onclick: cancelBatch }, '✕ Cancel'),
      right: h('button', { class: 'btn', onclick: () => manualEntry() }, '⌨ Code'),
    }),
    h('div', { class: 'body' }, h('div', { class: 'scan' }, main)),
    h('div', { class: 'foot' },
      h('div', { class: 'grow summary' }, n ? [h('b', {}, plural(n, 'item')), ` · ${plural(boxes, 'box')}`] : h('span', { class: 'muted' }, 'Nothing scanned yet')),
      h('button', { class: 'btn big ' + kind, disabled: !n, onclick: showReview }, 'Review & Finish →')));
}

async function cancelBatch() {
  if (S.batch.items.length) {
    const ok = await confirmDialog({ title: 'Discard this batch?', text: `${plural(S.batch.items.length, 'scanned item')} will not be recorded.`, ok: 'Discard', cancel: 'Keep scanning', danger: true });
    if (!ok) return;
  }
  await post(`/api/batches/${S.batch.id}/cancel`).catch(() => {});
  S.batch = null;
  showHome();
}

function manualEntry() {
  keypad({ title: 'Enter barcode number', maxLen: 32, allowCode: true, okLabel: 'Add', onOk: (v) => enqueueScan(v) });
}

function editQty(item) {
  keypad({ title: 'How many boxes?', value: item.quantity, maxLen: 3, onOk: (v) => setQty(item, Number(v)) });
}

// ------------------------------------------------------------------- review
function showReview() {
  S.screen = 'review';
  paintReview();
}

function paintReview() {
  const b = S.batch;
  if (!b || !b.items.length) { showScan(); return; }
  const kind = b.kind;
  const rows = b.items.map((it) => {
    const d = describe(it);
    const over = kind === 'out' && it.quantity > it.on_hand;
    return h('div', { class: 'row' },
      thumbEl(it, 'thumb'),
      h('div', { class: 'info' },
        h('div', { class: 'title' + (d.known ? '' : ' unknown') }, d.known ? d.name : `Unknown item · ${it.code}`),
        h('div', { class: 'sub' + (over ? ' warn' : '') },
          over ? `Only ${it.on_hand} on hand` : d.known ? d.sub : 'Will be logged; identify it later on the admin site')),
      h('div', { class: 'stepper sm' },
        h('button', { 'aria-label': 'Fewer', onclick: () => setQty(it, it.quantity - 1) }, '−'),
        h('button', { class: 'n', onclick: () => editQty(it) }, String(it.quantity)),
        h('button', { 'aria-label': 'More', onclick: () => setQty(it, it.quantity + 1) }, '+'),
        h('button', { class: 'rm', 'aria-label': 'Remove', onclick: () => setQty(it, 0) }, '🗑')));
  });
  mount(
    bar({
      kind,
      title: kind === 'in' ? 'REVIEW CHECK IN' : 'REVIEW CHECK OUT',
      left: h('button', { class: 'btn', onclick: showScan }, '← Keep scanning'),
    }),
    h('div', { class: 'body' }, h('div', { class: 'list' }, rows)),
    h('div', { class: 'foot' },
      h('div', { class: 'grow summary' }, h('b', {}, plural(b.items.length, 'item')), ` · ${plural(boxesOf(b), 'box')}`),
      h('button', { class: 'btn big ' + kind, onclick: finish }, `Finish ${kind === 'in' ? 'check in' : 'check out'}`)));
}

let finishing = false;
async function finish() {
  if (finishing) return;
  const b = S.batch;
  const over = b.kind === 'out' && b.items.some((i) => i.quantity > i.on_hand);
  if (over && !(await confirmDialog({ title: 'More than you have on record', text: 'Some items are above the quantity on hand. Record anyway?', ok: 'Record anyway' }))) return;
  finishing = true;
  try {
    const r = await post(`/api/batches/${b.id}/finish`);
    S.batch = null;
    beep(990, 140);
    const done = h('div', { class: 'done', onclick: () => { done.remove(); showHome(); } },
      h('div', { class: 'tick' }, '✓'),
      h('div', { class: 'big' }, `${r.kind === 'in' ? 'Checked in' : 'Checked out'} ${plural(r.boxes, 'box')}`),
      h('div', { class: 'muted' }, plural(r.items, 'item')));
    app.append(done);
    setTimeout(() => { if (done.isConnected) { done.remove(); showHome(); } }, 1600);
  } catch (e) {
    toast(e.message, 'error');
  } finally {
    finishing = false;
  }
}

// ---------------------------------------------------------------- inventory
async function showInventory() {
  S.screen = 'inventory';
  let d;
  try {
    const q = new URLSearchParams();
    if (S.inv.caliber !== null) q.set('caliber', S.inv.caliber);
    if (S.inv.weight !== null) q.set('weight', S.inv.weight);
    d = await get('/api/inventory/drill?' + q);
  } catch (e) {
    if (e.status !== 401) toast(e.message, 'error');
    return;
  }
  const crumbs = ['All calibers', ...d.breadcrumb].join('  ›  ');
  const back = () => {
    if (S.inv.weight !== null) S.inv.weight = null;
    else if (S.inv.caliber !== null) S.inv.caliber = null;
    else return showHome();
    showInventory();
  };
  const open = (r) => {
    if (!r.drillable) return;
    if (d.level === 'caliber') S.inv.caliber = r.key;
    else if (d.level === 'weight') S.inv.weight = r.key;
    showInventory();
  };
  const unknownBoxes = d.rows.filter((r) => r.rounds === null).reduce((n, r) => n + r.boxes, 0);
  mount(
    bar({ title: 'INVENTORY', left: h('button', { class: 'btn', onclick: back }, '← Back') }),
    h('div', { class: 'totals' },
      h('span', { class: 'rounds' }, fmtInt(d.total_rounds)), h('span', { class: 'unit' }, `rounds · ${plural(d.total_boxes - unknownBoxes, 'box')}`),
      unknownBoxes ? h('span', { class: 'extra' }, `+ ${plural(unknownBoxes, 'box')} unidentified`) : null),
    h('div', { class: 'crumbs' }, d.breadcrumb.length ? crumbs : 'Tap a caliber to drill in'),
    h('div', { class: 'body' }, h('div', { class: 'list' },
      d.rows.length ? d.rows.map((r) =>
        h('button', { class: 'inv-row', disabled: !r.drillable, onclick: () => open(r) },
          h('div', { class: 'info' }, h('div', { class: 'title' }, r.label), r.sublabel && h('div', { class: 'sub' }, r.sublabel)),
          h('div', { class: 'r' },
            h('div', { class: 'big' }, r.rounds === null ? '—' : fmtInt(r.rounds)),
            h('small', {}, r.rounds === null ? plural(r.boxes, 'box') : `rounds · ${plural(r.boxes, 'box')}`)),
          r.drillable && h('span', { class: 'chev' }, '›')))
        : h('div', { class: 'empty' }, 'Nothing in stock yet. Use Check In to add boxes.'))));
}

boot();
