"""Tests for BLE-MIDI packet framing (decode_ble_packet), per MMA/AMEI RP-052 v1.0
(2015-11-01) sections 7 and 9 -- see the citation and worked rationale in
fingerlight/midi.py's decode_ble_packet docstring. Pure bytes in, no BLE stack.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fingerlight.midi import decode_ble_packet  # noqa: E402


def test_single_full_message():
    # header: ts_high=0 -> 0x80.  timestamp byte: ts_low=0 -> 0x80.  note on ch0,
    # pitch 60, vel 100.
    packet = bytes([0x80, 0x80, 0x90, 60, 100])
    assert decode_ble_packet(packet) == [(0, bytes([0x90, 60, 100]))]


def test_two_messages_running_status_same_timestamp():
    # header ts_high=0; timestamp ts_low=1 -> ts=1.  Full note-on, then a second
    # note-on via running status (no status byte, no new timestamp byte) -- the spec's
    # "2 MIDI Messages with Running Status" packet.
    packet = bytes([0x80, 0x81, 0x90, 60, 100, 64, 90])
    assert decode_ble_packet(packet) == [
        (1, bytes([0x90, 60, 100])),
        (1, bytes([0x90, 64, 90])),
    ]


def test_running_status_message_with_new_timestamp():
    # Second message omits the status byte (running status) but carries its own new
    # timestamp byte -- the spec's "Running Status MIDI Message with New Timestamp".
    packet = bytes([0x80, 0x81, 0x90, 60, 100, 0x82, 64, 90])
    result = decode_ble_packet(packet)
    assert result == [
        (1, bytes([0x90, 60, 100])),
        (2, bytes([0x90, 64, 90])),
    ]


def test_timestamp_wraparound_increments_high_bits():
    # ts_high=63 (0x3F) -> header 0xBF.  First timestamp: ts_low=100 -> byte 0xE4,
    # ts1 = (63<<7)|100 = 8164.  Second message's timestamp byte has ts_low=5 (0x85),
    # which is LESS than the previous ts_low (100): per SS9 this means the sender's
    # 13-bit clock wrapped, so the receiver must infer timestampHigh incremented by
    # one: ts2 = (64<<7)|5 = 8197.
    packet = bytes([0xBF, 0xE4, 0x90, 60, 100, 0x85, 64, 90])
    result = decode_ble_packet(packet)
    assert [t for t, _ in result] == [8164, 8197]
    assert [msg for _, msg in result] == [bytes([0x90, 60, 100]), bytes([0x90, 64, 90])]


def test_system_real_time_byte_between_messages_gets_its_own_entry():
    # header ts_high=0; ts_low=0 -> 0x80; a Timing Clock (0xF8) as its own "message",
    # each System Real-Time byte per SS7 must be preceded by its own timestamp byte.
    packet = bytes([0x80, 0x80, 0xF8, 0x81, 0x90, 60, 100])
    result = decode_ble_packet(packet)
    assert result == [(0, bytes([0xF8])), (1, bytes([0x90, 60, 100]))]


def test_sysex_start_causes_rest_of_packet_to_be_skipped_without_crashing():
    packet = bytes([0x80, 0x80, 0xF0, 0x7D, 0x01, 0x02, 0xF7])
    # Must not raise; SysEx is out of scope and the packet is simply dropped from here.
    assert decode_ble_packet(packet) == []


def test_empty_and_header_only_packets_are_handled():
    assert decode_ble_packet(b"") == []
    assert decode_ble_packet(bytes([0x80])) == []  # header with nothing following
