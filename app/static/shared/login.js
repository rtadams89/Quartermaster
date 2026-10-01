// PIN pad used by both sites. Also handles first-run PIN creation.
import { h, clear } from './dom.js';
import { post, ApiError } from './api.js';

export function renderLogin(root, status, onSuccess, title = 'Quartermaster') {
  const setup = !status.pin_set;
  let entry = '';
  let first = null; // during setup: the first PIN, awaiting confirmation
  let busy = false;
  let lockTimer = null;
  let lockedUntil = status.retry_after ? Date.now() + status.retry_after * 1000 : 0;

  const dots = h('div', { class: 'pin-dots' }, [0, 1, 2, 3].map(() => h('span')));
  const msg = h('div', { class: 'pin-msg', 'aria-live': 'polite' });
  const prompt = h('div', { class: 'pin-prompt' });

  function paint() {
    [...dots.children].forEach((d, i) => d.classList.toggle('on', i < entry.length));
    prompt.textContent = setup ? (first ? 'Confirm your new PIN' : 'Choose a 4-digit PIN') : 'Enter PIN';
  }

  function tickLock() {
    clearInterval(lockTimer);
    const update = () => {
      const left = Math.ceil((lockedUntil - Date.now()) / 1000);
      if (left <= 0) {
        clearInterval(lockTimer);
        lockedUntil = 0;
        msg.textContent = '';
        pad.classList.remove('disabled');
        return;
      }
      pad.classList.add('disabled');
      const m = Math.floor(left / 60), s = String(left % 60).padStart(2, '0');
      msg.textContent = `Too many attempts. Try again in ${m}:${s}`;
    };
    update();
    lockTimer = setInterval(update, 500);
  }

  async function submit() {
    busy = true;
    const pin = entry;
    try {
      if (setup) {
        if (!first) { first = pin; entry = ''; paint(); busy = false; return; }
        if (first !== pin) {
          first = null; entry = ''; msg.textContent = "PINs didn't match. Start again."; paint(); busy = false; return;
        }
        await post('/api/auth/setup', { pin });
      } else {
        await post('/api/auth/login', { pin });
      }
      clearInterval(lockTimer);
      onSuccess();
    } catch (e) {
      entry = '';
      dots.classList.add('shake');
      setTimeout(() => dots.classList.remove('shake'), 400);
      if (e instanceof ApiError && e.status === 429) {
        lockedUntil = Date.now() + (e.retryAfter || 60) * 1000;
        tickLock();
      } else {
        msg.textContent = e.message;
      }
      paint();
      busy = false;
    }
  }

  function press(d) {
    if (busy || lockedUntil > Date.now()) return;
    if (d === 'back') entry = entry.slice(0, -1);
    else if (entry.length < 4) entry += d;
    if (entry.length) msg.textContent = '';
    paint();
    if (entry.length === 4) submit();
  }

  const keys = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '', '0', 'back'];
  const pad = h('div', { class: 'pin-pad' }, keys.map((k) =>
    k === '' ? h('span') : h('button', { type: 'button', class: 'pin-key', 'aria-label': k === 'back' ? 'Delete' : k, onclick: () => press(k) }, k === 'back' ? '⌫' : k)
  ));

  const onKey = (e) => {
    if (!root.isConnected) { window.removeEventListener('keydown', onKey); return; }
    if (/^\d$/.test(e.key)) press(e.key);
    else if (e.key === 'Backspace') press('back');
  };
  window.addEventListener('keydown', onKey);

  clear(root, h('div', { class: 'pin-screen' }, h('div', { class: 'pin-brand' }, title), prompt, dots, msg, pad));
  paint();
  if (lockedUntil > Date.now()) tickLock();
}
