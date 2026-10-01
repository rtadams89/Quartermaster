#!/usr/bin/env bash
# Quartermaster kiosk installer for a Raspberry Pi (Raspberry Pi OS Lite, Bookworm or newer).
#
# Sets the Pi up to boot straight into the Quartermaster kiosk page: installs the packages, writes the
# systemd service and launcher, and (if you ask for them) the camera helper, the screen rotation (0/90/180/270),
# the "ignore HDMI pseudo-pointers" fix that hides the stray mouse cursor, and a screen-blank timeout.
# Anything that is a real decision is asked in a menu; just press Enter to accept the [default].
#
#   sudo bash install.sh                 interactive install (safe to re-run to change a choice)
#   sudo bash install.sh --uninstall     remove everything this script created
#   bash install.sh --dry-run            walk through the menus and show what would be done, changing nothing
#   sudo bash install.sh --yes --url=http://192.168.1.50:8580
#                                        no questions: use the saved choices (or defaults) and this server
#
# This one file is all the Pi needs: the camera helper, service files and udev rule are built in.
#   sudo bash install.sh --print-helper  prints the built-in camera helper (for a manual install)
set -euo pipefail

CONF=/etc/quartermaster-kiosk.conf
UNIT=quartermaster-kiosk
LAUNCHER=/usr/local/bin/quartermaster-kiosk-browser
RULES=/etc/udev/rules.d/99-kiosk-ignore-pointers.rules
HELPER_DIR=/opt/quartermaster

# QM_INSTALL_ROOT redirects every file write into another directory and skips all system commands.
# It exists so the installer can be tested on a machine that is not a Pi.
ROOT="${QM_INSTALL_ROOT:-}"
DRY=0; YES=0; UNINSTALL=0; ARG_URL=""; PRINT_HELPER=0
[ -n "$ROOT" ] && DRY=1

for a in "$@"; do
  case "$a" in
    --dry-run) DRY=1 ;;
    --yes|-y) YES=1 ;;
    --uninstall) UNINSTALL=1 ;;
    --print-helper) PRINT_HELPER=1 ;;
    --url=*) ARG_URL="${a#--url=}" ;;
    -h|--help) sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $a (try --help)" >&2; exit 2 ;;
  esac
done

# The Pi camera helper (CSI camera modules only). Kept here so this file is the only thing the Pi needs.
emit_helper() {
  cat <<'QM_HELPER_EOF'
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
QM_HELPER_EOF
}
[ "$PRINT_HELPER" = 1 ] && { emit_helper; exit 0; }

# ----------------------------------------------------------------- helpers
say()  { printf '%s\n' "$*"; }
step() { printf '\n==> %s\n' "$*"; }
die()  { printf '\nERROR: %s\n' "$*" >&2; exit 1; }
run()  { if [ "$DRY" = 1 ]; then printf '   [dry-run] %s\n' "$*"; else "$@"; fi; }
path() { printf '%s%s' "$ROOT" "$1"; }
put()  { # put FILE MODE  (content on stdin)
  local f; f="$(path "$1")"
  if [ "$DRY" = 1 ] && [ -z "$ROOT" ]; then printf '   [dry-run] would write %s\n' "$1"; cat >/dev/null; return; fi
  mkdir -p "$(dirname "$f")"; cat >"$f"; chmod "$2" "$f"; printf '   wrote %s\n' "$1"
}
read_line() { local __rl_target="$1" __rl_text=""; read -r __rl_text || die "Input ended before the questions were answered."; printf -v "$__rl_target" '%s' "$__rl_text"; }

ask() { # ask VAR "Question" default
  local __v="$1" q="$2" def="$3" ans
  if [ "$YES" = 1 ]; then printf -v "$__v" '%s' "$def"; return; fi
  printf '%s [%s]: ' "$q" "$def"; read_line ans
  printf -v "$__v" '%s' "${ans:-$def}"
}

