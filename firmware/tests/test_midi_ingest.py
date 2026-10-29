"""Tests for the backend interface, source-selection policy (MidiIngest, B5's D-06
USB-preferred/BLE-fallback policy), and the end-to-end wire-up: synthetic backend ->
MidiDecoder -> practice.PracticeSession. No hardware, no MIDI libraries -- ReplayBackend
is the pure synthetic source used throughout.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fingerlight.lightscore import load  # noqa: E402
from fingerlight.midi import (  # noqa: E402
    MidiDecoder,
    MidiIngest,
    NoteEvent,
    NoteTracker,
    ReplayBackend,
    apply_to_session,
    pick_input_port,
)
from fingerlight.notemap import StripSettings  # noqa: E402
from fingerlight.practice import PracticeSession  # noqa: E402

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCALE = os.path.join(_ROOT, "lightscore", "examples", "c-major-scale-rh.lightscore.json")


def note_on(pitch, vel=100):
    return bytes([0x90, pitch, vel])


def note_off(pitch):
    return bytes([0x90, pitch, 0])  # velocity-0 note off, same as the real device


# ------------------------------------------------------------- NoteTracker


def test_note_tracker_release_all_synthesizes_offs_for_held_pitches():
    tracker = NoteTracker()
    tracker.apply(NoteEvent(t=0.0, pitch=60, on=True, velocity=100, channel=0))
    tracker.apply(NoteEvent(t=0.0, pitch=64, on=True, velocity=90, channel=0))
    tracker.apply(NoteEvent(t=0.0, pitch=60, on=False, velocity=0, channel=0))
    assert tracker.held == frozenset({64})

    released = tracker.release_all()
    assert [(e.pitch, e.on) for e in released] == [(64, False)]
    assert tracker.held == frozenset()


# ------------------------------------------------------------- MidiIngest failover


def test_midi_ingest_reports_active_source_and_decodes():
    usb = ReplayBackend(name="usb", chunks=[note_on(60)])
    ble = ReplayBackend(name="ble", chunks=[note_on(64)])
    ingest = MidiIngest([usb, ble])

    events = ingest.poll()
    assert ingest.active_source == "usb"
    assert [(e.pitch, e.on) for e in events] == [(60, True)]


def test_midi_ingest_fails_over_and_releases_stuck_notes_without_crashing():
    usb = ReplayBackend(name="usb", chunks=[note_on(60)])
    ble = ReplayBackend(name="ble", chunks=[note_on(64)])
    ingest = MidiIngest([usb, ble])

    ingest.poll()  # pitch 60 now held, source is usb
    assert ingest.active_source == "usb"

    usb.disconnect()  # simulate the USB source disappearing mid-session

    released = ingest.poll()  # must not raise
    assert ingest.active_source == "ble"
    assert [(e.pitch, e.on) for e in released] == [(60, False)], (
        "the pitch usb still had held must be released, or it sticks on forever"
    )

    events = ingest.poll()
    assert [(e.pitch, e.on) for e in events] == [(64, True)]


def test_midi_ingest_rejects_empty_backend_list():
    with pytest.raises(ValueError):
        MidiIngest([])


# ------------------------------------------------------------- end-to-end


def test_synthetic_backend_decoder_and_practice_session_connect():
    """Synthetic backend -> MidiDecoder -> PracticeSession, driven through the whole
    8-note C-major scale fixture. Reports events matched / errors and proves the two
    modules (midi.py and practice.py, unmodified) actually interoperate.
    """
    score = load(SCALE)
    settings = StripSettings.load()
    session = PracticeSession(score=score, settings=settings)

    pitches = [n.p for n in score.notes]
    assert pitches == [60, 62, 64, 65, 67, 69, 71, 72]

    chunks = []
    for p in pitches:
        chunks.append(note_on(p))
        chunks.append(note_off(p))
    backend = ReplayBackend(name="usb", chunks=chunks)
    ingest = MidiIngest([backend])

    matched = 0
    while True:
        events = ingest.poll()
        if not events and not backend.chunks:
            break
        before = session.index
        apply_to_session(events, session)
        if session.index > before:
            matched += 1

    assert matched == len(score.events) == 8
    assert session.finished
    assert session.errors == 0


def test_wrong_note_is_reported_not_fatal():
    score = load(SCALE)
    settings = StripSettings.load()
    session = PracticeSession(score=score, settings=settings)
    dec = MidiDecoder(clock=lambda: 0.0)

    # First expected note is pitch 60; play a wrong one (61) then the right one.
    events = dec.feed(note_on(61))
    apply_to_session(events, session)
    assert session.errors == 1
    assert session.index == 0

    events = dec.feed(note_on(60))
    apply_to_session(events, session)
    assert session.index == 1


# ------------------------------------------------------------- port choice


def test_pick_input_port_skips_the_alsa_loopback():
    """On a Pi, rtmidi lists 'Midi Through' first; opening it hears nothing."""
    names = ["Midi Through:Midi Through Port-0 14:0", "CASIO USB-MIDI:CASIO USB-MIDI MIDI 1 20:0"]
    assert pick_input_port(names) == names[1]
    assert pick_input_port(names[:1]) is None
    assert pick_input_port([]) is None


def test_pedal_events_pass_through_ingest_without_touching_held_notes():
    from fingerlight.midi import PedalEvent

    usb = ReplayBackend(name="usb", chunks=[note_on(60) + bytes([0xB0, 64, 127])])
    ingest = MidiIngest([usb], MidiDecoder(pedal=True))
    events = ingest.poll()
    assert [type(e).__name__ for e in events] == ["NoteEvent", "PedalEvent"]
    assert ingest._tracker.held == frozenset({60})
    usb.disconnect()
    released = ingest.poll()
    assert [(e.pitch, e.on) for e in released] == [(60, False)]
    assert not any(isinstance(e, PedalEvent) for e in released)
