#!/bin/sh
# Builds an invisible mouse-cursor theme called "blank" for the kiosk user.
# The compositor (cage) draws its own pointer until the browser's page takes over, and a page cannot
# hide that one. With this theme every pointer shape is a transparent pixel.
# Run as the kiosk user:  sh make-blank-cursor.sh      (needs: sudo apt install xcursorgen imagemagick)
set -eu
DIR="$HOME/.icons/blank/cursors"
mkdir -p "$DIR"
cd "$DIR"
convert -size 1x1 xc:none PNG32:blank.png
echo "1 0 0 blank.png" > blank.cfg
xcursorgen blank.cfg blank
rm -f blank.png blank.cfg
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