menu() { # menu VAR "Title" default_key key1 "Label 1" key2 "Label 2" ...
  local __v="$1" title="$2" def="$3"; shift 3
  local keys=() labels=() i=1 defn=1 ans n
  while [ $# -gt 0 ]; do keys+=("$1"); labels+=("$2"); [ "$1" = "$def" ] && defn=$i; i=$((i + 1)); shift 2; done
  n=${#keys[@]}
  if [ "$YES" = 1 ]; then printf -v "$__v" '%s' "$def"; return; fi
  printf '\n%s\n' "$title"
  for ((i = 0; i < n; i++)); do printf '  %d) %s\n' $((i + 1)) "${labels[i]}"; done
  while true; do
    printf 'Choose 1-%d [%d]: ' "$n" "$defn"; read_line ans
    ans="${ans:-$defn}"
    if [[ "$ans" =~ ^[0-9]+$ ]] && [ "$ans" -ge 1 ] && [ "$ans" -le "$n" ]; then
      printf -v "$__v" '%s' "${keys[ans - 1]}"; return
    fi
    say "Please type a number from the list."
  done
}

yesno() { # yesno "Question" default(y|n)  -> returns 0 for yes
  local q="$1" def="$2" ans
  if [ "$YES" = 1 ]; then [ "$def" = y ]; return; fi
  while true; do
    printf '%s [%s]: ' "$q" "$([ "$def" = y ] && echo Y/n || echo y/N)"; read_line ans
    ans="${ans:-$def}"
    case "${ans,,}" in y|yes) return 0 ;; n|no) return 1 ;; esac
    say "Please answer y or n."
  done
}

# ----------------------------------------------------------------- checks
if [ "$DRY" = 0 ] && [ "$(id -u)" -ne 0 ]; then
  exec sudo -E bash "$0" "$@"
fi
if [ "$DRY" = 0 ] && ! grep -qiE 'raspberry|debian' "$(path /etc/os-release)" 2>/dev/null; then
  die "This installer is written for Raspberry Pi OS (Debian)."
fi

# ----------------------------------------------------------------- uninstall
if [ "$UNINSTALL" = 1 ]; then
  say "This removes the Quartermaster kiosk service, launcher, camera helper, udev rule and saved settings."
  say "It leaves installed packages, your user account and boot settings alone."
  yesno "Remove them now?" n || { say "Nothing changed."; exit 0; }
  for u in "$UNIT" quartermaster-camera; do
    run systemctl disable --now "$u.service" 2>/dev/null || true
    [ "$DRY" = 1 ] && [ -z "$ROOT" ] && continue
    rm -f "$(path "/etc/systemd/system/$u.service")"
  done
  if [ "$DRY" = 0 ] || [ -n "$ROOT" ]; then
    rm -f "$(path "$LAUNCHER")" "$(path "$RULES")" "$(path "$CONF")"
    rm -rf "$(path "$HELPER_DIR")"
  fi
  run systemctl daemon-reload
  run udevadm control --reload-rules
  say "Removed. Reboot to get the normal console login back."
  exit 0
fi

# ----------------------------------------------------------------- defaults (saved choices win)
KIOSK_USER=""; KIOSK_URL=""; ROTATION="0"; CAMERA="none"; IGNORE_HDMI="yes"; BLANK_AFTER="0"; AUTOSTART="yes"
# shellcheck disable=SC1090
[ -r "$(path "$CONF")" ] && . "$(path "$CONF")"
# Choices saved by older versions of this installer.
case "$ROTATION" in none) ROTATION=0 ;; browser90|os90) ROTATION=90 ;; browser270|os270) ROTATION=270 ;; esac
case "$BLANK_AFTER" in 0|900|3600|14400) ;; *) BLANK_AFTER=0 ;; esac
[ -n "$ARG_URL" ] && KIOSK_URL="$ARG_URL"
if [ -z "$KIOSK_USER" ]; then
  if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != root ]; then KIOSK_USER="$SUDO_USER"; else KIOSK_USER="kiosk"; fi
fi

say "Quartermaster kiosk installer"
say "Press Enter to accept the answer shown in [brackets]."
[ "$DRY" = 1 ] && say "(dry run: nothing will be changed)"

