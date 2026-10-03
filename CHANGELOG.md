# Changelog

All notable changes to Quartermaster, newest first.

## 0.22.3
- Admin: Add stock now picks the product with the same type-ahead search as Labels (type part of the caliber, manufacturer or product, then pick). The barcode choice appears once a product with several codes is chosen. Identify code's "Existing product" choice uses the same search.

## 0.22.2
- Kiosk: the per-item box quantity can now be up to 99,999 (it was capped at 999). The keypad accepts five digits. The admin Add stock and Adjust limits were raised to match.

## 0.22.1
- Labels: the number of labels now starts at 1 and the sheet starts with 2 columns.

## 0.22.0
- Cost per round: you still enter the cost per box, and the Products and Inventory pages now show the cost per round for each product (cost per box divided by rounds per box).
- Summary views show the low-to-high cost per round of everything in stock in that group: the admin Dashboard "By caliber" table, the admin Inventory footer (respecting the caliber filter and search), and the kiosk Inventory screens at caliber, bullet-weight and product level. Products with no cost entered, and products with no stock, are left out.

## 0.21.0
- Labels: build one print sheet from many different labels. Each "Add to sheet" adds that product's labels (or new unassigned codes, or an existing code) to the sheet, and the sheet keeps filling until you clear it, even if you visit other pages and come back.
- Labels: choose 1 to 6 columns for the sheet, and remove any single label with the × on it before printing. Clear sheet starts over. The label style (bar or QR) applies to the whole sheet.

## 0.20.0
- Labels: the product box is now a type-ahead search (same as Caliber and Manufacturer). Leave it empty to make labels for codes you will identify later.
- Labels: a product keeps one label code. Choosing a product that already has a QM label reprints that same code instead of making a new one; a product with no label yet gets one new code. The number is now how many labels to print. With no product chosen, each label still gets its own new code.

## 0.19.0
- "Brand" is now called "Manufacturer" everywhere you see it: the product form, the product search box and the CSV columns. CSV files now use a `manufacturer` column in place of `brand`, both in exports and in imports.

## 0.18.0
- Admin product form: Brand now suggests brands you have used before as you type (most-used first). New brands can still be typed freely.
- A brand typed in a different case ("federal") is saved with the spelling already in use ("Federal"), including on CSV import, so the same brand never ends up spelled two ways.
- The Caliber list no longer pops open by itself when the product form opens; it opens when you click, type or press an arrow key.

## 0.17.0
- Admin product form (including Identify): Caliber is now a type-ahead box. Typing filters the list ("45" finds .45 ACP, "5.56" finds .223 Rem / 5.56 NATO); pick with the mouse, or the arrow keys and Enter.
- Cost per box is labelled as US dollars and tidied to $0.00 when you leave the box. Typing the $ or commas is optional. In CSV files costs are written as 18.50 and may be read with or without the $.
- Alert when below: the form now says that leaving it blank means no alert.

## 0.16.0
- Low-stock alerts: set an "Alert below (rounds)" level on a caliber (Calibers page) or a product (product form). Items under their level show on the admin dashboard and Inventory page, and on the kiosk (a LOW tag on Inventory, and a count on the home screen). A caliber with a level stays listed on the kiosk even when it has run out.
- Products: Export CSV and Import CSV. Import shows what will be added, updated or left alone before doing it, creates missing calibers, and refuses the whole file if any row has a problem. Importing a barcode that was scanned but never described identifies it.
- Backups made before this version cannot be restored (they lack the new alert-levels table).

## 0.15.0
- Kiosk: on the Inventory screen, scanning a box jumps straight to that item (caliber, weight, then the item highlighted) and shows how many are in stock. Boxes with none in stock or not in the system get a short message.

## 0.14.0
- Removed code kept only for older versions: the still-photo camera mode and `/snapshot.jpg`, the installer's handling of settings saved by earlier installers, and restoring backups that lack newer tables (such a backup is now rejected as incompatible). Re-run the installer on the Pi to update the camera helper.

## 0.13.0
- Kiosk: the Pi camera module now shows a live preview on the box-photo screen (USB webcams already did). The helper built into `pi/install.sh` streams frames from `rpicam-vid` only while the photo screen is open; the photo is the frame on screen. Re-run the installer on the Pi to update the helper.
- Kiosk: the Skip / Take photo / Retake / Use photo buttons are now at the top of the photo screen.

## 0.12.0
- Admin: when you identify a code, the online listing's photo is saved as the box photo if the code has none and the image looks like a real product photo (not tiny, oddly shaped or blank). A photo you took or uploaded is never replaced.

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
