# Changelog

The version lives in one place, `app/__init__.py` (`__version__`). It is shown small on the kiosk home
screen and in the footer of the admin sidebar, and reported by `/api/health` and `/api/auth/status`.
Every change bumps it (patch for fixes and docs, minor for features) and gets a line here.

## 0.7.1
- `pi/install.sh` is now the only file the Pi needs: the camera helper is built in (`--print-helper` shows it). Removed the separate helper, service and udev-rule files.

## 0.7.0
- Admin: Settings → Reset all data (needs the PIN and the word RESET; saves a copy first; keeps the PIN).
- `pi/install.sh`: interactive Pi kiosk installer (server, user, orientation, camera, cursor fix, screen blanking, autostart), with `--uninstall`, `--dry-run` and `--yes`.

## 0.6.0
- Kiosk and docs wording: "Check In/Out" is now "Ammo In/Out".
- Added LICENSE: PolyForm Noncommercial 1.0.0 (derivative works allowed, no commercial use).

## 0.5.4
- Cleanup: removed the cursor-theme and cursor-debug scripts; the udev rule (`pi/99-kiosk-ignore-pointers.rules`) is the one cursor fix.
  The Pi 4's HDMI CEC pseudo-pointers made cage draw a cursor; libinput now ignores them.

## 0.5.0
- Favicon (SVG, ICO and touch icon) on the kiosk, admin and landing pages.

## 0.4.0
- Version number shown on the kiosk home screen and in the admin footer.

## 0.3.0
- Kiosk: mouse pointer always hidden.
- Kiosk: portrait layout; rotate in the OS or load `/kiosk/?rotate=90|270`.

## 0.2.0
- Container listens on port 8580.
- Admin: backup download and restore.
- Box photos: taken on the kiosk for new barcodes (webcam or Pi camera helper); view, replace and remove in admin.

## 0.1.0
- Initial release: kiosk check in/out with queued review, inventory drill-down, admin site, PIN with idle lock and per-IP lockout.
