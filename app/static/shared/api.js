// fetch wrapper. A 401 anywhere fires 'qm:locked' so the UI can show the PIN screen.

// Filled in by the server with the build this script came from (see app/assets.py).
export const BUILD = '__BUILD__';

/**
 * Reload this page when the server has been updated. Checks every minute and whenever the tab becomes
 * visible again. canReload() lets the page hold off while the user is in the middle of something.
 */
export function watchBuild(canReload) {
  if (BUILD.startsWith('__')) return () => {}; // served without substitution (not by the app)
  let busy = false;
  const check = async () => {
    if (busy) return;
    busy = true;
    try {
      const res = await fetch('/api/health', { cache: 'no-store' });
      const { build } = await res.json();
      if (build && build !== BUILD && canReload()) location.reload();
    } catch { /* offline: try again next time */ }
    busy = false;
  };
  const timer = setInterval(check, 60000);
  const onVisible = () => { if (document.visibilityState === 'visible') check(); };
  document.addEventListener('visibilitychange', onVisible);
  return () => { clearInterval(timer); document.removeEventListener('visibilitychange', onVisible); };
}

export class ApiError extends Error {
  constructor(status, message, retryAfter) {
    super(message);
    this.status = status;
    this.retryAfter = retryAfter || 0;
  }
}

async function request(path, opts) {
  let res;
  try {
    res = await fetch(path, { credentials: 'same-origin', ...opts });
  } catch {
    throw new ApiError(0, 'Cannot reach the server');
  }
  if (res.ok) {
    const type = res.headers.get('content-type') || '';
    return type.includes('json') ? res.json() : res;
  }
  let msg = res.statusText;
  try {
    const j = await res.json();
    const d = j.detail;
    msg = typeof d === 'string' ? d : Array.isArray(d) ? d.map((e) => e.msg).join('; ') : msg;
  } catch { /* not json */ }
  const err = new ApiError(res.status, msg, Number(res.headers.get('retry-after')));
  if (res.status === 401 && !path.startsWith('/api/auth/')) {
    window.dispatchEvent(new CustomEvent('qm:locked'));
  }
  throw err;
}

export function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  return request(path, opts);
}

/** Send a File/Blob as the raw request body (photos, backup files). */
export function sendBlob(method, path, blob) {
  return request(path, { method, headers: { 'Content-Type': blob.type || 'application/octet-stream' }, body: blob });
}

export const get = (p) => api('GET', p);
export const post = (p, b = {}) => api('POST', p, b);
export const put = (p, b = {}) => api('PUT', p, b);
export const patch = (p, b = {}) => api('PATCH', p, b);
export const del = (p) => api('DELETE', p);

// Sign-in plumbing used by both sites -----------------------------------------

/** Calls onLock() whenever the session is gone (server said 401, or idle timer fired). */
export function watchSession({ idleMinutes, onLock }) {
  let idleTimer;
  let lastPing = 0;
  const arm = () => {
    clearTimeout(idleTimer);
    idleTimer = setTimeout(async () => {
      try { await post('/api/auth/logout'); } catch { /* offline: server will expire it anyway */ }
      onLock();
    }, idleMinutes * 60 * 1000);
  };
  const activity = () => {
    arm();
    const now = Date.now();
    if (now - lastPing > 30_000) {
      lastPing = now;
      post('/api/auth/ping').catch(() => {});
    }
  };
  const locked = () => { clearTimeout(idleTimer); onLock(); };
  const events = ['pointerdown', 'keydown'];
  for (const ev of events) window.addEventListener(ev, activity, { passive: true });
  window.addEventListener('qm:locked', locked);
  arm();
  return {
    stop() {
      clearTimeout(idleTimer);
      for (const ev of events) window.removeEventListener(ev, activity);
      window.removeEventListener('qm:locked', locked);
    },
  };
}
