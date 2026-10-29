"""A minimal Standard MIDI File reader, used ONLY as independent ground truth in tests.

Why this exists instead of a library: the point of the MusicXML acceptance tests is to
check our parser against something that did **not** come from our parser.  MuseScore
renders the same source score to MIDI, and this reads that MIDI back.  Two independent
implementations agreeing on onsets, durations and pitches is evidence; our parser
agreeing with expectations we typed ourselves is not.

Deliberately tiny and strict: it understands exactly the subset MuseScore emits for a
piano score (format 0/1, note on/off, set-tempo, time signature) and raises on anything
else rather than guessing.  It is test scaffolding, never shipped to the Pi.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass


@dataclass(frozen=True)
class MidiNote:
    t_ms: int
    d_ms: int
    pitch: int
    channel: int
    velocity: int


def _read_varlen(data: bytes, i: int) -> tuple[int, int]:
    value = 0
    while True:
        byte = data[i]
        i += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, i


def _parse_track(data: bytes) -> list[tuple[int, str, tuple]]:
    """Yield (absolute_tick, kind, payload) for the events we care about."""
    events: list[tuple[int, str, tuple]] = []
    i, tick, running_status = 0, 0, None
    while i < len(data):
        delta, i = _read_varlen(data, i)
        tick += delta
        status = data[i]
        if status & 0x80:
            i += 1
            running_status = status
        else:
            status = running_status
            if status is None:
                raise ValueError("running status with no prior status byte")

        if status == 0xFF:  # meta
            meta_type = data[i]
            i += 1
            length, i = _read_varlen(data, i)
            payload = data[i : i + length]
            i += length
            if meta_type == 0x51:  # set tempo, 3 bytes us-per-quarter
                us = int.from_bytes(payload, "big")
                events.append((tick, "tempo", (us,)))
            elif meta_type == 0x2F:
                break
        elif status in (0xF0, 0xF7):  # sysex - skip
            length, i = _read_varlen(data, i)
            i += length
        else:
            kind = status & 0xF0
            channel = status & 0x0F
            if kind in (0xC0, 0xD0):  # 1 data byte
                i += 1
            else:
                d1, d2 = data[i], data[i + 1]
                i += 2
                if kind == 0x90 and d2 > 0:
                    events.append((tick, "on", (channel, d1, d2)))
                elif kind == 0x80 or (kind == 0x90 and d2 == 0):
                    events.append((tick, "off", (channel, d1)))
    return events


def read_midi(path: str) -> list[MidiNote]:
    """Return every note in the file, sorted by (onset, pitch), in milliseconds."""
    with open(path, "rb") as fh:
        data = fh.read()

    if data[:4] != b"MThd":
        raise ValueError("not a Standard MIDI File")
    (_, ntrks, division) = struct.unpack(">HHH", data[8:14])
    if division & 0x8000:
        raise ValueError("SMPTE time division is not supported")
    ticks_per_quarter = division

    # Collect every event from every track onto one absolute timeline.
    all_events: list[tuple[int, str, tuple]] = []
    i = 14
    for _ in range(ntrks):
        if data[i : i + 4] != b"MTrk":
            raise ValueError("expected MTrk at byte %d" % i)
        length = struct.unpack(">I", data[i + 4 : i + 8])[0]
        all_events.extend(_parse_track(data[i + 8 : i + 8 + length]))
        i += 8 + length
    all_events.sort(key=lambda e: (e[0], e[1] != "tempo"))

    # Walk the timeline, converting ticks to ms through the tempo map as we go, so a
    # mid-score tempo change is honoured rather than ignored.
    us_per_quarter = 500000  # MIDI default = 120 bpm
    last_tick, elapsed_us = 0, 0.0
    open_notes: dict[tuple[int, int], tuple[float, int]] = {}
    notes: list[MidiNote] = []

    for tick, kind, payload in all_events:
        elapsed_us += (tick - last_tick) * us_per_quarter / ticks_per_quarter
        last_tick = tick
        if kind == "tempo":
            us_per_quarter = payload[0]
        elif kind == "on":
            channel, pitch, velocity = payload
            open_notes[(channel, pitch)] = (elapsed_us, velocity)
        elif kind == "off":
            channel, pitch = payload
            started = open_notes.pop((channel, pitch), None)
            if started is not None:
                start_us, velocity = started
                notes.append(
                    MidiNote(
                        t_ms=int(round(start_us / 1000.0)),
                        d_ms=int(round((elapsed_us - start_us) / 1000.0)),
                        pitch=pitch,
                        channel=channel,
                        velocity=velocity,
                    )
                )

    notes.sort(key=lambda n: (n.t_ms, n.pitch))
    return notes


if __name__ == "__main__":  # pragma: no cover - ground-truth dump
    import sys

    for n in read_midi(sys.argv[1]):
        print("t=%-6d d=%-6d pitch=%-4d ch=%d" % (n.t_ms, n.d_ms, n.pitch, n.channel))
