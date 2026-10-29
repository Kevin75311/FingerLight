"""Is this Pi ready to drive the bar? Run before touching a wire.

    python tools/hw/preflight.py

Checks the machine, not the strip: board model, SPI enabled and reachable,
buffer size, group membership, the Python packages each test script needs, and
that the repo's own data files are where the tests expect. Every line prints what
it measured, and a FAIL tells you the command that fixes it.

Safe to run anywhere -- off a Pi it simply reports which checks do not apply.
"""

from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

rows = []


def check(name, ok, detail, fix=""):
    rows.append((name, ok, detail, fix))
    print("%-26s %-4s %s" % (name, "PASS" if ok else ("n/a" if ok is None else "FAIL"), detail))
    if ok is False and fix:
        print("%-26s      fix: %s" % ("", fix))


def read(path, default=""):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return default


def main():
    model = read("/proc/device-tree/model").strip("\x00").strip()
    on_pi = "Raspberry Pi" in model
    check("board", bool(model) or None, model or "not a Raspberry Pi (that is fine for --dry-run)")

    if on_pi:
        is5 = "Raspberry Pi 5" in model
        check("Pi 5", is5, "yes" if is5 else "this is not a Pi 5 -- the SPI path still works, "
                                            "but rpi_ws281x would too on older boards")

    # --- SPI -------------------------------------------------------------
    if on_pi:
        dev = "/dev/spidev0.0"
        have = os.path.exists(dev)
        check("spidev device", have, dev if have else "%s missing" % dev,
              "sudo raspi-config nonint do_spi 0   (then reboot)")
        cmdline = read("/boot/firmware/cmdline.txt") or read("/boot/cmdline.txt")
        bufsiz = None
        for tok in cmdline.split():
            if tok.startswith("spidev.bufsiz="):
                bufsiz = int(tok.split("=", 1)[1])
        need = 2136                                  # this bar, measured by ws2812_spi
        check("spidev buffer", (bufsiz or 4096) >= need,
              "%d bytes available, this bar needs %d per frame"
              % (bufsiz or 4096, need),
              "add spidev.bufsiz=65536 to /boot/firmware/cmdline.txt (one line, then reboot)")
        groups = os.popen("id -nG").read().split()
        check("spi group", "spi" in groups or os.geteuid() == 0,
              "groups: %s" % " ".join(groups) if groups else "unknown",
              "sudo usermod -aG spi,gpio $USER   (then log out and back in)")
    else:
        check("spidev device", None, "skipped -- not on a Pi")

    # --- python packages --------------------------------------------------
    for mod, why, fix in (
        ("spidev", "LED driver", "sudo apt install python3-spidev"),
        ("mido", "MIDI tests", "sudo apt install python3-rtmidi  (mido comes with it)"),
        ("rtmidi", "MIDI backend", "sudo apt install python3-rtmidi"),
        ("sounddevice", "mic tests",
         "pip3 install --user --break-system-packages sounddevice  (not in Trixie's apt)"),
        ("numpy", "mic analysis + the map", "sudo apt install python3-numpy"),
    ):
        try:
            __import__(mod)
            ok, detail = True, "importable"
        except ImportError as e:
            ok, detail = False, str(e)
        check("python: %s" % mod, ok, detail, fix)

    # --- the repo's own data ---------------------------------------------
    import json

    n_leds = 176
    p = os.path.join(ROOT, "data", "led_map.json")
    try:
        with open(p, "rb") as f:
            m = json.load(f)
        n_leds = int(m["led_count"])
        check("led map", True, "%d LEDs, origin %+.3f mm, worst centre err %.2f mm"
              % (n_leds, m["strip_origin_mm"], m["worst_centre_err_mm"]))
    except Exception as e:
        check("led map", False, "%s: %s" % (p, e), "python tools/gen_map.py 164.8 --origin centre")

    p = os.path.join(ROOT, "firmware", "config", "settings_overlay.json")
    try:
        with open(p, "rb") as f:
            o = json.load(f)
        check("settings overlay", True, "shift %d, %d note_offsets"
              % (o["shift"], len(o["note_offsets"])))
    except Exception as e:
        check("settings overlay", False, str(e))

    # --- the encoder itself ----------------------------------------------
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from ws2812_spi import Strip

        s = Strip(n_leds)
        s.fill((255, 255, 255))
        n = len(s.frame())
        check("frame size", n <= 4096, "%d bytes at full white" % n)
    except Exception as e:
        check("frame size", False, str(e))

    bad = [r for r in rows if r[1] is False]
    print("")
    print("%d checks, %d failed, %d skipped"
          % (len(rows), len(bad), sum(1 for r in rows if r[1] is None)))
    if bad:
        print("Fix those before wiring anything -- every one of them is a cable-free problem.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
