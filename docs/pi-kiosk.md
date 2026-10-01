# Raspberry Pi kiosk setup

The Pi runs no Quartermaster code. It is a full-screen browser pointed at the server:

```
http://<server>:8580/kiosk/
```

The UI is laid out for the official 7" 800x480 touchscreen and everything is finger-sized.

> **Status:** these steps follow the standard Raspberry Pi OS kiosk recipe, but they have not been run on your Pi yet.
> Treat the first boot as a shakedown and adjust package or user names to match your install.

## Quick install (recommended)

After flashing Raspberry Pi OS Lite (64-bit, Bookworm or newer) and getting the Pi onto your network (see step 1 below for the imager settings), copy the single file `pi/install.sh` to it and run it (everything else is built into that one file):

```bash
scp pi/install.sh kiosk@pikiosk.local:~/     # from your computer
ssh kiosk@pikiosk.local
sudo bash install.sh
```

It asks a handful of questions, each with a sensible default (just press Enter):

1. The **server address** (it checks it can reach the server).
2. Which Linux **user** runs the kiosk (and creates it if needed).
3. The **rotation** of the picture: 0°, 90°, 180° or 270° (done by the page, via `?rotate=`).
4. Which **camera** takes box photos: none, a USB webcam, or a Pi camera module (which also installs the helper service).
5. Whether to apply the **mouse-arrow fix**, and how long before the **screen blanks**: never, or after 15 minutes, 1 hour or 4 hours of inactivity.
6. Whether to **start at boot**.

It then shows a summary, installs the packages, writes the files described in the rest of this page, and offers to reboot. Your answers are saved in `/etc/quartermaster-kiosk.conf`, so to change one later, run the installer again. `sudo bash install.sh --uninstall` removes everything it created, and `bash install.sh --dry-run` shows what it would do without changing anything.

> **Status:** the installer's menus, generated files and launcher were exercised here against a scratch directory with stand-ins for `apt`, `systemctl`, Chromium and `wlr-randr`. It has not yet run on a real Pi, so check `journalctl -u quartermaster-kiosk -b` if the screen stays black, and tell me what you see.

The sections below describe the same setup done by hand, and what each piece is for.

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
  http://SERVER:8580/kiosk/
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

**Alternative:** if you would rather use Raspberry Pi OS *with desktop*, skip all of the above, set Chromium to launch with `--kiosk http://SERVER:8580/kiosk/` from the desktop autostart, and turn off screen blanking in `raspi-config`.

## 3. The USB barcode scanner

Plug it in and it should just work: it presents itself as a USB keyboard, "types" the barcode, and presses Enter. The kiosk listens for that burst of fast keystrokes on the scan screen, so no text field needs focus.

- Use a **2D imager** in **USB HID / keyboard-emulation** mode (the usual factory default) so it reads both UPCs and the QR codes you print yourself.
- The scanner must end each scan with **Enter** (CR). That is the usual default. If not, scan the "add Enter suffix" setting from the scanner's manual.
- Don't enable a prefix, and keep the keyboard layout on **US**. A different layout can turn digits into symbols.
- Quick test: SSH is not needed. Open the kiosk, tap *Ammo In*, scan a box. Its code should appear on screen.

Scans only count while the scan screen is showing. Scanning on the home or review screens is ignored on purpose.

## 4. Box photos (camera)

The first time the kiosk sees a barcode it has never seen before, it offers to photograph the box. What you need on the Pi depends on the camera.

If no camera is found, the kiosk skips the step (and says so once). You can also turn the prompt off under *Settings* on the admin site.

### USB webcam: live preview, nothing to install

Chromium opens USB webcams itself. These flags are needed on the Chromium line in the service file above, because browsers only allow camera access on HTTPS or `localhost`, and the kiosk page is plain HTTP on your LAN:

```
  --use-fake-ui-for-media-stream \
  --unsafely-treat-insecure-origin-as-secure=http://SERVER:8580 \
  --user-data-dir=/home/kiosk/.config/qm-kiosk \
```

The first grants camera permission automatically (there is no one to click a prompt on a kiosk); the second marks only your Quartermaster server as trusted for this browser. Make sure the kiosk user is in the `video` group (`sudo usermod -aG video kiosk`).

### Raspberry Pi camera module (ribbon cable): use the helper

Chromium cannot open CSI camera modules, so a tiny helper takes the picture instead. It listens on `127.0.0.1` only, and the kiosk page calls it automatically when it is running. There is no live preview in this mode: hold the box up, tap *Take photo*, and retake if it isn't good.

The installer sets all of this up when you choose the Pi camera module. To do it by hand:

