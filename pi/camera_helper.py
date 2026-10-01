#!/usr/bin/env python3
"""Quartermaster camera helper for the Raspberry Pi.

Why this exists: Chromium can't open Raspberry Pi *CSI* camera modules (they speak libcamera, not
a plain webcam interface). This ~100-line service runs on the Pi, takes a still with `rpicam-still`
when the kiosk page asks for one, and hands the JPEG back. It listens on 127.0.0.1 only, so nothing
else on your network can reach the camera.

USB webcams do NOT need this: the kiosk uses them directly through the browser (see docs/pi-kiosk.md).

    GET /health         -> {"ok": true, "camera": true|false}
    GET /snapshot.jpg   -> a fresh JPEG from the camera

Configuration (environment variables):
    QM_CAMERA_PORT     port to listen on                         (default 8581)
    QM_CAMERA_ORIGIN   the kiosk's origin, e.g. http://192.168.1.50:8580. Browsers from other origins
                       are refused. Strongly recommended; if unset, any page may request a photo.
    QM_CAMERA_WIDTH / QM_CAMERA_HEIGHT   still size             (default 1280x960)
    QM_CAMERA_COMMAND  override the capture program              (default: rpicam-still, else libcamera-still)
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("QM_CAMERA_PORT", "8581"))
ORIGIN = os.environ.get("QM_CAMERA_ORIGIN", "").rstrip("/")
WIDTH = os.environ.get("QM_CAMERA_WIDTH", "1280")
HEIGHT = os.environ.get("QM_CAMERA_HEIGHT", "960")
COMMAND = os.environ.get("QM_CAMERA_COMMAND") or shutil.which("rpicam-still") or shutil.which("libcamera-still")

_capture_lock = threading.Lock()  # the camera can only do one thing at a time


def capture() -> bytes:
    if not COMMAND:
        raise RuntimeError("rpicam-still not found (install rpicam-apps)")
    with _capture_lock, tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "shot.jpg")
        subprocess.run(
            [COMMAND, "--nopreview", "-t", "800", "--width", WIDTH, "--height", HEIGHT, "--quality", "85", "-o", out],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=25,
        )
        with open(out, "rb") as f:
            return f.read()


class Handler(BaseHTTPRequestHandler):
    server_version = "QuartermasterCamera"

    def _cors(self):
        origin = self.headers.get("Origin")
        if not ORIGIN:
            self.send_header("Access-Control-Allow-Origin", "*")
        elif origin == ORIGIN:
            self.send_header("Access-Control-Allow-Origin", ORIGIN)
            self.send_header("Vary", "Origin")
        # Chromium's "Private Network Access" check, for a page on the LAN calling loopback:
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def _allowed(self) -> bool:
        origin = self.headers.get("Origin")
        return not (ORIGIN and origin and origin != ORIGIN)

    def _send(self, status: int, body: bytes, content_type: str):
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):  # CORS preflight
        self.send_response(204 if self._allowed() else 403)
        self._cors()
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        if not self._allowed():
            return self._send(403, b'{"error":"origin not allowed"}', "application/json")
        path = self.path.split("?")[0]
        if path == "/health":
            return self._send(200, json.dumps({"ok": True, "camera": bool(COMMAND)}).encode(), "application/json")
        if path == "/snapshot.jpg":
            try:
                return self._send(200, capture(), "image/jpeg")
            except Exception as e:  # noqa: BLE001 - report anything to the kiosk, which shows it
                return self._send(500, json.dumps({"error": str(e)}).encode(), "application/json")
        self._send(404, b'{"error":"not found"}', "application/json")

    def log_message(self, fmt, *args):  # quiet; journald has the service's own start/stop lines
        pass


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Quartermaster camera helper on 127.0.0.1:{PORT} (command: {COMMAND or 'NOT FOUND'}, origin: {ORIGIN or 'any'})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
