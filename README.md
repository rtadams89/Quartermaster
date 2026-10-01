# Quartermaster

Ammunition inventory with a barcode-scanner kiosk.

- **Kiosk** (`/kiosk/`): a touch UI for a Raspberry Pi with a 7" 800x480 screen and a USB barcode scanner. Tap *Check In* or *Check Out*, scan boxes, review the quantities, tap *Finish*.
- **Admin site** (`/admin/`): the full-featured site for a computer. Identify unknown barcodes, manage products and calibers, correct counts, read the history, print your own labels, export CSV.
- **One server** (this repo) runs in Docker and owns the SQLite database. The Pi only loads a URL.

## Quick start

```bash
git clone <this repo> && cd quartermaster
cp .env.example .env        # optional
docker compose up -d --build
```

Open `http://<docker-host>:8080/admin/` on your computer. On first visit you'll be asked to **choose a 4-digit PIN**. That PIN unlocks both the kiosk and the admin site.

Then point the Pi at `http://<docker-host>:8080/kiosk/` (see [docs/pi-kiosk.md](docs/pi-kiosk.md)).

The database is a single file in `./data/quartermaster.db`. Keep that folder on a volume you trust.

## How it works

### Check-in / check-out (kiosk)

1. Tap **Check In** or **Check Out**.
2. Scan boxes. Each scan adds one box to a **queue**; scanning the same box again adds another. The card shows the last item scanned with `−` / `+` buttons, and you can tap the number to type a quantity.
3. Tap **Review & Finish**. Every queued item is listed with its quantity, all editable (or removable).
4. Tap **Finish**. Only now are the changes written to the inventory.

The queue is kept on the server, so a reboot or an idle lock mid-scan doesn't lose it; you'll get a *Resume* banner. Checking out more than you have on record asks for confirmation instead of blocking you.

### Unknown barcodes

A code the system has never seen is **never rejected**. It is logged against the bare code, shown as "Unknown item", and counted in the inventory under *Unidentified*. Later, on the admin site's **Unidentified** page, pick the code and either attach it to an existing product or create a new one. Everything already logged for that code picks up the details retroactively, because the ledger stores codes, not products.

### Quick inventory (kiosk)

*Inventory* drills down: **caliber → bullet weight → specific product/UPC**, with rounds and boxes at every level. Unidentified boxes appear as their own row, so totals stay honest.

### Labels for ammo with no UPC

On the admin site's **Labels** page, create unique codes (`QM000001`, `QM000002`, …) as **Code 128** bars or **QR**, optionally already attached to a product, and print them. Any 2D-capable USB scanner reads both. Stick one on each box, or on a repacked or reloaded batch.

### Barcode normalisation

UPC-A (12 digits) and the same product's EAN-13 (leading `0` added) are treated as the same code, because different scanners emit one or the other. Your own labels are case-insensitive.

## Security model

- One PIN, stored as a salted scrypt hash. Changeable on the admin site (*Settings*).
- Sessions lock after **15 idle minutes** (`QM_IDLE_MINUTES`), enforced by the server and by an idle timer in each UI.
- Wrong PINs are tracked **per source IP**. After 5 failures an IP is locked out for 60 s, doubling with each further failure up to 1 h. Only that IP is blocked; other devices can still sign in. A correct PIN resets the count. Locked IPs are listed (and can be cleared) under *Settings*.
- A 4-digit PIN is a speed bump, not strong authentication. Keep this on your LAN and don't expose it to the internet.
- If you run a reverse proxy in front, set `QM_TRUST_PROXY=true` so lockouts use the real client IP. Leave it `false` otherwise (the header is trivially forged).

### Locked out or forgot the PIN?

```bash
docker compose exec quartermaster python -m app.cli reset-pin 1234
docker compose exec quartermaster python -m app.cli clear-lockouts
```

## Configuration

All optional; see `.env.example`.

| Variable | Default | Meaning |
| --- | --- | --- |
| `QM_PORT` | `8080` | Host port |
| `QM_IDLE_MINUTES` | `15` | Idle time before the PIN is required again |
| `QM_LOCKOUT_THRESHOLD` | `5` | Failures before an IP is locked |
| `QM_LOCKOUT_BASE_SECONDS` | `60` | First lock duration (doubles per extra failure) |
| `QM_LOCKOUT_MAX_SECONDS` | `3600` | Longest lock |
| `QM_TRUST_PROXY` | `false` | Use `X-Forwarded-For` for the client IP |
| `QM_COOKIE_SECURE` | `false` | Send the session cookie as Secure (HTTPS only) |
| `QM_DB_PATH` | `/data/quartermaster.db` | Database location inside the container |

## Design notes

- **Ledger, not a counter.** Every check-in/out/correction is an immutable row in `transactions`; on-hand is the sum per code. History, audit, and retroactive identification all fall out of that. Mistakes are fixed with a correcting entry, not by editing history.
- **Boxes are the unit.** Quantities are whole boxes; rounds = boxes × the product's rounds-per-box. Partially used boxes aren't tracked.
- **Plain stack:** FastAPI + SQLAlchemy + SQLite on the server; the two UIs are dependency-free ES modules with no build step and no CDN, so they work on a LAN with no internet. There are no schema migrations yet; tables are created on first start. Add Alembic if the schema starts changing.
- **Single worker** by design: SQLite and one user.

## Development

```bash
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
pytest                                            # API tests
QM_DB_PATH=./data/dev.db uvicorn app.main:app --reload --port 8080
```

Layout:

```
app/
  main.py  config.py  db.py  models.py  security.py  services.py  codes.py  seed.py  cli.py
  routers/   auth.py  batches.py  catalog.py  inventory.py  labels.py
  static/    shared/ (api, dom, PIN pad)   kiosk/   admin/
tests/       auth/lockout/idle tests, inventory/batch/drill-down tests
docs/        pi-kiosk.md
```
