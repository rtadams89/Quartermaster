# Changelog

All notable changes to Quartermaster, newest first.

## 0.11.0
- Admin: Identify code now asks UPCitemdb (free, no key) what the barcode is and offers to pre-fill the new-product form (title, brand, caliber, rounds per box, weight, bullet type). Nothing is saved until you press Save. Answers are cached; only the barcode number is sent. Turn off with `QM_UPC_LOOKUP=false`.

## 0.10.0
- Admin: Inventory → "+ Add stock" adds boxes of a product (with a note) from the admin site, recorded as ammo in.

## 0.9.3
- Removed unused playwright from requirements-dev.txt.

## 0.9.2
- Removed DEVELOPMENT.md.

## 0.9.1
- Documentation rewritten for end users (hardware, server, Pi installer).

## 0.9.0
- Kiosk: with `?blank=N` (set by the installer to match the screen-blank period) the page goes black just before the screen blanks and swallows the first touch or scan that wakes it.
- Admin reset now returns to a fresh install: no safety copy, and the PIN, logins and lockouts are erased too, so the next visit sets a new PIN.

## 0.8.0
- Fixed stale UI after an update: static files now use content-hash ETags (modification time + size could collide), and open pages compare their build id to the server's (`/api/health`) and reload themselves when it changes.
- Kiosk: `?rotate=180`. Installer: rotation is now only the page-level `?rotate=` (0/90/180/270); the OS-level `wlr-randr` option is gone.
- Installer: screen blank timeout is a menu (never, 15 min, 1 hour, 4 hours) using swayidle + wlr-randr; replaces the on/off console-blank option.

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
