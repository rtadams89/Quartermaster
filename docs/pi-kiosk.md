# Raspberry Pi kiosk setup

The Pi runs no Quartermaster code. It is a full-screen browser pointed at the server:

```
http://<server>:8080/kiosk/
```

The UI is laid out for the official 7" 800x480 touchscreen and everything is finger-sized.

> **Status:** these steps follow the standard Raspberry Pi OS kiosk recipe, but they have not been run on your Pi yet.
> Treat the first boot as a shakedown and adjust package or user names to match your install.

## 1. Image and packages

1. Flash **Raspberry Pi OS Lite (64-bit, Bookworm or newer)** to the Pi 4B with Raspberry Pi Imager. In the imager's settings, set the hostname, create a user (the examples below use `kiosk`), enable SSH, and enter your Wi-Fi if you are not using Ethernet.
2. Boot, SSH in, then:

```bash
sudo apt update
sudo apt install --no-install-recommends cage chromium fonts-dejavu-core fonts-noto-color-emoji
```

`cage` is a tiny single-app Wayland compositor, which is all a kiosk needs. (On older releases the browser package is called `chromium-browser`; use whichever your release provides, and adjust the path in the service file.)

The icons in the UI (🔒 🗑 ⌨ ✓) come from the emoji font, so don't skip that package.

## 2. Autostart the kiosk

Create `/etc/systemd/system/quartermaster-kiosk.service` (replace `SERVER` and, if needed, the user name and UID):

```ini
[Unit]
Description=Quartermaster kiosk
After=systemd-user-sessions.service network-online.target getty@tty1.service
Wants=network-online.target
Conflicts=getty@tty1.service

[Service]
User=kiosk
PAMName=login
TTYPath=/dev/tty1
StandardInput=tty
StandardOutput=journal
UtmpIdentifier=tty1
UtmpMode=user
ExecStart=/usr/bin/cage -s -- /usr/bin/chromium \
  --kiosk --noerrdialogs --disable-infobars --no-first-run \
  --disable-session-crashed-bubble --disable-pinch \
  --overscroll-history-navigation=0 --password-store=basic \
  --check-for-update-interval=31536000 \
  http://SERVER:8080/kiosk/
Restart=always
RestartSec=3

[Install]
WantedBy=graphical.target
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable quartermaster-kiosk.service
sudo systemctl set-default graphical.target
sudo reboot
```

If the screen stays black, check `journalctl -u quartermaster-kiosk -b`. Common causes: wrong user name, wrong browser binary path, or the server URL being unreachable (in which case the kiosk shows a "Cannot reach the Quartermaster server" page with a Retry button).

**Alternative:** if you would rather use Raspberry Pi OS *with desktop*, skip all of the above, set Chromium to launch with `--kiosk http://SERVER:8080/kiosk/` from the desktop autostart, and turn off screen blanking in `raspi-config`.

## 3. The USB barcode scanner

Plug it in and it should just work: it presents itself as a USB keyboard, "types" the barcode, and presses Enter. The kiosk listens for that burst of fast keystrokes on the scan screen, so no text field needs focus.

- Use a **2D imager** in **USB HID / keyboard-emulation** mode (the usual factory default) so it reads both UPCs and the QR codes you print yourself.
- The scanner must end each scan with **Enter** (CR). That is the usual default. If not, scan the "add Enter suffix" setting from the scanner's manual.
- Don't enable a prefix, and keep the keyboard layout on **US**. A different layout can turn digits into symbols.
- Quick test: SSH is not needed. Open the kiosk, tap *Check In*, scan a box. Its code should appear on screen.

Scans only count while the scan screen is showing. Scanning on the home or review screens is ignored on purpose.

## 4. Screen and touch

- **Screen never blanks:** the service above keeps the compositor running; if the display still sleeps, add `consoleblank=0` to the end of the single line in `/boot/firmware/cmdline.txt`.
- **Wrong rotation:** most 7" panels are right-side-up out of the box. If yours isn't, rotate with `wlr-randr` in the service's `ExecStart` (before launching Chromium) or set the rotation in `/boot/firmware/config.txt` for your panel type.
- **Touch offset:** panels that connect over DSI report correct coordinates automatically. USB touch panels occasionally need a libinput calibration matrix; search for your panel's model plus "libinput calibration".

## 5. Locking

The kiosk locks itself after 15 minutes without a touch or scan (configurable with `QM_IDLE_MINUTES` on the server) and asks for the PIN again. Anything you had scanned but not finished stays queued on the server, so after unlocking you'll see a "Resume" banner.