# ----------------------------------------------------------------- 1. server
step "1 of 6: the Quartermaster server"
say "The Pi runs no Quartermaster code; it just opens a page from your server."
while true; do
  ask KIOSK_URL "Server address (for example http://192.168.1.50:8580)" "${KIOSK_URL:-http://quartermaster.local:8580}"
  KIOSK_URL="${KIOSK_URL%/}"; KIOSK_URL="${KIOSK_URL%/kiosk}"; KIOSK_URL="${KIOSK_URL%/}"
  [[ "$KIOSK_URL" =~ ^https?:// ]] || KIOSK_URL="http://$KIOSK_URL"
  if ! command -v curl >/dev/null 2>&1; then say "(curl is not installed yet, so the server is not checked)"; break; fi
  if reply="$(curl -fsS -m 6 "$KIOSK_URL/api/health" 2>/dev/null)"; then
    say "   Reached the server: $reply"; break
  fi
  say "   Could not reach $KIOSK_URL/api/health from here."
  [ "$YES" = 1 ] && { say "   Continuing anyway (--yes)."; break; }
  yesno "   Use this address anyway (the server may just not be running yet)?" n && break
done

# ----------------------------------------------------------------- 2. user
step "2 of 6: the account that runs the kiosk"
ask KIOSK_USER "Linux user" "$KIOSK_USER"
CREATE_USER=0
if [ "$DRY" = 0 ] && ! id "$KIOSK_USER" >/dev/null 2>&1; then
  yesno "   User '$KIOSK_USER' does not exist. Create it (no password, kiosk use only)?" y || die "Pick an existing user and run again."
  CREATE_USER=1
fi

# ----------------------------------------------------------------- 3. orientation
step "3 of 6: screen rotation"
say "The kiosk page turns itself (the URL gets ?rotate=...), so no operating-system display settings are touched."
menu ROTATION "How should the picture be turned?" "$ROTATION" \
  0   "0°: normal landscape (800x480)" \
  90  "90°: turned clockwise, portrait" \
  180 "180°: upside-down landscape" \
  270 "270°: turned counter-clockwise, portrait"
[ "$ROTATION" = 0 ] || say "   If the picture comes out the wrong way up, run this installer again and pick the opposite angle (90 <-> 270)."

# ----------------------------------------------------------------- 4. camera
step "4 of 6: box-photo camera"
menu CAMERA "Which camera takes pictures of new boxes?" "$CAMERA" \
  none "No camera (the kiosk simply skips the photo step)" \
  usb  "A USB webcam (live preview)" \
  csi  "A Raspberry Pi camera module on the ribbon cable (takes a still; installs a small helper service)"

# ----------------------------------------------------------------- 5. hardware fixes
step "5 of 6: fixes for common Pi problems"
say "The Pi's two HDMI ports register fake 'mouse' devices, which makes the kiosk compositor draw a mouse arrow"
say "in the middle of the screen. Telling the system to ignore them hides it. (Your touchscreen is not affected.)"
if yesno "Ignore the HDMI pseudo-pointers (recommended)?" "$([ "$IGNORE_HDMI" = yes ] && echo y || echo n)"; then IGNORE_HDMI=yes; else IGNORE_HDMI=no; fi
say ""
menu BLANK_AFTER "Turn the screen off after how long without a touch or scan?" "$BLANK_AFTER" \
  0     "Never blank (always on)" \
  900   "After 15 minutes of inactivity" \
  3600  "After 1 hour of inactivity" \
  14400 "After 4 hours of inactivity"
[ "$BLANK_AFTER" = 0 ] || say "   A touch wakes it; the page goes black just before and swallows that first touch, so it never presses a button."

# ----------------------------------------------------------------- 6. start-up
step "6 of 6: start-up"
if yesno "Start the kiosk automatically at every boot (recommended)?" "$([ "$AUTOSTART" = yes ] && echo y || echo n)"; then AUTOSTART=yes; else AUTOSTART=no; fi

# ----------------------------------------------------------------- confirm
rot_label() { case "$1" in 0) echo "0° (normal)";; 90) echo "90° clockwise";; 180) echo "180° (upside-down)";; 270) echo "270° (counter-clockwise)";; esac; }
blank_label() { case "$1" in 0) echo "never";; 900) echo "after 15 minutes";; 3600) echo "after 1 hour";; 14400) echo "after 4 hours";; esac; }
cam_label() { case "$1" in none) echo "none";; usb) echo "USB webcam";; csi) echo "Pi camera module + helper";; esac; }
printf '\n---------------------------------------------\nReady to install with:\n'
printf '  Server:           %s\n  Kiosk user:       %s%s\n  Orientation:      %s\n  Camera:           %s\n' \
  "$KIOSK_URL" "$KIOSK_USER" "$([ "$CREATE_USER" = 1 ] && echo ' (will be created)')" "$(rot_label "$ROTATION")" "$(cam_label "$CAMERA")"
