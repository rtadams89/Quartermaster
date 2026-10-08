import { get, post, patch, sendBlob, watchSession, watchBuild } from '/shared/api.js';
import { h, clear, fmtInt, fmtPriceRange, toast } from '/shared/dom.js';
import { renderLogin } from '/shared/login.js';
import { detectCamera, createCamera } from '/kiosk/camera.js';

const app = document.getElementById('app');

// Orientation. The UI has a landscape layout (800x480) and a portrait one (480x800). A portrait
// screen, or ?rotate=90 / ?rotate=270 (the page turns itself), selects the latter. ?rotate=180 turns it upside-down.
(() => {
  const root = document.documentElement;
  const rotate = new URLSearchParams(location.search).get('rotate');
  if (rotate === '90' || rotate === '270') root.classList.add('rot', 'rot-' + rotate);
  else if (rotate === '180') root.classList.add('rot-180');
  const portrait = matchMedia('(orientation: portrait)');
  const apply = () => root.classList.toggle('portrait', root.classList.contains('rot') || portrait.matches);
  portrait.addEventListener('change', apply);
  apply();
})();

// Sleep veil. When the Pi blanks its screen after N seconds without input (see the installer), the first touch
// that wakes the screen would also press whatever is under the finger. The installer passes the same period as
// ?blank=N; the page goes black a few seconds before the screen does, and swallows the first touch or scan.
// The home screen's Screen off button uses the same veil straight away (sleepNow), whether or not a timeout is set.
let sleepNow = () => {};
(() => {
  const secs = Number(new URLSearchParams(location.search).get('blank'));
  const timed = secs >= 11;
  const after = (secs - 10) * 1000;
  const veil = h('div', { id: 'veil', 'aria-hidden': 'true' });
  const eaten = ['pointerdown', 'pointerup', 'mousedown', 'mouseup', 'touchstart', 'touchend', 'click', 'keydown', 'keyup', 'keypress'];
  const activity = ['pointerdown', 'pointermove', 'touchstart', 'keydown'];
  let timer, state = 'awake'; // awake -> asleep -> waking -> awake
  const arm = () => { clearTimeout(timer); if (timed) timer = setTimeout(sleep, after); };
  function sleep() {
    clearTimeout(timer);
    state = 'asleep';
    document.body.append(veil);
    veil.className = '';
  }
  function wake() {
    state = 'waking';
    veil.className = 'wake';
    // Keep eating events until the touch (or the scanner's burst) has finished, then reveal the page.
    clearTimeout(wake.t);
    wake.t = setTimeout(() => { veil.remove(); state = 'awake'; arm(); }, 500);
  }
  for (const type of eaten) {
    window.addEventListener(type, (e) => {
      if (state === 'awake') return;
      e.preventDefault();
      e.stopImmediatePropagation();
      if (state === 'asleep' && activity.includes(type)) wake();
      else if (state === 'waking') { clearTimeout(wake.t); wake.t = setTimeout(() => { veil.remove(); state = 'awake'; arm(); }, 500); }
    }, { capture: true, passive: false });
  }
  for (const type of activity) window.addEventListener(type, () => { if (state === 'awake') arm(); }, { capture: true, passive: true });
  sleepNow = sleep;
  arm();
})();

const S = {
  sound: { enabled: true, volume: 50 }, // admin setting
  cameraFlip: false,  // admin setting: camera mounted upside down
  photoPrompt: true,  // ask for a box photo when a brand-new barcode is scanned (admin setting)
  cam: null,          // { kind, at } cached camera detection
  warnedNoCam: false,
  batch: null,        // the draft ammo in/out batch being built (lives on the server)
  last: null,         // most recently scanned item, shown on the scan screen
  inv: { caliber: null, weight: null, hit: null }, // hit: the row to highlight after a scan
  screen: 'lock',
  session: null,
  version: '',        // from /api/auth/status; shown small on the home screen
};

document.addEventListener('contextmenu', (e) => e.preventDefault());

