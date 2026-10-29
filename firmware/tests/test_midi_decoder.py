"""Tests for the pure MIDI decoder core (B5): running status, the stuck-note trap
(note-on velocity 0), split reads, channel/range filtering, sustain, System Real-Time
interleaving. No hardware, no MIDI libraries -- everything here runs on raw bytes.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fingerlight.midi import MidiDecoder, NoteEvent  # noqa: E402


def ev_tuples(events):
    return [(e.pitch, e.on, e.velocity, e.channel) for e in events]


# ------------------------------------------------------------- realistic byte stream


def test_realistic_stream_running_status_velocity_zero_and_split_read():
    """One decoder call feeds: a full note-on, a running-status note-on, a
    running-status note-on-velocity-0 (must decode as note-off), then a *status byte
    alone* -- with its two data bytes arriving in a second, separate feed() call. All
    four resulting events are checked exactly.
    """
    dec = MidiDecoder(clock=lambda: 0.0)

    chunk1 = bytes(
        [
            0x90, 0x3C, 0x64,  # full: note on, ch0, pitch 60 (0x3C), vel 100
            0x40, 0x5A,        # running status -> note on, pitch 64 (0x40), vel 90
            0x3C, 0x00,        # running status -> note on, pitch 60, vel 0 == note off
            0x80,              # start of an EXPLICIT note-off message: status only
        ]
    )
    chunk2 = bytes([0x40, 0x00])  # ... its data bytes, arriving in the next read

    events = dec.feed(chunk1)
    assert ev_tuples(events) == [
        (60, True, 100, 0),
        (64, True, 90, 0),
        (60, False, 0, 0),
    ]

    events2 = dec.feed(chunk2)
    assert ev_tuples(events2) == [(64, False, 0, 0)]

    # Four note events checked in total across the two calls.
    assert len(events) + len(events2) == 4


# ------------------------------------------------------------- the stuck-note trap


def test_note_on_velocity_zero_releases_the_note():
    dec = MidiDecoder(clock=lambda: 0.0)
    events = dec.feed(bytes([0x90, 60, 100]))  # note on
    events += dec.feed(bytes([0x90, 60, 0]))   # note on, vel 0 -> must be note off
    assert ev_tuples(events) == [(60, True, 100, 0), (60, False, 0, 0)]


def test_stuck_note_bug_is_actually_caught_by_the_above_test():
    """Demonstrates the previous test is not vacuous: a decoder that (wrongly) treats
    every Note On as `on=True` regardless of velocity produces the stuck-note bug this
    module exists to avoid, and the assertion above would fail against it.
    """
    dec = MidiDecoder(clock=lambda: 0.0)
    events = dec.feed(bytes([0x90, 60, 100]))
    events += dec.feed(bytes([0x90, 60, 0]))

    # Simulate the buggy decoder's output directly (on=True always for a Note On
    # status, ignoring velocity) and show it fails the same assertion the correct
    # decoder passes above -- i.e. the test *can* fail, it is not tautological.
    buggy = [NoteEvent(t=0.0, pitch=e.pitch, on=True, velocity=e.velocity, channel=0)
             for e in events]
    with pytest.raises(AssertionError):
        assert ev_tuples(buggy) == [(60, True, 100, 0), (60, False, 0, 0)]


# ------------------------------------------------------------- running status details


def test_running_status_persists_across_multiple_feed_calls():
    dec = MidiDecoder(clock=lambda: 0.0)
    dec.feed(bytes([0x90, 60, 100]))
    events = dec.feed(bytes([64, 90]))  # bare data bytes in a *new* call
    assert ev_tuples(events) == [(64, True, 90, 0)]


def test_system_real_time_byte_mid_message_does_not_disturb_running_status():
    dec = MidiDecoder(clock=lambda: 0.0)
    # Active Sensing (0xFE) dropped in between the status byte and its data bytes.
    events = dec.feed(bytes([0x90, 60, 0xFE, 100]))
    assert ev_tuples(events) == [(60, True, 100, 0)]


def test_system_common_cancels_running_status():
    dec = MidiDecoder(clock=lambda: 0.0)
    dec.feed(bytes([0x90, 60, 100]))
    dec.feed(bytes([0xF6]))  # Tune Request, a System Common message with 0 data bytes
    # Running status is gone: bare data bytes now have no status context and are
    # dropped rather than misinterpreted as a new note-on.
    events = dec.feed(bytes([64, 90]))
    assert events == []


# ------------------------------------------------------------- filtering


def test_channel_filter_drops_other_channels():
    dec = MidiDecoder(clock=lambda: 0.0, channel=0)
    events = dec.feed(bytes([0x91, 60, 100]))  # note on, channel 1
    assert events == []
    events = dec.feed(bytes([0x90, 60, 100]))  # note on, channel 0
    assert ev_tuples(events) == [(60, True, 100, 0)]


@pytest.mark.parametrize("pitch, expect_event", [(20, False), (21, True),
                                                   (108, True), (109, False)])
def test_pitch_range_filter_is_the_88_key_boundary(pitch, expect_event):
    dec = MidiDecoder(clock=lambda: 0.0)
    events = dec.feed(bytes([0x90, pitch, 100]))
    assert bool(events) == expect_event


def test_sustain_pedal_cc_is_consumed_but_not_surfaced():
    dec = MidiDecoder(clock=lambda: 0.0)
    events = dec.feed(bytes([0xB0, 64, 127]))  # CC 64 (sustain) on
    assert events == []
    # And it must not desync running status for what follows: a *different* running
    # running-status context (note on) parses correctly afterwards.
    events = dec.feed(bytes([0x90, 60, 100]))
    assert ev_tuples(events) == [(60, True, 100, 0)]


# ------------------------------------------------------------- injected clock


def test_clock_is_injected_not_wall_time():
    calls = iter([1.0, 2.0])
    dec = MidiDecoder(clock=lambda: next(calls))
    events = dec.feed(bytes([0x90, 60, 100]))
    events += dec.feed(bytes([0x90, 60, 0]))
    assert [e.t for e in events] == [1.0, 2.0]


def test_timestamp_override_bypasses_clock():
    dec = MidiDecoder(clock=lambda: 999.0)
    events = dec.feed(bytes([0x90, 60, 100]), t=42.0)
    assert events[0].t == 42.0


# ------------------------------------------------------------- sustain, opted in


def test_pedal_events_are_edges_only_when_asked_for():
    from fingerlight.midi import PedalEvent

    dec = MidiDecoder(clock=lambda: 1.5, pedal=True)
    # down, a half-pedal wobble that stays down, up; then a note with running status
    events = dec.feed(bytes([0xB0, 64, 127, 64, 90, 64, 0, 0x90, 60, 100]))
    pedals = [e for e in events if isinstance(e, PedalEvent)]
    assert [(p.down, p.t) for p in pedals] == [(True, 1.5), (False, 1.5)]
    assert ev_tuples([e for e in events if not isinstance(e, PedalEvent)]) == [(60, True, 100, 0)]


def test_other_controllers_never_surface_even_with_pedal_on():
    dec = MidiDecoder(clock=lambda: 0.0, pedal=True)
    assert dec.feed(bytes([0xB0, 7, 100, 0xB0, 67, 127])) == []   # volume, soft pedal