printf '  Hide mouse arrow: %s\n  Screen blanks:    %s\n  Start at boot:    %s\n---------------------------------------------\n' \
  "$IGNORE_HDMI" "$(blank_label "$BLANK_AFTER")" "$AUTOSTART"
yesno "Go ahead?" y || { say "Nothing was changed."; exit 0; }

# ----------------------------------------------------------------- install
step "Installing packages"
PKGS=(cage fonts-dejavu-core fonts-noto-color-emoji curl)
if [ "$DRY" = 1 ]; then CHROMIUM_PKG=chromium
elif apt-cache show chromium >/dev/null 2>&1; then CHROMIUM_PKG=chromium; else CHROMIUM_PKG=chromium-browser; fi
PKGS+=("$CHROMIUM_PKG")
[ "$BLANK_AFTER" = 0 ] || PKGS+=(swayidle wlr-randr)
if [ "$CAMERA" = csi ]; then
  if [ "$DRY" = 0 ] && ! apt-cache show rpicam-apps-lite >/dev/null 2>&1; then PKGS+=(libcamera-apps-lite); else PKGS+=(rpicam-apps-lite); fi
fi
run apt-get update
run env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${PKGS[@]}"

if [ "$CREATE_USER" = 1 ]; then
  step "Creating user $KIOSK_USER"
  run adduser --disabled-password --gecos "Quartermaster kiosk" "$KIOSK_USER"
fi
run usermod -aG video,render "$KIOSK_USER"

step "Saving your choices to $CONF"
put "$CONF" 644 <<EOF
# Written by pi/install.sh. Re-run the installer to change these; the launcher reads this file at every start.
KIOSK_USER="$KIOSK_USER"
KIOSK_URL="$KIOSK_URL"
ROTATION="$ROTATION"
CAMERA="$CAMERA"
IGNORE_HDMI="$IGNORE_HDMI"
BLANK_AFTER="$BLANK_AFTER"
AUTOSTART="$AUTOSTART"
EOF

step "Writing the browser launcher"
put "$LAUNCHER" 755 <<'EOF'
#!/bin/sh
# Started by cage. Reads /etc/quartermaster-kiosk.conf, so changing a choice never needs a new unit file.
. /etc/quartermaster-kiosk.conf

# Turn the screen off after BLANK_AFTER seconds without a touch or scan, and back on at the next one.
if [ "${BLANK_AFTER:-0}" -gt 0 ] 2>/dev/null; then
  out="$(wlr-randr 2>/dev/null | awk 'NR==1 {print $1}')"
  if [ -n "$out" ]; then
    swayidle -w timeout "$BLANK_AFTER" "wlr-randr --output $out --off" resume "wlr-randr --output $out --on" &
  fi
fi

# Settings the page needs go on the URL: its rotation, and the blank period so it can swallow the wake-up touch.
URL="$KIOSK_URL/kiosk/"
SEP="?"
case "${ROTATION:-0}" in
  90|180|270) URL="$URL${SEP}rotate=$ROTATION"; SEP="&" ;;
esac
if [ "${BLANK_AFTER:-0}" -gt 0 ] 2>/dev/null; then URL="$URL${SEP}blank=$BLANK_AFTER"; fi

BROWSER="$(command -v chromium || command -v chromium-browser)"
EXTRA=""
if [ "$CAMERA" = usb ]; then
  # Browsers only allow cameras on HTTPS/localhost; this trusts just your own server and answers the prompt.
  EXTRA="--use-fake-ui-for-media-stream --unsafely-treat-insecure-origin-as-secure=$KIOSK_URL"
fi

# shellcheck disable=SC2086
exec "$BROWSER" --kiosk --noerrdialogs --disable-infobars --no-first-run \
  --disable-session-crashed-bubble --disable-pinch --overscroll-history-navigation=0 \
  --password-store=basic --check-for-update-interval=31536000 \
  --user-data-dir="$HOME/.config/qm-kiosk" $EXTRA "$URL"
