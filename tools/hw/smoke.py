"""The build-day gate: one run that proves Pi -> strip and piano -> Pi -> strip.

    sudo systemctl stop fingerlight          # the app holds SPI and the MIDI port
    python tools/hw/smoke.py                 # all three steps, about five minutes
    python tools/hw/smoke.py --skip-leds     # MIDI only (no strip attached yet)
    python tools/hw/smoke.py --skip-midi     # strip only (no piano nearby)
    sudo systemctl start fingerlight         # afterwards

1. **Preflight** -- `preflight.py`'s machine checks (SPI, buffer, groups, packages).
2. **Strip** -- three static patterns, each followed by a yes/no question: is what you
   see what the stage says you should see.
3. **Keys** -- play every key from A0 to C8, in any order.  Each key lights its own LED
   pair through the real note map (plus any saved calibration offsets), so a dead key,
   a wrong mapping or a stuck note shows up the moment it happens.  It ends when all 88
   have been heard, or on Ctrl-C.  Then it reports which keys never arrived, which
   never released, the velocity range, whether the sustain pedal was seen, and how
   long the Pi took from receiving a note to the strip having it.

The result is written to `~/.fingerlight/smoke-report.json` and printed as a block to
paste into `log.md`.  Exit status 0 only if every step that ran passed.

The latency figure is **Pi-side only**: from mido handing the message over to the SPI
write returning.  It does not include the piano's own scan or USB, which no script on
the Pi can see -- that part is judged by eye (does the light feel instant?).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "firmware", "src"))

from fingerlight.notemap import MIDI_MAX, MIDI_MIN, StripSettings, note_to_leds  # noqa: E402

NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
HOME = os.path.join(os.path.expanduser("~"), ".fingerlight")
KEY_RGB = (255, 176, 96)                # free play's default warm amber


def note_name(n: int) -> str:
    return "%s%d" % (NAMES[n % 12], n // 12 - 1)


def load_strip(home: str = HOME) -> StripSettings:
    """The generated map plus the calibration screen's saved offsets, if any."""
    s = StripSettings.load()
    try:
        with open(os.path.join(home, "led_offsets.json"), encoding="utf-8") as fh:
            raw = json.load(fh)
        return s.with_offsets({int(k): int(v) for k, v in raw.get("offsets", {}).items()})
    except (OSError, ValueError, AttributeError):
        return s


# ----------------------------------------------------------------- the pure part


class KeyCoverage:
    """What the keyboard sent: which keys, how hard, released or not, how fast handled."""

    def __init__(self) -> None:
        self.on_count: dict[int, int] = {}
        self.held: set[int] = set()
        self.velocities: list[int] = []
        self.pedal_seen = False
        self.out_of_range: set[int] = set()
        self.latency_ms: list[float] = []

    def note(self, pitch: int, on: bool, velocity: int = 0) -> bool:
        """Record one event.  Returns True when it is a key heard for the first time."""
        if not MIDI_MIN <= pitch <= MIDI_MAX:
            self.out_of_range.add(pitch)
            return False
        if not on:
            self.held.discard(pitch)
            return False
        first = pitch not in self.on_count
        self.on_count[pitch] = self.on_count.get(pitch, 0) + 1
        self.held.add(pitch)
        self.velocities.append(velocity)
        return first

    @property
    def heard(self) -> int:
        return len(self.on_count)

    @property
    def complete(self) -> bool:
        return self.heard == MIDI_MAX - MIDI_MIN + 1

    def missing(self) -> list[int]:
        return [p for p in range(MIDI_MIN, MIDI_MAX + 1) if p not in self.on_count]

    def report(self) -> dict:
        lat = sorted(self.latency_ms)
        return {
            "keys_heard": self.heard,
            "keys_missing": [note_name(p) for p in self.missing()],
            "stuck": [note_name(p) for p in sorted(self.held)],
            "out_of_range": sorted(self.out_of_range),
            "velocity_min": min(self.velocities) if self.velocities else None,
            "velocity_max": max(self.velocities) if self.velocities else None,
            "pedal_seen": self.pedal_seen,
            "latency_ms_median": round(statistics.median(lat), 2) if lat else None,
            "latency_ms_p95": round(lat[int(0.95 * (len(lat) - 1))], 2) if lat else None,
            "latency_ms_max": round(lat[-1], 2) if lat else None,
            "pass": self.complete and not self.held and not self.out_of_range,
        }


