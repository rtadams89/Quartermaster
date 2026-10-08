// Camera access for the kiosk. Two back ends, picked automatically:
//
//  'helper'  – a tiny local service on the Pi (built into pi/install.sh) that reads the camera with
//              rpicam-vid. This is the route for Raspberry Pi *CSI* camera modules, which Chromium
//              cannot open directly. The page polls it for the newest frame, so there is a live
//              preview too.
//  'browser' – the browser's own camera access (getUserMedia). Works with USB webcams and gives a
//              live preview. Needs a secure context: see docs/pi-kiosk.md for the Chromium flag.

import { h } from '/shared/dom.js';

const HELPER = 'http://127.0.0.1:8581';
const MAX_SIDE = 1280;

export async function detectCamera() {
  try {
    const r = await fetch(HELPER + '/health', { signal: AbortSignal.timeout(1500) });
    if (r.ok && (await r.json()).camera) return 'helper';
  } catch { /* helper not running: fine */ }
  try {
    if (navigator.mediaDevices?.enumerateDevices) {
      const devices = await navigator.mediaDevices.enumerateDevices();
      if (devices.some((d) => d.kind === 'videoinput')) return 'browser';
    }
  } catch { /* no camera API in this context */ }
  return null;
}

/** Returns { el, start(), snapshot(), stop() }. `el` is what to show while framing the shot. */
export function createCamera(kind, { flip = false } = {}) {
  return kind === 'helper' ? helperCamera(flip) : browserCamera(flip);
}

/** Draw a picture onto a canvas no bigger than MAX_SIDE, turned half a turn when `flip` is set. */
function toJpeg(source, w, hh, flip) {
  const scale = Math.min(1, MAX_SIDE / Math.max(w, hh));
  const canvas = document.createElement('canvas');
  canvas.width = Math.round(w * scale);
  canvas.height = Math.round(hh * scale);
  const ctx = canvas.getContext('2d');
  if (flip) { ctx.translate(canvas.width, canvas.height); ctx.rotate(Math.PI); }
  ctx.drawImage(source, 0, 0, canvas.width, canvas.height);
  return new Promise((resolve, reject) =>
    canvas.toBlob((b) => (b ? resolve(b) : reject(new Error('Could not capture image'))), 'image/jpeg', 0.85));
}

function browserCamera(flip) {
  const video = h('video', { class: 'cam-video', autoplay: true, muted: true, playsinline: true });
  if (flip) video.style.transform = 'rotate(180deg)';
  video.muted = true;
  let stream = null;
  return {
    el: video,
    async start() {
      stream = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 1280 }, height: { ideal: 960 } },
        audio: false,
      });
      video.srcObject = stream;
      await video.play();
      if (!video.videoWidth) await new Promise((res) => video.addEventListener('loadedmetadata', res, { once: true }));
    },
    snapshot() {
      const w = video.videoWidth, hh = video.videoHeight;
      if (!w || !hh) throw new Error('Camera is not ready yet');
      return toJpeg(video, w, hh, flip);
    },
    stop() {
      stream?.getTracks().forEach((t) => t.stop());
      video.srcObject = null;
    },
  };
}

/** Live preview from the Pi helper: fetch the newest frame over and over (long-polling, so each
 *  request returns as soon as there is a new picture). The photo is simply the frame on screen. */
function helperCamera(flip) {
  const img = h('img', { class: 'cam-video', alt: '' });
  if (flip) img.style.transform = 'rotate(180deg)';
  let running = false, last = null, lastUrl = null;
  const loop = async (onFirst, onFail) => {
    let seq = 0, failures = 0, got = false;
    while (running) {
      try {
        const r = await fetch(`${HELPER}/frame.jpg?after=${seq}`, { signal: AbortSignal.timeout(8000) });
        if (!r.ok) throw new Error('no picture');
        seq = Number(r.headers.get('X-Frame')) || seq;
        last = await r.blob();
        const url = URL.createObjectURL(last);
        img.src = url;
        if (lastUrl) URL.revokeObjectURL(lastUrl);
        lastUrl = url;
        failures = 0;
        if (!got) { got = true; onFirst(); }
        await new Promise((res) => setTimeout(res, 40)); // ~25 fps ceiling; the camera sets the real rate
      } catch {
        if (!got && ++failures >= 3) { onFail(new Error('The camera helper is not producing pictures')); return; }
        await new Promise((res) => setTimeout(res, 500));
      }
    }
  };
  return {
    el: img,
    start() {
      running = true;
      return new Promise((resolve, reject) => loop(resolve, reject));
    },
    async snapshot() {
      if (!last) throw new Error('Camera is not ready yet');
      if (!flip) return last;
      const bitmap = await createImageBitmap(last);
      try { return await toJpeg(bitmap, bitmap.width, bitmap.height, true); } finally { bitmap.close(); }
    },
    stop() {
      running = false;
      if (lastUrl) URL.revokeObjectURL(lastUrl);
      lastUrl = last = null;
      img.removeAttribute('src');
    },
  };
}
