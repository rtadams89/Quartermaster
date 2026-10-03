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

**Unknown barcodes.** A barcode the system hasn't seen is still recorded, as "Unknown item". When it is new, the kiosk offers to take a photo of the box (if you have a camera). Later, open **Unidentified** in the admin site and name it, and everything already scanned picks up the details. The server looks the barcode up online (only the number is sent) and offers to fill in the form for you (and keeps the listing's photo if the box has none yet); set `QM_UPC_LOOKUP=false` in `.env` to turn that off.

**Inventory.** On the kiosk, tap *Inventory* and drill down by caliber, then bullet weight, then product, or scan a box to jump straight to it and see how many are in stock. In the admin site, *Inventory → + Add stock* adds boxes without scanning, and *Adjust* corrects a count.

**Low stock.** In the admin site, *Calibers* has an "Alert below (rounds)" box for each caliber, and each product has the same setting. When rounds on hand drop under it, the item shows on the admin dashboard, the kiosk Inventory screen marks it LOW, and the kiosk home screen says how many are running low.

**Product list in a spreadsheet.** *Products → Export CSV* downloads every product with its barcodes. Edit it (or write your own with at least the columns `caliber` and `rounds_per_box`), then *Import CSV*. You see what will be added or changed before anything happens, and a file with any problem rows is refused whole.

**Ammo without a barcode.** In the admin site, **Labels** prints your own barcode or QR labels to stick on those boxes.

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