def group_missing(pitches: list[int]) -> str:
    """[21, 22, 23, 60] -> "A0..B0, C4": runs read faster than 88 names."""
    out, i = [], 0
    while i < len(pitches):
        j = i
        while j + 1 < len(pitches) and pitches[j + 1] == pitches[j] + 1:
            j += 1
        a, b = note_name(pitches[i]), note_name(pitches[j])
        out.append(a if i == j else "%s..%s" % (a, b))
        i = j + 1
    return ", ".join(out)


# ----------------------------------------------------------------------- steps


def ask(question: str, auto: str | None) -> bool:
    if auto is not None:
        print("  %s %s (--yes)" % (question, auto))
        return auto == "y"
    while True:
        a = input("  %s [y/n] " % question).strip().lower()
        if a in ("y", "n"):
            return a == "y"


def step_preflight() -> dict:
    print("\n== 1/3 preflight ==")
    import preflight
    code = preflight.main()
    return {"pass": code == 0}


def open_strip(args):
    from ws2812_spi import Strip

    s = Strip(args.n, order=args.order, brightness=args.brightness)
    return s.open(dry=args.dry_run)


def step_strip(args, strip) -> dict:
    print("\n== 2/3 strip ==")
    n = args.n
    checks = []

    def show(pixels, question):
        strip.clear()
        for i, rgb in pixels.items():
            strip[i] = rgb
        strip.show()
        checks.append({"what": question, "ok": ask(question, args.yes)})

    show({0: (80, 0, 0), n // 2: (80, 0, 0), n - 1: (80, 0, 0)},
         "Three red LEDs: the first, the middle (#%d) and the very last?" % (n // 2))
    third = n // 3
    px = {i: (255, 0, 0) for i in range(third)}
    px.update({i: (0, 255, 0) for i in range(third, 2 * third)})
    px.update({i: (0, 0, 255) for i in range(2 * third, n)})
    show(px, "Red, then green, then blue, left to right (red not green = wrong --order)?")
    s = load_strip(args.home)
    px = {}
    for p in (21, 60, 108):
        for i in note_to_leds(p, s):
            px[i] = KEY_RGB
    show(px, "Amber pairs behind A0, middle C (C4) and C8?")
    strip.clear()
    strip.show()
    return {"checks": checks, "pass": all(c["ok"] for c in checks)}


def step_keys(args, strip) -> dict:
    print("\n== 3/3 keys ==")
    try:
        import mido
    except ImportError:
        print("  mido is missing: sudo apt install python3-rtmidi")
        return {"pass": False, "error": "mido missing"}
    from fingerlight.midi import pick_input_port

    port_name = pick_input_port(mido.get_input_names())
    if port_name is None:
        print("  no MIDI input -- is the piano on and plugged into a Pi USB-A port?")
        return {"pass": False, "error": "no MIDI port"}
    s = load_strip(args.home)
    cov = KeyCoverage()
    print("  listening on %s" % port_name)
    print("  Play every key A0..C8, any order. Each one lights as you play it.")
    print("  Press the sustain pedal once too. Ctrl-C ends early.\n")
    deadline = time.monotonic() + args.timeout
    try:
        with mido.open_input(port_name) as port:
            while not cov.complete and time.monotonic() < deadline:
                got = False
                for msg in port.iter_pending():
                    got = True
                    t0 = time.perf_counter()
                    if msg.type == "control_change" and msg.control == 64:
                        cov.pedal_seen = True
                        continue
                    if msg.type not in ("note_on", "note_off"):
                        continue
                    on = msg.type == "note_on" and msg.velocity > 0
                    first = cov.note(msg.note, on, msg.velocity)
                    if strip is not None and MIDI_MIN <= msg.note <= MIDI_MAX:
                        for i in note_to_leds(msg.note, s):
                            strip[i] = KEY_RGB if on else (0, 0, 0)
                        strip.show()
                        cov.latency_ms.append((time.perf_counter() - t0) * 1000.0)
                    if first:
                        print("  %3d/88  %-4s vel %3d" % (cov.heard, note_name(msg.note),
                                                       msg.velocity), flush=True)
                if not got:
                    time.sleep(0.001)
            # give the last key time to come up before calling anything stuck
            end = time.monotonic() + 1.5
            while cov.held and time.monotonic() < end:
                for msg in port.iter_pending():
                    if msg.type in ("note_on", "note_off"):
                        cov.note(msg.note, msg.type == "note_on" and msg.velocity > 0,
                                 msg.velocity)
                time.sleep(0.005)
    except KeyboardInterrupt:
        print("\n  stopped early.")
    rep = cov.report()
    rep["port"] = port_name
    if cov.missing():
        print("  never heard: %s" % group_missing(cov.missing()))
    if rep["stuck"]:
        print("  never released: %s" % ", ".join(rep["stuck"]))
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--skip-preflight", action="store_true")
    ap.add_argument("--skip-leds", action="store_true", help="no strip attached")
    ap.add_argument("--skip-midi", action="store_true", help="no piano attached")
    ap.add_argument("-n", type=int, default=StripSettings.load().led_count)
    ap.add_argument("--order", default="GRB")
    ap.add_argument("--brightness", type=float, default=0.15)
    ap.add_argument("--timeout", type=float, default=300.0, help="seconds for the key step")
    ap.add_argument("--home", default=HOME, help="where led_offsets.json and the report live")
    ap.add_argument("--dry-run", action="store_true", help="no SPI writes")
    ap.add_argument("--yes", choices=("y", "n"), default=None,
                    help="answer every strip question (for unattended runs)")
    args = ap.parse_args(argv)

    report = {"at": _dt.datetime.now().replace(microsecond=0).isoformat(), "steps": {}}
    if not args.skip_preflight:
        report["steps"]["preflight"] = step_preflight()
    strip = None
    if not args.skip_leds:
        try:
            strip = open_strip(args)
        except Exception as exc:
            print("\n  cannot open the strip: %s" % exc)
            report["steps"]["strip"] = {"pass": False, "error": str(exc)}
    try:
        if strip is not None:
            report["steps"]["strip"] = step_strip(args, strip)
        if not args.skip_midi:
            report["steps"]["keys"] = step_keys(args, strip)
    finally:
        if strip is not None:
            strip.clear()
            strip.show()
            strip.close()

    ok = all(s.get("pass") for s in report["steps"].values())
    report["pass"] = ok
    os.makedirs(args.home, exist_ok=True)
    path = os.path.join(args.home, "smoke-report.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1)

    print("\n---- for log.md " + "-" * 50)
    for name, st in report["steps"].items():
        line = "%-9s %s" % (name, "PASS" if st.get("pass") else "FAIL")
        if name == "keys" and "keys_heard" in st:
            line += ("  %d/88 keys, stuck %d, velocity %s..%s, pedal %s, "
                     "Pi-side latency median %s ms / max %s ms"
                     % (st["keys_heard"], len(st["stuck"]), st["velocity_min"],
                        st["velocity_max"], "seen" if st["pedal_seen"] else "NOT seen",
                        st["latency_ms_median"], st["latency_ms_max"]))
        elif "error" in st:
            line += "  " + st["error"]
        elif name == "strip":
            line += "  %d/%d patterns as expected" % (
                sum(c["ok"] for c in st["checks"]), len(st["checks"]))
        print(line)
    print("-" * 66)
    print("report: %s" % path)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