// ------------------------------------------------------------------ helpers
let audio;
function beep(freq = 880, ms = 90) {
  if (!S.sound.enabled || S.sound.volume <= 0) return;
  try {
    audio ??= new AudioContext();
    const o = audio.createOscillator(), g = audio.createGain();
    o.frequency.value = freq;
    const v = S.sound.volume / 100;
    g.gain.value = 0.32 * v * v; // squared so the slider feels even
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

// Products with 1 round per box are counted by the individual round, so they show rounds instead of boxes.
const isSingle = (it) => it.product?.rounds_per_box === 1;
/** "3 boxes", "25 rounds" or "3 boxes · 25 rounds" for everything in a batch. */
const qtyText = (b) => {
  const boxes = b.items.filter((i) => !isSingle(i)).reduce((n, i) => n + i.quantity, 0);
  const rounds = b.items.filter(isSingle).reduce((n, i) => n + i.quantity, 0);
  return [boxes > 0 && plural(boxes, 'box'), rounds > 0 && plural(rounds, 'round')].filter(Boolean).join(' · ') || plural(0, 'box');
};
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

function confirmDialog({ title, text, items, ok = 'OK', cancel = 'Cancel', danger = false }) {
  return new Promise((resolve) => {
    const close = modal(h('div', { class: 'dialog' },
      h('h2', {}, title), text && h('p', {}, text),
      items && h('ul', {}, ...items.map((t) => h('li', {}, t))),
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
  await loadSettings();
  showHome();
}

async function loadSettings() {
  const s = await get('/api/settings').catch(() => null);
  if (!s) return;
  S.photoPrompt = s.photo_prompt;
  S.sound = s.sound;
  S.cameraFlip = s.camera.flip;
}

// --------------------------------------------------------------------- home
function showHome() {
  S.screen = 'home';
  loadSettings(); // pick up admin changes without a new login
  const invSub = h('small', {}, 'See what you have');
  S.last = null;
  const b = S.batch && S.batch.items.length ? S.batch : null;
  mount(
    bar({
      title: ['Quartermaster', S.version && h('span', { class: 'ver' }, 'v' + S.version)],
      left: null,
      right: [clock(), h('button', { class: 'btn', 'aria-label': 'Screen off', title: 'Screen off', onclick: screenOff }, '🌙'),
        h('button', { class: 'btn', 'aria-label': 'Lock', onclick: lockNow }, '🔒')],
    }),
    b && h('div', { class: 'resume' },
      h('div', { class: 'grow' }, `Unfinished ammo ${b.kind}: ${plural(b.items.length, 'item')}, ${qtyText(b)}`),
      h('button', { class: 'btn primary', onclick: () => showScan() }, 'Resume'),
      h('button', { class: 'btn danger', onclick: discardDraft }, 'Discard')),
    h('div', { class: 'home' },
      h('button', { class: 'tile in', onclick: () => startBatch('in') }, h('span', { class: 'ico' }, '⬇'), 'Ammo In', h('small', {}, 'Add boxes')),
      h('button', { class: 'tile out', onclick: () => startBatch('out') }, h('span', { class: 'ico' }, '⬆'), 'Ammo Out', h('small', {}, 'Remove boxes')),
      h('button', { class: 'tile inv', onclick: () => { S.inv = { caliber: null, weight: null, hit: null }; showInventory(); } }, h('span', { class: 'ico' }, '☰'), 'Inventory', invSub)));
  get('/api/low-stock').then((l) => {
    if (l.count && S.screen === 'home') { invSub.textContent = `${l.count} running low`; invSub.classList.add('warn'); }
  }).catch(() => {});
}

/** Lock the kiosk, black out the page right away and ask the Pi to switch the real screen off. A touch brings back the PIN screen. */
function screenOff() {
  lockNow();
  sleepNow();
  // Best effort: without the Pi helper (or off the Pi) the black page alone is what you get.
  fetch('http://127.0.0.1:8581/sleep', { method: 'POST', signal: AbortSignal.timeout(3000) }).catch(() => {});
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
  const onScan = (S.screen === 'scan' || S.screen === 'inventory') && !app.querySelector('.overlay');
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
  scanChain = scanChain.then(() => (S.screen === 'inventory' ? findInInventory(code) : doScan(code))).catch(() => {});
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

// Scanning a box on the Inventory screen jumps to that item and shows what is in stock.
async function findInInventory(code) {
  try {
    const r = await get(`/api/inventory/code/${encodeURIComponent(code)}`);
    if (S.screen !== 'inventory') return;
    if (!r.found) { beep(200, 220); toast(`${code} is not in the system`, 'error'); return; }
    if (r.boxes === 0) { beep(520); toast(`${r.label}: none in stock`, 'info'); return; }
    beep(880);
    S.inv = { caliber: r.caliber, weight: r.weight, hit: r.row };
    await showInventory();
  } catch (e) {
    if (e.status !== 401) { beep(200, 220); toast(e.message, 'error'); }
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
    const cam = createCamera(kind, { flip: S.cameraFlip });
    let shot = null, shotUrl = null, busy = false, ready = false;
    const stage = h('div', { class: 'cam-stage' });
    const msg = h('div', { class: 'cam-msg' });
    const actions = h('div', { class: 'cam-actions' });
    const root = h('div', { class: 'overlay cam' }, h('div', { class: 'cam-panel' },
      actions,
      h('div', { class: 'cam-title' }, 'New barcode ', h('b', {}, code), h('span', { class: 'cam-sub' }, ' · photograph the box so you can identify it later')),
      stage, msg));
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
  const n = S.batch.items.length;
  const it = S.last && S.batch.items.find((x) => x.id === S.last.id);
  let main;
  if (!it) {
    main = h('div', { class: 'scan-prompt' },
      h('div', { class: 'big' }, kind === 'in' ? 'Scan boxes going in' : 'Scan boxes going out'),
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
        over && h('div', { class: 'note warn' }, `Only ${it.on_hand} ${isSingle(it) ? 'rounds ' : ''}on hand`)),
      h('div', { class: 'stepper' },
        h('button', { 'aria-label': 'Fewer', onclick: () => setQty(it, it.quantity - 1) }, '−'),
        h('button', { class: 'n', onclick: () => editQty(it) }, String(it.quantity)),
        h('button', { 'aria-label': 'More', onclick: () => setQty(it, it.quantity + 1) }, '+')));
  }
  mount(
    bar({
      kind,
      title: kind === 'in' ? 'AMMO IN' : 'AMMO OUT',
      left: h('button', { class: 'btn', onclick: cancelBatch }, '✕ Cancel'),
      right: h('button', { class: 'btn', onclick: () => manualEntry() }, '⌨ Code'),
    }),
    h('div', { class: 'body' }, h('div', { class: 'scan' }, main)),
    h('div', { class: 'foot' },
      h('div', { class: 'grow summary' }, n ? [h('b', {}, plural(n, 'item')), ` · ${qtyText(S.batch)}`] : h('span', { class: 'muted' }, 'Nothing scanned yet')),
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
  keypad({ title: isSingle(item) ? 'How many rounds?' : 'How many boxes?', value: item.quantity, maxLen: 5, onOk: (v) => setQty(item, Number(v)) });
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
          over ? `Only ${it.on_hand} ${isSingle(it) ? 'rounds ' : ''}on hand` : d.known ? d.sub : 'Will be logged; identify it later on the admin site')),
      h('div', { class: 'stepper sm' },
        h('button', { 'aria-label': 'Fewer', onclick: () => setQty(it, it.quantity - 1) }, '−'),
        h('button', { class: 'n', onclick: () => editQty(it) }, String(it.quantity)),
        h('button', { 'aria-label': 'More', onclick: () => setQty(it, it.quantity + 1) }, '+'),
        h('button', { class: 'rm', 'aria-label': 'Remove', onclick: () => setQty(it, 0) }, '🗑')));
  });
  mount(
    bar({
      kind,
      title: kind === 'in' ? 'REVIEW AMMO IN' : 'REVIEW AMMO OUT',
      left: h('button', { class: 'btn', onclick: showScan }, '← Keep scanning'),
    }),
    h('div', { class: 'body' }, h('div', { class: 'list' }, rows)),
    h('div', { class: 'foot' },
      h('div', { class: 'grow summary' }, h('b', {}, plural(b.items.length, 'item')), ` · ${qtyText(b)}`),
      h('button', { class: 'btn big ' + kind, onclick: finish }, `Finish ammo ${kind}`)));
}

let finishing = false;
async function finish() {
  if (finishing) return;
  const b = S.batch;
  const over = b.kind === 'out' && b.items.some((i) => i.quantity > i.on_hand);
  const outdoor = b.kind === 'out' ? b.items.filter((i) => i.product && i.product.indoor_safe === false) : [];
  if (outdoor.length && !(await confirmDialog({
    title: '⚠ Not for indoor ranges',
    text: outdoor.length === 1 ? 'This ammo is marked outdoor range only:' : 'These items are marked outdoor range only:',
    items: outdoor.map((i) => i.product.label),
    ok: 'Take it anyway', cancel: 'Go back', danger: true }))) return;
  if (over && !(await confirmDialog({ title: 'More than you have on record', text: 'Some items are above the quantity on hand. Record anyway?', ok: 'Record anyway' }))) return;
  finishing = true;
  try {
    const r = await post(`/api/batches/${b.id}/finish`);
    S.batch = null;
    beep(990, 140);
    const done = h('div', { class: 'done', onclick: () => { done.remove(); showHome(); } },
      h('div', { class: 'tick' }, '✓'),
      h('div', { class: 'big' }, `${[r.boxes > 0 && plural(r.boxes, 'box'), r.rounds > 0 && plural(r.rounds, 'round')].filter(Boolean).join(' · ')} ${r.kind === 'in' ? 'added' : 'removed'}`),
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
    S.inv.hit = null;
    if (S.inv.weight !== null) S.inv.weight = null;
    else if (S.inv.caliber !== null) S.inv.caliber = null;
    else return showHome();
    showInventory();
  };
  const open = (r) => {
    if (!r.drillable) return;
    S.inv.hit = null;
    if (d.level === 'caliber') S.inv.caliber = r.key;
    else if (d.level === 'weight') S.inv.weight = r.key;
    showInventory();
  };
  const unknownBoxes = d.rows.filter((r) => r.rounds === null).reduce((n, r) => n + r.boxes, 0);
  mount(
    bar({ title: 'INVENTORY', left: h('button', { class: 'btn', onclick: back }, '← Back') }),
    h('div', { class: 'totals' },
      h('span', { class: 'rounds' }, fmtInt(d.total_rounds)), h('span', { class: 'unit' }, d.total_boxes - unknownBoxes === 0 && d.total_rounds > 0 ? 'rounds' : `rounds · ${plural(d.total_boxes - unknownBoxes, 'box')}`),
      unknownBoxes ? h('span', { class: 'extra' }, `+ ${plural(unknownBoxes, 'box')} unidentified`) : null),
    h('div', { class: 'crumbs' }, d.breadcrumb.length ? crumbs : 'Tap a caliber to drill in, or scan a box to find it'),
    h('div', { class: 'body' }, h('div', { class: 'list' },
      d.rows.length ? d.rows.map((r) =>
        h('button', { class: 'inv-row' + (r.key === S.inv.hit && !r.drillable ? ' hit' : ''), disabled: !r.drillable, onclick: () => open(r) },
          h('div', { class: 'info' }, h('div', { class: 'title' }, r.label, r.low && h('span', { class: 'low-tag' }, 'LOW')), (r.sublabel || r.price) && h('div', { class: 'sub' }, [r.sublabel, r.price && `${fmtPriceRange(r.price)}/rd`].filter(Boolean).join(' · '))),
          h('div', { class: 'r' },
            h('div', { class: 'big' }, r.rounds === null ? '—' : fmtInt(r.rounds)),
            h('small', {}, r.rounds === null ? plural(r.boxes, 'box') : r.single || (r.boxes === 0 && r.rounds > 0) ? 'rounds' : `rounds · ${plural(r.boxes, 'box')}`)),
          r.drillable && h('span', { class: 'chev' }, '›')))
        : h('div', { class: 'empty' }, 'Nothing in stock yet. Use Ammo In to add boxes.'))));
  app.querySelector('.inv-row.hit')?.scrollIntoView({ block: 'center' });
}

// Pick up a new server version by itself, but only while resting on the lock or home screen.
watchBuild(() => S.screen === 'lock' || S.screen === 'home');

boot();
