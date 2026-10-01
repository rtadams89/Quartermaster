#!/bin/sh
# Builds an invisible mouse-cursor theme called "blank" for the kiosk user.
# The compositor (cage) draws its own pointer until the browser's page takes over, and a page cannot
# hide that one. With this theme every pointer shape is a fully transparent image.
# Run as the kiosk user:  sh make-blank-cursor.sh      (needs only python3, which Pi OS ships with)
set -eu
DIR="$HOME/.icons/blank/cursors"
mkdir -p "$DIR"
python3 - "$DIR/blank" <<'PY'
import struct, sys
# Xcursor file: header, table of contents, then one transparent ARGB image per nominal size.
sizes = (24, 32, 48)
chunks = []
for s in sizes:
    pixels = bytes(4 * s * s)  # all zero = fully transparent
    chunks.append(struct.pack("<9I", 36, 0xFFFD0002, s, 1, s, s, 0, 0, 50) + pixels)
header_len = 16 + 12 * len(sizes)
out = struct.pack("<4s3I", b"Xcur", 16, 0x10000, len(sizes))
pos = header_len
for s, c in zip(sizes, chunks):
    out += struct.pack("<3I", 0xFFFD0002, s, pos)
    pos += len(c)
with open(sys.argv[1], "wb") as f:
    f.write(out + b"".join(chunks))
PY
cd "$DIR"
# Every cursor name the compositor or browser may ask for resolves to the same empty image.
for n in default left_ptr arrow top_left_arrow pointer hand hand1 hand2 text xterm ibeam watch wait \
         progress crosshair move grab grabbing fleur help question_arrow not-allowed X_cursor; do
  ln -sf blank "$n"
done
cat > "$HOME/.icons/blank/index.theme" <<'THEME'
[Icon Theme]
Name=blank
Comment=Invisible cursor
THEME
echo "Created $HOME/.icons/blank. Set XCURSOR_THEME=blank in the kiosk service (see docs/pi-kiosk.md)."