1. Check the camera works first: `rpicam-still -t 1000 -o test.jpg` (it ships with Raspberry Pi OS; on a minimal install `sudo apt install rpicam-apps-lite`).
2. Install the helper (its source is built into `install.sh`):

   ```bash
   sudo mkdir -p /opt/quartermaster
   bash install.sh --print-helper | sudo tee /opt/quartermaster/camera_helper.py >/dev/null
   sudo tee /etc/systemd/system/quartermaster-camera.service >/dev/null <<'EOF'
   [Unit]
   Description=Quartermaster camera helper
   After=local-fs.target

   [Service]
   User=kiosk
   SupplementaryGroups=video
   Environment=QM_CAMERA_ORIGIN=http://SERVER:8580
   ExecStart=/usr/bin/python3 /opt/quartermaster/camera_helper.py
   Restart=always
   RestartSec=3

   [Install]
   WantedBy=multi-user.target
   EOF
   sudo systemctl daemon-reload && sudo systemctl enable --now quartermaster-camera
   curl http://127.0.0.1:8581/health                            # should print {"ok": true, "camera": true}
   ```

   `QM_CAMERA_ORIGIN` must be exactly the address the kiosk loads (for example `http://192.168.1.50:8580`); it stops any other website in that browser from taking pictures.

If both a helper and a webcam are present, the helper is used.

> **Status:** the kiosk's camera screen was tested here with a simulated webcam, and the helper with a stand-in for `rpicam-still`. Neither has run on real camera hardware yet, so expect to adjust on first try.

## 5. Screen and touch

- **Screen blanking:** the installer sets how long the screen stays on (never, 15 minutes, 1 hour or 4 hours of inactivity) using `swayidle` and `wlr-randr` inside the kiosk launcher. A touch wakes it. So that the wake-up touch doesn't also press a button, the launcher adds `blank=<seconds>` to the kiosk URL: the page goes black about 10 seconds before the screen turns off, and swallows the first touch (or the first scan) that wakes it. The screen blanking itself relies on `cage` supporting idle notification, which I have not confirmed on cage 0.3.1: if the screen never turns off, check `journalctl -u quartermaster-kiosk -b` for `swayidle` errors (the black page still works on its own).
- **Rotation:** see the next section.
- **Touch offset:** panels that connect over DSI report correct coordinates automatically. USB touch panels occasionally need a libinput calibration matrix; search for your panel's model plus "libinput calibration".

### Mouse pointer

The kiosk page hides the pointer over its own content (`cursor: none`), but the arrow you see in the middle of the screen at boot is drawn by `cage`, not the page. `cage` draws a pointer whenever *any* input device claims to be a pointer, and the Pi 4's two HDMI ports each register a CEC device (`vc4-hdmi-0`, `vc4-hdmi-1`) that does ([cage issue #299](https://github.com/cage-kiosk/cage/issues/299)). The fix is to have libinput ignore those two devices:

```bash
sudo tee /etc/udev/rules.d/99-kiosk-ignore-pointers.rules >/dev/null <<'EOF'
SUBSYSTEM=="input", ATTRS{name}=="vc4-hdmi-0", ENV{LIBINPUT_IGNORE_DEVICE}="1"
SUBSYSTEM=="input", ATTRS{name}=="vc4-hdmi-1", ENV{LIBINPUT_IGNORE_DEVICE}="1"
EOF
sudo udevadm control --reload-rules && sudo udevadm trigger
sudo reboot
```

The rule file is two lines and does not touch the touchscreen or the barcode scanner:

```
SUBSYSTEM=="input", ATTRS{name}=="vc4-hdmi-0", ENV{LIBINPUT_IGNORE_DEVICE}="1"
SUBSYSTEM=="input", ATTRS{name}=="vc4-hdmi-1", ENV{LIBINPUT_IGNORE_DEVICE}="1"
```

To check, `sudo apt install libinput-tools` and run `sudo libinput list-devices`: the `vc4-hdmi` entries should be gone, and nothing but a mouse you plugged in yourself should list `pointer` under *Capabilities*. If a cursor ever returns, look for another device with `pointer` in that list (some USB scanners and touch panels register a second "mouse" interface) and add a matching rule with its exact name.

## 6. Rotation (portrait or upside-down mounting)

The kiosk page can turn itself by 90°, 180° or 270°, so the display can be mounted any way up. The installer asks for this and puts `?rotate=` on the URL; to do it by hand, load:

```
http://SERVER:8580/kiosk/?rotate=90     (UI turned clockwise)
http://SERVER:8580/kiosk/?rotate=180    (upside-down)
http://SERVER:8580/kiosk/?rotate=270    (UI turned counter-clockwise)
```

At 90° and 270° the screen is portrait, and the kiosk switches to its portrait layout (480x800) automatically. The page does the rotating, so no operating-system display setting is involved, and touch, the scanner and the camera preview all follow it. If the picture comes out the wrong way up, use the opposite angle (90 and 270 are opposites).

> **Status:** each rotation was checked here in a headless browser (nothing clipped, taps land on the right buttons). Not yet tried on a real Pi.

## 7. Locking

The kiosk locks itself after 15 minutes without a touch or scan (configurable with `QM_IDLE_MINUTES` on the server) and asks for the PIN again. Anything you had scanned but not finished stays queued on the server, so after unlocking you'll see a "Resume" banner.
