#!/usr/bin/env bash
# Raspberry Pi 5 setup for the Finger_Light bar (D-27, D-28).
#
#   bash tools/hw/setup_pi.sh            # show what it would do
#   bash tools/hw/setup_pi.sh --apply    # actually do it
#
# Idempotent, and prints every change. Nothing here needs the strip connected.
# Target: Raspberry Pi OS (64-bit) on a Pi 5 -- Trixie (Debian 13, the current release
# since 2025-10) or Bookworm.
#
# Why apt and not pip: Bookworm marks the system Python as externally managed
# (PEP 668), so `pip install` into it fails by design. Every package below has a
# Debian build, which also gets you working ALSA/PortAudio wiring for free. If
# you later want a venv for app development, create it with
# `python3 -m venv --system-site-packages .venv` so these stay visible.

set -euo pipefail

APPLY=0
[[ "${1:-}" == "--apply" ]] && APPLY=1

say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
do_()  { if [[ $APPLY == 1 ]]; then echo "+ $*"; eval "$@"; else echo "  would run: $*"; fi; }

say "0. Where we are"
uname -srm
cat /proc/device-tree/model 2>/dev/null | tr -d '\0'; echo
grep -q "Raspberry Pi 5" /proc/device-tree/model 2>/dev/null \
  || echo "  NOTE: not a Pi 5 -- the SPI LED path still works, the rest of this is generic."

say "1. Packages"
# python3-spidev      : the LED driver (tools/hw/ws2812_spi.py)
# python3-rtmidi      : MIDI backend; pulls mido's dependency chain
# python3-mido        : MIDI message parsing
# python3-numpy       : mic analysis, and the note map tooling
# libportaudio2, python3-cffi : what the sounddevice wheel needs underneath (1b)
# chromium-browser    : the app runs in kiosk mode (D-30). On Trixie this name is a
#                       transitional package that pulls in `chromium`; on Bookworm it
#                       is the browser itself -- so one name works on both.
do_ "sudo apt-get update"
do_ "sudo apt-get install -y python3-spidev python3-rtmidi python3-mido \
     python3-numpy python3-cffi python3-pip libportaudio2 git chromium-browser alsa-utils"

say "1b. sounddevice (USB mic) -- not packaged for Trixie"
# Debian 13 has no python3-sounddevice (only forky/sid do), and naming a package apt
# cannot find fails the WHOLE install above -- which is why it is not in that list.
# It is a small pure-Python wheel over libportaudio2, so a --user pip install is the
# least invasive fix; --break-system-packages is the PEP 668 opt-in and only writes
# to ~/.local, never /usr.
if python3 -c "import sounddevice" 2>/dev/null; then
  echo "  sounddevice already importable"
else
  do_ "pip3 install --user --break-system-packages sounddevice"
fi

say "2. Enable SPI (LED data on SPI0 MOSI = GPIO10, physical pin 19)"
if [[ -e /dev/spidev0.0 ]]; then
  echo "  /dev/spidev0.0 already present"
else
  do_ "sudo raspi-config nonint do_spi 0"
  echo "  (this edits /boot/firmware/config.txt and needs a reboot)"
fi

say "3. Raise the spidev buffer"
# This bar sends 2136 bytes per frame, inside the stock 4096 limit -- but a
# longer strip, or a library that spells a bit with 8 SPI bits instead of 4,
# silently needs more. Raising it now costs nothing and removes a class of
# mysterious failure later.
CMDLINE=/boot/firmware/cmdline.txt
[[ -f $CMDLINE ]] || CMDLINE=/boot/cmdline.txt
if grep -q "spidev.bufsiz=" "$CMDLINE" 2>/dev/null; then
  echo "  already set: $(grep -o 'spidev.bufsiz=[0-9]*' "$CMDLINE")"
else
  echo "  $CMDLINE is ONE line -- append, never add a newline:"
  do_ "sudo sed -i 's/\$/ spidev.bufsiz=65536/' $CMDLINE"
fi

say "4. Groups (so the LED tests do not need sudo)"
if id -nG | grep -qw spi; then
  echo "  $USER is already in the spi group"
else
  do_ "sudo usermod -aG spi,gpio,audio $USER"
  echo "  log out and back in for this to take effect"
fi

say "5. Check the piano and the mic are visible"
echo "  USB devices:"; lsusb 2>/dev/null | sed 's/^/    /' || echo "    (lsusb not installed)"
echo "  MIDI ports:";  amidi -l 2>/dev/null | sed 's/^/    /' || echo "    (none yet)"
echo "  Capture devices:"; arecord -l 2>/dev/null | sed 's/^/    /' || echo "    (none yet)"

say "Next"
cat <<'TXT'
  1. reboot if SPI or the buffer changed
  2. python3 tools/hw/preflight.py          # must be all PASS before wiring
  3. wire it up, then:
     python3 tools/hw/led_test.py stages
  4. docs/build.md has the full order of operations and what each stage proves.
TXT
