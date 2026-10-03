# Quartermaster

Keep track of your ammunition by scanning boxes in and out.

- **Kiosk:** a small touchscreen with a barcode scanner. Tap *Ammo In* or *Ammo Out*, scan boxes, check the list, tap *Finish*. A quick *Inventory* view shows what you have by caliber, bullet weight and product.
- **Admin site:** a website for your computer to name unknown barcodes, manage products and calibers, fix counts, see history, print labels, and make backups.

Both are served by one small server that runs in Docker. The touchscreen just displays a web page from it.

## What you need

| | |
|---|---|
| **Server** | Any computer or NAS that runs Docker |
| **Kiosk computer** | Raspberry Pi 4 (2 GB or more), microSD card (16 GB+), power supply |
| **Screen** | Official Raspberry Pi 7" touchscreen (800x480) |
| **Barcode scanner** | USB 2D barcode scanner that acts as a keyboard (most do). A 2D scanner reads both UPC barcodes and QR codes |
| **Camera (optional)** | A USB webcam or a Raspberry Pi camera, to photograph new boxes |

## 1. Start the server

On the Docker computer:

```bash
git clone <this repo> quartermaster
cd quartermaster
docker compose up -d --build
```

Open `http://<server-address>:8580/admin/` in a browser and choose a 4-digit PIN. The same PIN unlocks the kiosk and the admin site, and both lock after 15 minutes of inactivity. Your data is stored in the `data` folder next to `docker-compose.yml`.

To change the port or time zone, copy `.env.example` to `.env` and edit it.

## 2. Set up the kiosk

Follow [docs/pi-kiosk.md](docs/pi-kiosk.md). In short, you copy one file to the Pi and answer a few questions.

## Using it

**Ammo In / Ammo Out.** Tap one, then scan boxes. Each scan adds one box to the list; scan the same box again to add another, or tap the number to type a quantity. Tap *Review & Finish* to check the list, then *Finish* to save it. Nothing is recorded until you finish.

**Unknown barcodes.** A barcode the system hasn't seen is still recorded, as "Unknown item". When it is new, the kiosk offers to take a photo of the box (if you have a camera). Later, open **Needs details** in the admin site and name it, and everything already scanned picks up the details. The server looks the barcode up online (only the number is sent) and offers to fill in the form for you (and keeps the listing's photo if the box has none yet); set `QM_UPC_LOOKUP=false` in `.env` to turn that off.

**Inventory.** On the kiosk, tap *Inventory* and drill down by caliber, then bullet weight, then product, or scan a box to jump straight to it and see how many are in stock. In the admin site, *Inventory* opens the same drill-down (caliber, then bullet weight, then manufacturer, then the product) with clickable breadcrumbs to jump back; *All items* shows every product in one searchable table. *+ Add stock* adds boxes without scanning, and *Adjust* corrects a count.

**Needs details.** The admin dashboard's *Needs details* box counts barcodes that have no product yet plus products still missing a cost, manufacturer, bullet type or bullet weight (shotshells don't need a weight). Click it, or *Needs details* in the menu, to see them all and fix each one in place.

**Out of stock.** The dashboard's *Out of stock* box lists the calibers you keep (they have a product, or an alert level) but have none of right now. In the admin *Inventory* browse view those calibers carry an OUT tag, like the LOW tag.

**On your phone.** The admin site switches to a phone layout by itself on a mobile browser: a menu button at the top, and every table shown as one card per row, with all the same pages and buttons. *Use desktop layout* in the menu switches back (and *Use mobile layout* does the reverse); the choice is remembered in that browser.

**Low stock.** In the admin site, *Calibers* has an "Alert below (rounds)" box for each caliber, and each product has the same setting. Leave the box blank for no alert. When rounds on hand drop under it, the item shows on the admin dashboard, the kiosk Inventory screen marks it LOW, and the kiosk home screen says how many are running low.

**Cost per round.** Enter the cost per box on a product. Products and Inventory then show the cost per round, and the summaries (dashboard, inventory totals, kiosk calibers and bullet weights) show the low-to-high range for what you have in stock. All prices are shown to the cent. The admin dashboard and inventory also show the value of what you have (boxes times cost per box, for products that have a cost entered), with a totals row at the bottom of *By caliber*.

**Product list in a spreadsheet.** *Products → Export CSV* downloads every product with its barcodes. Edit it (or write your own with at least the columns `caliber` and `rounds_per_box`; costs are US dollars, with or without the $), then *Import CSV*. You see what will be added or changed before anything happens, and a file with any problem rows is refused whole.

**Bullet weight N/A and ammo counted by the round.** For ammo with no traditional bullet weight (shot, slugs, flares), enter *0* (or *N/A*) as the bullet weight on the product form; it is shown as N/A. For ammo you count loose, set *Rounds per box* to 1: it is then shown and counted in rounds everywhere (the kiosk asks "How many rounds?") and is left out of box totals.

**Security.** Everything needs the PIN except the first-run PIN screen, so set the PIN as soon as the server starts and keep the server on your home network (do not port-forward it). By default five wrong PINs lock that device out for a minute, doubling each time; *Settings → Failed sign-in attempts* changes the number of attempts and the length of the first lockout. Changing the PIN signs out every other browser. Backups never contain the PIN. If you put the server behind an HTTPS reverse proxy, set `QM_TRUST_PROXY=true` and `QM_COOKIE_SECURE=true` and make the proxy pass the original `Host` header.

**Clearing the history.** *Settings → History → Clear history…* erases the log of ins, outs and corrections but keeps your counts: each barcode's current number of boxes stays as one "opening balance" entry. Products, barcodes, photos and alert levels are untouched. It can't be undone, so export the history CSV or download a backup first if you want the log.

**Ammo without a barcode.** In the admin site, **Labels** prints your own barcode or QR labels to stick on those boxes. Add as many different products as you like to one sheet (every label for a product carries the same code, so each box scans as that product), pick the number of columns, remove any label you don't want, and print.

**Backup and restore.** *Settings* has a button to download a backup of everything (except the PIN) and one to restore from it. Backups are manual.

**Reset.** *Settings → Reset all data* returns Quartermaster to a fresh install, including the PIN. It keeps no copy, so download a backup first.

**Forgot the PIN?** On the Docker computer:

```bash
docker compose exec quartermaster python -m app.cli reset-pin 1234
```

## Updating

```bash
git pull
docker compose up -d --build
```

Open kiosk and admin pages reload by themselves when the server is updated. The version number is shown on the kiosk home screen and at the bottom of the admin menu. See [CHANGELOG.md](CHANGELOG.md) for what changed.

## License

[PolyForm Noncommercial 1.0.0](LICENSE): free to use and modify for any noncommercial purpose; commercial use is not allowed.
