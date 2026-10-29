"""Does the USB microphone hear the piano? (D-29: it is the no-MIDI backup.)

    python tools/hw/mic_test.py devices
    python tools/hw/mic_test.py level --seconds 5     # play while this runs
    python tools/hw/mic_test.py note --seconds 3      # strongest pitch it heard

Remember what this input is for. With USB-MIDI connected the piano already says
exactly which key went down and how hard; the mic exists for instruments that
cannot. And with headphones on it hears nothing -- that is the design, not a
fault.
"""

from __future__ import annotations

import argparse
import math
import sys

NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _sd():
    try:
        import sounddevice as sd
    except ImportError:
        raise SystemExit("sounddevice is missing. On the Pi:\n"
                         "  sudo apt install libportaudio2 python3-cffi\n"
                         "  pip3 install --user --break-system-packages sounddevice\n"
                         "  (Trixie has no python3-sounddevice package)")
    return sd


def cmd_devices(_args):
    sd = _sd()
    print(sd.query_devices())
    try:
        print("")
        print("default input: %s" % sd.query_devices(kind="input")["name"])
    except Exception:
        print("no default input -- pass --device with an index from the list above")
    return 0


def _record(args):
    import numpy as np

    sd = _sd()
    kw = {"samplerate": args.rate, "channels": 1, "dtype": "float32"}
    if args.device != "":
        kw["device"] = int(args.device) if args.device.isdigit() else args.device
    print("recording %.1f s ..." % args.seconds, flush=True)
    audio = sd.rec(int(args.seconds * args.rate), **kw)
    sd.wait()
    return np.asarray(audio).reshape(-1)


def cmd_level(args):
    import numpy as np

    x = _record(args)
    rms = float(np.sqrt(np.mean(x ** 2)))
    peak = float(np.max(np.abs(x)))
    clipped = int(np.sum(np.abs(x) > 0.995))
    dbfs = 20 * math.log10(rms) if rms > 0 else -120.0
    print("samples %d  rms %.4f (%.1f dBFS)  peak %.3f  clipped %d"
          % (len(x), rms, dbfs, peak, clipped))
    if peak < 0.01:
        print("VERDICT: silence. Wrong device, muted input, or headphones are plugged in.")
        return 1
    if clipped > len(x) * 0.001:
        print("VERDICT: clipping. Move the mic off the speaker grille or drop the gain.")
        return 1
    if dbfs < -45:
        print("VERDICT: very quiet. Usable for onsets, marginal for pitch.")
    else:
        print("VERDICT: healthy level.")
    return 0


def cmd_note(args):
    import numpy as np

    x = _record(args)
    if float(np.max(np.abs(x))) < 0.01:
        print("silence -- nothing to analyse")
        return 1
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    freqs = np.fft.rfftfreq(len(x), 1.0 / args.rate)
    band = (freqs > 27.0) & (freqs < 4200.0)           # A0..C8 fundamentals
    f = float(freqs[band][int(np.argmax(spec[band]))])
    midi = int(round(69 + 12 * math.log2(f / 440.0)))
    cents = 1200 * math.log2(f / (440.0 * 2 ** ((midi - 69) / 12.0)))
    print("strongest partial %.1f Hz -> %s%d (%+.0f cents)"
          % (f, NAMES[midi % 12], midi // 12 - 1, cents))
    print("One strongest partial is not transcription -- it only shows the mic hears")
    print("pitch at all. The real path is score-informed verification, hardware 6.4.")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["devices", "level", "note"])
    ap.add_argument("--device", default="", help="index or name substring")
    ap.add_argument("--rate", type=int, default=44100)
    ap.add_argument("--seconds", type=float, default=3.0)
    a = ap.parse_args()
    return {"devices": cmd_devices, "level": cmd_level, "note": cmd_note}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
