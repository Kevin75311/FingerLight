"""Is the piano talking to the Pi, and is anything being dropped?

    python tools/hw/midi_test.py ports          # what the Pi can see
    python tools/hw/midi_test.py watch          # print every event, with gaps
    python tools/hw/midi_test.py latency        # play 20 notes, read the spread

The PX-S1100's USB TO HOST socket is a device port, so the Pi is the host: one
cable into any Pi 5 USB-A port, no adapter, no hub (D-27). If `ports` shows
nothing, the problem is upstream of this script -- check `lsusb` and `amidi -l`,
and that the piano is powered on.
"""

from __future__ import annotations

import argparse
import sys
import time

NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def note_name(n):
    return "%s%d" % (NAMES[n % 12], n // 12 - 1)


def _mido():
    try:
        import mido
    except ImportError:
        raise SystemExit("mido is missing. On the Pi:\n"
                         "  sudo apt install python3-rtmidi\n"
                         "  (or, in a venv: pip install mido python-rtmidi)")
    return mido


def cmd_ports(_args):
    mido = _mido()
    ins = mido.get_input_names()
    print("MIDI inputs (%d):" % len(ins))
    for name in ins:
        print("   %s" % name)
    if not ins:
        print("   none.")
        print("   Check: lsusb | grep -i casio ; amidi -l ; is the piano powered on?")
        return 1
    return 0


def _open(mido, want):
    ins = mido.get_input_names()
    if not ins:
        raise SystemExit("no MIDI inputs -- run `ports` first")
    if want:
        for name in ins:
            if want.lower() in name.lower():
                return mido.open_input(name), name
        raise SystemExit("no input matching %r in %s" % (want, ins))
    # ALSA's "Midi Through" loopback is always listed first on a Pi and never carries
    # the piano -- same rule as fingerlight.midi.pick_input_port.
    real = [n for n in ins if not n.lower().startswith("midi through")]
    if not real:
        raise SystemExit("only the ALSA loopback is present (%s) -- is the piano on?" % ins)
    return mido.open_input(real[0]), real[0]


def cmd_watch(args):
    mido = _mido()
    port, name = _open(mido, args.port)
    print("watching %s -- play something, Ctrl-C to stop" % name)
    last = None
    with port:
        for msg in port:
            now = time.perf_counter()
            gap = "" if last is None else "  +%6.1f ms" % ((now - last) * 1000)
            last = now
            if msg.type in ("note_on", "note_off"):
                on = msg.type == "note_on" and msg.velocity > 0
                print("%-9s %-4s vel %3d%s" % ("NOTE ON" if on else "note off",
                                               note_name(msg.note), msg.velocity, gap))
            elif msg.type == "control_change":
                extra = "  (sustain pedal)" if msg.control == 64 else ""
                print("CC %-3d    val %3d%s%s" % (msg.control, msg.value, gap, extra))
            else:
                print("%s%s" % (msg, gap))
    return 0


def cmd_latency(args):
    mido = _mido()
    port, name = _open(mido, args.port)
    print("play %d separate notes on %s" % (args.count, name))
    gaps, t0, seen = [], None, 0
    with port:
        for msg in port:
            if msg.type != "note_on" or msg.velocity == 0:
                continue
            now = time.perf_counter()
            if t0 is not None:
                gaps.append((now - t0) * 1000)
            t0 = now
            seen += 1
            print("  %2d/%d  %-4s vel %3d"
                  % (seen, args.count, note_name(msg.note), msg.velocity))
            if seen >= args.count:
                break
    if len(gaps) < 2:
        print("not enough notes to say anything")
        return 1
    gaps.sort()
    print("")
    print("inter-note gaps: min %.1f  median %.1f  max %.1f ms"
          % (gaps[0], gaps[len(gaps) // 2], gaps[-1]))
    print("This measures YOUR playing, not the link. What it is really for: check that")
    print("the count above equals the number of notes you played (nothing dropped) and")
    print("that no gap is absurdly long (nothing arrived in a late burst).")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["ports", "watch", "latency"])
    ap.add_argument("--port", default="", help="substring of the port name")
    ap.add_argument("--count", type=int, default=20)
    a = ap.parse_args()
    return {"ports": cmd_ports, "watch": cmd_watch, "latency": cmd_latency}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
