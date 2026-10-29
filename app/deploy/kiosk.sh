#!/usr/bin/env bash
# Full-screen the app on the touchscreen.  Started from ~/.config/labwc/autostart by
# install_app.sh; safe to run by hand from a desktop terminal too.
#
# Waits for the app server first: at boot the desktop can come up before the service,
# and a kiosk that opened onto "connection refused" stays there.

URL="${1:-http://127.0.0.1:8080/}"

for _ in $(seq 1 90); do
  curl -fs -o /dev/null "$URL" && break
  sleep 1
done

BROWSER="$(command -v chromium || command -v chromium-browser)"
if [[ -z "$BROWSER" ]]; then
  echo "kiosk.sh: no chromium on PATH -- run tools/hw/setup_pi.sh --apply" >&2
  exit 1
fi

# Its own profile, so a crash never brings up a "restore pages?" bubble on the panel.
exec "$BROWSER" "$URL" \
  --kiosk --noerrdialogs --disable-infobars --no-first-run \
  --disable-session-crashed-bubble --disable-features=Translate \
  --overscroll-history-navigation=0 --disable-pinch \
  --user-data-dir="$HOME/.fingerlight/chromium"
