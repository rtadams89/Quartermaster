// Camera access for the kiosk. Two back ends, picked automatically:
//
//  'helper'  – a tiny local service on the Pi (pi/camera_helper.py) that takes a still with
//              rpicam-still. This is the reliable route for Raspberry Pi *CSI* camera modules,
//              which Chromium cannot open directly. No live preview; the photo is shown after.
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
export function createCamera(kind) {
  return kind === 'helper' ? helperCamera() : browserCamera();
}

function browserCamera() {
  const video = h('video', { class: 'cam-video', autoplay: true, muted: true, playsinline: true });
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
      const scale = Math.min(1, MAX_SIDE / Math.max(w, hh));
      const canvas = document.createElement('canvas');
      canvas.width = Math.round(w * scale);
      canvas.height = Math.round(hh * scale);
      canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
      return new Promise((resolve, reject) =>
        canvas.toBlob((b) => (b ? resolve(b) : reject(new Error('Could not capture image'))), 'image/jpeg', 0.85));
    },
    stop() {
      stream?.getTracks().forEach((t) => t.stop());
      video.srcObject = null;
    },
  };
}

function helperCamera() {
  return {
    el: h('div', { class: 'cam-placeholder' }, h('div', { class: 'ico' }, '📷'), h('div', {}, 'Hold the box in front of the camera, then tap Take photo')),
    async start() {},
    async snapshot() {
      const r = await fetch(HELPER + '/snapshot.jpg', { signal: AbortSignal.timeout(25000) });
      if (!r.ok) throw new Error('The camera helper could not take a photo');
      return r.blob();
    },
    stop() {},
  };
}
