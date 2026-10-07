# Setting up the kiosk (Raspberry Pi)

You need the Raspberry Pi with the 7" touchscreen attached, the USB barcode scanner plugged in, and the Quartermaster server already running (see the [README](../README.md)).

## 1. Prepare the Pi

1. On your computer, open **Raspberry Pi Imager** and choose **Raspberry Pi OS Lite (64-bit)**.
2. In the imager's settings, set a hostname (for example `pikiosk`), create a user (for example `kiosk`) with a password, turn on SSH, and enter your Wi-Fi details if you aren't using a network cable.
3. Write the card, put it in the Pi, and power it on.

## 2. Run the installer

From your computer, copy the installer to the Pi and run it:

```bash
scp pi/install.sh kiosk@pikiosk.local:~/
ssh kiosk@pikiosk.local
sudo bash install.sh
```

It asks a few questions. Press Enter to accept the suggested answer in brackets.

| Question | What to pick |
|---|---|
| **Server address** | Where Quartermaster runs, for example `http://192.168.1.50:8580` |
| **Linux user** | The user you created (for example `kiosk`) |
| **Rotation** | 0° for normal landscape; 90° or 270° if the screen is mounted upright (try the other if it's upside-down); 180° if mounted upside-down |
| **Camera** | None, a USB webcam, or a Raspberry Pi camera (Camera Module 3 and other autofocus cameras focus automatically; older fixed-focus modules need the box held at their set distance) |
| **Hide the mouse arrow** | Yes |
| **Screen off after** | Never, or after 15 minutes, 1 hour, or 4 hours without use. A touch wakes it |
| **Start at boot** | Yes |

At the end, let it reboot. The Pi will start straight into Quartermaster and ask for your PIN.

To change an answer later, run `sudo bash install.sh` again. To remove everything it set up, run `sudo bash install.sh --uninstall`.

## Using the scanner

Plug the scanner into any USB port. On the *Ammo In* or *Ammo Out* screen, just scan; no need to tap anything first. If scans don't register, check that the scanner is in keyboard mode (the usual default), adds an Enter after each scan, and uses a US keyboard layout.

## If the screen stays black

Connect with `ssh` and run `journalctl -u quartermaster-kiosk -b` to see what went wrong. The most common causes are a wrong server address or a server that isn't running.
