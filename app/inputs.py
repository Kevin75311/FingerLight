"""Input threads: USB-MIDI (primary) and the microphone (backup, D-29).

Both are optional imports.  On a PC without `mido` or `sounddevice` each reports itself
"unavailable" and the app still runs, driven by the preview's on-screen keys.

UNVERIFIED on hardware, like the backends they wrap: the MIDI thread is built on
`fingerlight.midi` (decoder and ingest are tested; the mido port is not), and the mic
thread on `fingerlight.audio.verify_frame` (tested on synthesised audio, never on a
real mic beside a real piano).
"""

from __future__ import annotations

import threading
import time

try:
    import mido  # type: ignore
except ImportError:
    mido = None  # type: ignore

from fingerlight.midi import MidiIngest, UsbMidiBackend, pick_input_port

#: How often to look for a piano that is not there yet, or has gone away.
RESCAN_S = 2.0


class MidiThread(threading.Thread):
    def __init__(self, engine):
        super().__init__(name="midi", daemon=True)
        self.engine = engine
        self.stop = threading.Event()

    def run(self) -> None:
        eng = self.engine
        if mido is None:
            eng.set_input_status("midi", "unavailable", "mido / python3-rtmidi not installed")
            return
        ingest = backend = port = None
        last_scan = 0.0
        while not self.stop.is_set():
            now = time.monotonic()
            if ingest is None:
                if now - last_scan < RESCAN_S:
                    time.sleep(0.05)
                    continue
                last_scan = now
                try:
                    port = pick_input_port(mido.get_input_names())
                    if port is None:
                        eng.set_input_status("midi", "absent", "no MIDI device -- piano off or unplugged?")
                        continue
                    backend = UsbMidiBackend(port)
                    ingest = MidiIngest([backend])
                    eng.set_input_status("midi", "live", port)
                except Exception as exc:
                    eng.set_input_status("midi", "absent", str(exc))
                    ingest = None
                    continue
            # rtmidi does not close a port when its device is unplugged, so ask ALSA
            # whether the port still exists rather than trusting `is_available()`.
            if now - last_scan >= RESCAN_S:
                last_scan = now
                if port not in mido.get_input_names():
                    for ev in ingest._tracker.release_all():
                        eng.note(ev.pitch, False, "midi")
                    backend.close()
                    ingest = None
                    eng.set_input_status("midi", "absent", "piano disconnected")
                    continue
            for ev in ingest.poll():
                eng.note(ev.pitch, ev.on, "midi")
            time.sleep(0.002)
        if backend is not None:
            backend.close()


class MicThread(threading.Thread):
    """Score-informed listening: only ever asks "is the expected note sounding?".

    Active only when Settings -> Input is "mic" *and* MIDI is not live (D-29: MIDI is
    the truth whenever it is present).  A pitch counts on its rising edge -- present in
    this block, absent in the last -- so a note still ringing from the previous event
    cannot satisfy a repeated note in the next one.
    """

    RATE = 44100
    BLOCK = 4096            # ~93 ms; the resolution audio.py was benchmarked at

    def __init__(self, engine):
        super().__init__(name="mic", daemon=True)
        self.engine = engine
        self.stop = threading.Event()

    def run(self) -> None:
        eng = self.engine
        try:
            import numpy as np  # noqa: F401
            import sounddevice as sd  # type: ignore
            from fingerlight.audio import verify_frame
        except Exception as exc:
            eng.set_input_status("mic", "unavailable", "sounddevice/numpy missing (%s)"
                                 % type(exc).__name__)
            return
        while not self.stop.is_set():
            if not self._wanted():
                eng.set_input_status("mic", "idle", "MIDI is live" if
                                     eng.inputs["midi"]["state"] == "live" else "off in settings")
                time.sleep(0.5)
                continue
            try:
                with sd.InputStream(samplerate=self.RATE, channels=1, dtype="float32",
                                    blocksize=self.BLOCK) as stream:
                    eng.set_input_status("mic", "live", str(sd.query_devices(kind="input")["name"]))
                    prev: frozenset[int] = frozenset()
                    while not self.stop.is_set() and self._wanted():
                        block, _ = stream.read(self.BLOCK)
                        want = self._expected()
                        heard = verify_frame(block[:, 0], self.RATE, want) if want else frozenset()
                        for p in sorted(heard - prev):
                            eng.note(p, True, "mic")
                        for p in sorted(prev - heard):
                            eng.note(p, False, "mic")
                        prev = heard
            except Exception as exc:
                eng.set_input_status("mic", "absent", str(exc))
                time.sleep(RESCAN_S)

    def _wanted(self) -> bool:
        eng = self.engine
        return eng.settings["input"] == "mic" and eng.inputs["midi"]["state"] != "live"

    def _expected(self) -> frozenset[int]:
        with self.engine._lock:
            a = self.engine.attempt
            return frozenset() if a is None else a.session.waiting_for
