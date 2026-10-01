# Changelog

The version lives in one place, `app/__init__.py` (`__version__`). It is shown small on the kiosk home
screen and in the footer of the admin sidebar, and reported by `/api/health` and `/api/auth/status`.
Every change bumps it (patch for fixes and docs, minor for features) and gets a line here.

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
