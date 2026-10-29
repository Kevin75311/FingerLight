#!/usr/bin/env bash
# Install the Finger_Light app on the Pi: a system service for the server, and a kiosk
# Chromium on the touchscreen at login.
#
#   bash app/deploy/install_app.sh            # show what it would do
#   bash app/deploy/install_app.sh --apply    # do it
#
# Run tools/hw/setup_pi.sh --apply first (packages, SPI, groups).  Idempotent.

set -euo pipefail

APPLY=0
[[ "${1:-}" == "--apply" ]] && APPLY=1
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
USER_NAME="$(id -un)"

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
do_()  { if [[ $APPLY == 1 ]]; then echo "+ $*"; eval "$@"; else echo "  would run: $*"; fi; }

say "0. Where we are"
echo "  repo: $REPO"
echo "  user: $USER_NAME"
[[ -f "$REPO/app/server.py" ]] || { echo "  app/server.py not found under $REPO"; exit 1; }

say "1. App server as a system service (starts at boot, restarts on failure)"
UNIT=/etc/systemd/system/fingerlight.service
RENDERED="$(sed -e "s|@USER@|$USER_NAME|g" -e "s|@REPO@|$REPO|g" "$REPO/app/deploy/fingerlight.service")"
if [[ -f $UNIT ]] && [[ "$(cat "$UNIT")" == "$RENDERED" ]]; then
  echo "  $UNIT is current"
else
  if [[ $APPLY == 1 ]]; then
    printf '%s\n' "$RENDERED" | sudo tee "$UNIT" >/dev/null
    echo "+ wrote $UNIT"
  else
    echo "  would write $UNIT:"
    printf '%s\n' "$RENDERED" | sed 's/^/    | /'
  fi
fi
do_ "sudo systemctl daemon-reload"
do_ "sudo systemctl enable --now fingerlight.service"
do_ "sudo systemctl restart fingerlight.service"

say "2. Kiosk Chromium at desktop login (labwc)"
# A user autostart file may take the place of the system one, which is what starts the
# panel and kanshi (kanshi applies the screen rotation set in Control Centre).  So the
# first time, seed the user file from the system file and then add the kiosk line.
AUTO_DIR="$HOME/.config/labwc"
AUTO="$AUTO_DIR/autostart"
LINE="bash $REPO/app/deploy/kiosk.sh &"
if [[ -f $AUTO ]] && grep -qF "app/deploy/kiosk.sh" "$AUTO"; then
  echo "  $AUTO already starts the kiosk"
else
  do_ "mkdir -p '$AUTO_DIR'"
  if [[ ! -f $AUTO && -f /etc/xdg/labwc/autostart ]]; then
    do_ "cp /etc/xdg/labwc/autostart '$AUTO'"
  fi
  do_ "printf '%s\n' '$LINE' >> '$AUTO'"
fi
do_ "chmod +x '$REPO/app/deploy/kiosk.sh'"

say "3. Scores folder"
do_ "mkdir -p '$HOME/.fingerlight/scores'"

say "Next"
cat <<TXT
  1. Screen blanking off -- from a terminal ON THE PI'S DESKTOP (it edits the labwc
     session it finds running):   sudo raspi-config nonint do_blanking 1
  2. Reboot. The panel should come up in the app, full screen.
  3. From a phone on the same Wi-Fi: http://$(hostname).local:8080/
  4. Logs:     journalctl -u fingerlight -f
     Restart:  sudo systemctl restart fingerlight
     Leave the kiosk: Alt+F4 on a keyboard, or over SSH: pkill -f kiosk.sh; pkill chromium
TXT