EOF

step "Writing the kiosk service"
CAGE_BIN="$(command -v cage 2>/dev/null || echo /usr/bin/cage)"
put "/etc/systemd/system/$UNIT.service" 644 <<EOF
[Unit]
Description=Quartermaster kiosk
After=systemd-user-sessions.service network-online.target getty@tty1.service
Wants=network-online.target
Conflicts=getty@tty1.service

[Service]
User=$KIOSK_USER
PAMName=login
TTYPath=/dev/tty1
StandardInput=tty
StandardOutput=journal
UtmpIdentifier=tty1
UtmpMode=user
ExecStart=$CAGE_BIN -s -- $LAUNCHER
Restart=always
RestartSec=3

[Install]
WantedBy=graphical.target
EOF

if [ "$IGNORE_HDMI" = yes ]; then
  step "Hiding the stray mouse arrow"
  put "$RULES" 644 <<'EOF'
# The Pi's HDMI ports register CEC input devices that claim to be keyboard+pointer. cage draws a mouse
# pointer whenever any pointer exists, so tell libinput to ignore these. Do NOT list the touchscreen.
SUBSYSTEM=="input", ATTRS{name}=="vc4-hdmi-0", ENV{LIBINPUT_IGNORE_DEVICE}="1"
SUBSYSTEM=="input", ATTRS{name}=="vc4-hdmi-1", ENV{LIBINPUT_IGNORE_DEVICE}="1"
EOF
  run udevadm control --reload-rules
  run udevadm trigger
elif [ -e "$(path "$RULES")" ]; then
  rm -f "$(path "$RULES")"; say "   removed the old pointer rule"
fi

if [ "$CAMERA" = csi ]; then
  step "Installing the Pi camera helper"
  if [ "$DRY" = 1 ] && [ -z "$ROOT" ]; then
    say "   [dry-run] would write camera_helper.py to $HELPER_DIR"
  else
    mkdir -p "$(path "$HELPER_DIR")"
    emit_helper >"$(path "$HELPER_DIR")/camera_helper.py"; chmod 755 "$(path "$HELPER_DIR")/camera_helper.py"
    say "   wrote $HELPER_DIR/camera_helper.py"
  fi
  put "/etc/systemd/system/quartermaster-camera.service" 644 <<EOF
[Unit]
Description=Quartermaster camera helper (Pi camera module -> kiosk)
After=local-fs.target

[Service]
User=$KIOSK_USER
SupplementaryGroups=video
# Exactly the address the kiosk loads, so no other website can ask for photos.
Environment=QM_CAMERA_ORIGIN=$KIOSK_URL
ExecStart=/usr/bin/python3 $HELPER_DIR/camera_helper.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
else
  run systemctl disable --now quartermaster-camera.service 2>/dev/null || true
  [ -e "$(path /etc/systemd/system/quartermaster-camera.service)" ] && rm -f "$(path /etc/systemd/system/quartermaster-camera.service)"
fi

step "Enabling services"
run systemctl daemon-reload
run systemctl set-default graphical.target
if [ "$AUTOSTART" = yes ]; then run systemctl enable "$UNIT.service"; else run systemctl disable "$UNIT.service" 2>/dev/null || true; fi
[ "$CAMERA" = csi ] && run systemctl enable --now quartermaster-camera.service

printf '\n=============================================\nDone.\n'
say "  Start it now:    sudo systemctl start $UNIT      (or just reboot)"
say "  Watch the log:   journalctl -u $UNIT -b"
say "  Change a choice: run this installer again"
say "  Remove it all:   sudo bash install.sh --uninstall"
[ "$CAMERA" = csi ] && say "  Camera check:    curl http://127.0.0.1:8581/health    (should say \"camera\": true)"
[ "$ROTATION" != 0 ] && say "  Wrong way up?    run the installer again and pick the opposite angle (90 <-> 270)"
if [ "$DRY" = 0 ] && [ "$YES" = 0 ]; then
  yesno "Reboot now to start the kiosk?" y && { say "Rebooting..."; sleep 2; reboot; }
fi
exit 0
