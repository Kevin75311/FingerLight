"""MIDI ingest (B5): the path by which the bar learns which keys were actually pressed.

USB MIDI is primary, BLE MIDI is secondary (decision D-06); the Pi is the USB host.  Two
cleanly separated layers, same discipline as the rest of `fingerlight`:

1. **A pure, hardware-free core** -- `MidiDecoder` (raw-byte -> `NoteEvent`, with running
   status and split-read handling) and `decode_ble_packet` (BLE-MIDI's own packet
   framing).  100% testable on a PC with no piano and no BLE stack attached.
2. **Thin backend adapters** -- `UsbMidiBackend`, `BleMidiBackend`, and the pure
   `ReplayBackend` used by tests -- behind one `MidiSource` protocol.  Third-party
   imports (`mido`/`python-rtmidi` for USB; a BLE GATT client for BLE) are guarded, so
   importing this module on a machine with neither installed still works and the pure
   core still runs -- verified by `python -c "import fingerlight.midi"` on this machine,
   which has none of them.
3. **`MidiIngest`** -- USB-preferred/BLE-fallback source selection: polls the preferred
   backend, reports which one is live via `active_source`, and fails over without
   raising when a source disappears mid-session, releasing any pitches it still held so
   `practice.py` never sees a stuck key.

`practice.py` (untouched, out of scope for B5) wants exactly `note_on(pitch: int)` and
`note_off(pitch: int)` -- no velocity, no channel, no timestamp.  `apply_to_session()` at
the bottom of this file is the thin adapter that feeds it from a `NoteEvent` stream.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Protocol, Sequence

#: The 88-key range, A0..C8.  Mirrors lightscore.py / notemap.py's own constants -- each
#: module defines it locally rather than cross-importing, which is the existing house
#: convention (see notemap.py:30-31).
MIDI_MIN = 21
MIDI_MAX = 108

#: Control Change number practice.py would need if it ever grew pedal awareness.
CC_SUSTAIN = 64


# ============================================================================
# Layer 1: pure core -- decoder, note events, BLE packet framing
# ============================================================================


@dataclass(frozen=True)
class NoteEvent:
    """One decoded note-on or note-off, in `practice.py`'s vocabulary (pitch only).

    `on=False` covers **both** an explicit Note Off (status nibble 0x8) and a Note On
    with velocity 0 -- real keyboards, the PX-S1100 included, use the latter routinely,
    and a decoder that only recognises the former leaves keys stuck on forever (the
    "stuck note" failure this module is built to avoid; see test_midi_decoder.py).

    `t`: a clock reading at decode time, for future latency/timing work -- practice.py
    does not consume it today.  For `MidiDecoder.feed()` without an override, it is
    whatever the injected clock returns (default `time.monotonic()`, seconds).  For a
    message decoded from a BLE packet, it is that message's embedded timestamp in
    seconds (`ble_ms / 1000`).  The two are **not** on the same clock and must not be
    compared across backends.
    """

    t: float
    pitch: int
    on: bool
    velocity: int
    channel: int


def _channel_voice_data_len(status: int) -> int:
    """Data-byte count for a channel-voice status byte (0x80-0xEF)."""
    nibble = status & 0xF0
    return 1 if nibble in (0xC0, 0xD0) else 2


#: System Common data-byte counts (0xF1-0xF6).  0xF4/0xF5 are reserved/undefined in
#: MIDI 1.0; treated as zero data bytes so a stray one cannot desync the parser.
_SYSTEM_COMMON_DATA_LEN = {0xF1: 1, 0xF2: 2, 0xF3: 1, 0xF4: 0, 0xF5: 0, 0xF6: 0}


class MidiDecoder:
    """Streaming MIDI byte decoder with running-status support.

    Feed raw bytes as they arrive, in *any* chunking -- one byte at a time, many at
    once, or a single 3-byte message split across two `feed()` calls -- and get back
    complete decoded note events.  State (the current running-status byte and any
    partially-received data bytes) lives on the instance and persists across calls, so
    a message split across a read boundary is decoded correctly rather than lost or
    corrupted.

    Handles, deliberately:
      * Note-on / note-off, including note-on-velocity-0 as note-off (see `NoteEvent`).
      * Running status: a repeated status byte may be omitted; the decoder remembers the
        last channel-voice status byte and applies it to bare data bytes.  Real MIDI
        streams (and BLE-MIDI-reconstructed ones, see `decode_ble_packet`) use this
        constantly.
      * System Real-Time bytes (0xF8-0xFF) interleaved at *any* point in the stream,
        including mid-message -- MIDI 1.0 allows this and it must not disturb the
        pending message or the running-status byte.
      * System Common (0xF1-0xF6) and System Exclusive (0xF0...0xF7): consumed so the
        byte stream stays in sync, but not surfaced as note events -- out of scope for a
        finger-cue bar.
      * Channel filtering (`channel=0..15`, or `None` for all) and the 88-key pitch
        range 21..108 -- anything outside either is decoded (so parsing stays correct)
        but not emitted.
      * Sustain pedal (CC 64, `CC_SUSTAIN`): its 2 data bytes are consumed correctly (so
        running status keeps sync) but **deliberately not surfaced** as a `NoteEvent`.
        `practice.py`'s `PracticeSession` has no pedal concept -- chord completion is
        purely which pitches are held, and `note_off` never un-satisfies an event (see
        practice.py's `note_off` docstring) -- so pedal state has no consumer today. A
        future practice mode that wants sustain should extend `NoteEvent` or add a
        separate event type here, not repurpose this silent drop.
    """

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        channel: int | None = None,
    ) -> None:
        self._clock = clock
        self._channel = channel
        self._status: int | None = None
        self._pending: list[int] = []
        self._in_sysex = False

    def feed(self, data: bytes, t: float | None = None) -> list[NoteEvent]:
        """Decode a chunk of raw bytes, returning any complete note events in order.

        `t`, if given, stamps every event produced by *this* call instead of calling the
        injected clock -- used by the BLE path, which already knows each message's
        timestamp from the packet.
        """
        events: list[NoteEvent] = []
        for byte in data:
            events.extend(self._feed_byte(byte, t))
        return events

    def _feed_byte(self, byte: int, t_override: float | None) -> list[NoteEvent]:
        if 0xF8 <= byte <= 0xFF:
            # System Real-Time: single byte, legal at any position, touches nothing.
            return []

        if byte & 0x80:
            if byte == 0xF0:  # SysEx start -- unsupported, drop until F7 or next status
                self._in_sysex = True
                self._status = None
                self._pending = []
                return []
            if byte == 0xF7:  # SysEx end
                self._in_sysex = False
                return []
            if 0xF1 <= byte <= 0xF6:  # System Common -- cancels running status
                self._in_sysex = False
                self._status = None
                self._pending = []
                return []
            # Channel-voice status byte (0x80-0xEF): establishes running status.
            self._in_sysex = False
            self._status = byte
            self._pending = []
            return []

        # Data byte.
        if self._in_sysex or self._status is None:
            return []  # no context to interpret it against; drop rather than guess
        self._pending.append(byte)
        if len(self._pending) < _channel_voice_data_len(self._status):
            return []
        data, self._pending = self._pending, []
        return self._emit(self._status, data, t_override)

    def _emit(
        self, status: int, data: list[int], t_override: float | None
    ) -> list[NoteEvent]:
        nibble = status & 0xF0
        channel = status & 0x0F
        if self._channel is not None and channel != self._channel:
            return []
        if nibble not in (0x80, 0x90):
            return []  # CC (incl. sustain), program change, pressure, pitch bend, ...

        pitch, velocity = data
        if not (MIDI_MIN <= pitch <= MIDI_MAX):
            return []
        on = nibble == 0x90 and velocity > 0
        t = self._clock() if t_override is None else t_override
        return [NoteEvent(t=t, pitch=pitch, on=on, velocity=velocity, channel=channel)]


def decode_ble_packet(packet: bytes) -> list[tuple[int, bytes]]:
    """Decode one BLE-MIDI packet into `(timestamp_ms, raw_midi_message_bytes)` pairs.

    Implements MMA/AMEI RP-052, "Specification for MIDI over Bluetooth Low Energy
    (BLE-MIDI)", Version 1.0, 2015-11-01 (fetched from
    https://www.hangar42.nl/wp-content/uploads/2017/10/BLE-MIDI-spec.pdf, the MMA's own
    mirror having moved):

    * SS7 "Packet Encoding": the first byte is a header byte (bit7=1, bit6=reserved-0,
      bits5-0=`timestampHigh`).  What follows is a sequence of messages, each either a
      *full* message (a timestamp byte [bit7=1, bits6-0=`timestampLow`] + a status byte
      + data bytes) or a *running-status* message (status byte omitted, reusing the most
      recently preceding full message's status within the same packet; the timestamp
      byte before it is itself optional -- if absent, the previous message's timestamp
      is reused).  Because a timestamp byte and a status byte are bit-indistinguishable
      (both have bit7 set), the grammar is resolved positionally: a status byte can
      **only** ever follow a timestamp byte (SS7 rule "every MIDI Status byte must be
      preceded by a timestamp byte"), so at every message boundary a bit7-set byte is
      unambiguously a timestamp, and the byte *after* it decides whether a new status
      follows (bit7 set) or running-status data does (bit7 clear).  System Real-Time
      bytes may appear at any such boundary as a one-byte "message" and do not disturb
      running status.  System Common cancels running status; the end of the packet
      always does.
    * SS9 "Bluetooth LE MIDI Timestamps": the 13-bit millisecond timestamp is
      `(timestampHigh << 7) | timestampLow`.  If a later message's `timestampLow` in the
      same packet is smaller than the previous one's, the sender's clock wrapped (13
      bits = 8192 ms) and the receiver reconstructs it by incrementing `timestampHigh` by
      one -- implemented below and exercised by
      `test_midi_ble.py::test_timestamp_wraparound_increments_high_bits`.

    System Exclusive (SS8, "Multiple Packet Encoding") is **out of scope**: it is the
    only message type that spans multiple BLE packets, and a finger-cue bar has no use
    for SysEx -- `practice.py` only ever needs channel-voice note events.  A SysEx start
    byte (0xF0) inside a packet causes the rest of that packet to be skipped rather than
    misparsed as channel-voice data.
    """
    if not packet or not (packet[0] & 0x80):
        return []

    ts_high = packet[0] & 0x3F
    out: list[tuple[int, bytes]] = []
    last_status: int | None = None
    last_ts_low: int | None = None
    last_ts_ms: int | None = None
    in_sysex = False

    i = 1
    n = len(packet)
    while i < n:
        byte = packet[i]

        if not (byte & 0x80):
            # Running-status data byte, no new timestamp: reuse the last one.
            if in_sysex or last_status is None or last_ts_ms is None:
                i += 1
                continue
            ndata = _channel_voice_data_len(last_status)
            data = packet[i : i + ndata]
            i += ndata
            out.append((last_ts_ms, bytes([last_status]) + data))
            continue

        # bit7 set at a message boundary => timestamp byte (see docstring).
        ts_low = byte & 0x7F
        if last_ts_low is not None and ts_low < last_ts_low:
            ts_high += 1  # 13-bit wrap, SS9
        last_ts_low = ts_low
        ts_ms = (ts_high << 7) | ts_low
        last_ts_ms = ts_ms
        i += 1
        if i >= n:
            break  # timestamp with nothing after it: malformed tail, drop

        nxt = packet[i]
        if not (nxt & 0x80):
            # Running-status message with a new timestamp, no status byte.
            if in_sysex or last_status is None:
                i += 1
                continue
            ndata = _channel_voice_data_len(last_status)
            data = packet[i : i + ndata]
            i += ndata
            out.append((ts_ms, bytes([last_status]) + data))
            continue

        # A status byte.
        status = nxt
        i += 1
        if 0xF8 <= status <= 0xFF:
            out.append((ts_ms, bytes([status])))
            continue
        if status == 0xF0:
            in_sysex = True
            last_status = None
            continue
        if status == 0xF7:
            in_sysex = False
            continue
        if 0xF1 <= status <= 0xF6:
            in_sysex = False
            last_status = None
            ndata = _SYSTEM_COMMON_DATA_LEN.get(status, 0)
            i += ndata
            continue
        # Channel-voice status: full message, establishes running status.
        in_sysex = False
        last_status = status
        ndata = _channel_voice_data_len(status)
        data = packet[i : i + ndata]
        i += ndata
        out.append((ts_ms, bytes([status]) + data))

    return out


class NoteTracker:
    """Tracks which pitches are currently held, from a stream of `NoteEvent`.

    Used by `MidiIngest` to synthesize note-off events for everything still held when a
    source disappears mid-session -- without this, a source dropping mid-chord would
    leave `practice.py`'s held set (and the LEDs) stuck on, the same failure mode a
    missed velocity-0 note-off causes.
    """

    def __init__(self) -> None:
        self._held: set[int] = set()

    def apply(self, event: NoteEvent) -> None:
        if event.on:
            self._held.add(event.pitch)
        else:
            self._held.discard(event.pitch)

    @property
    def held(self) -> frozenset[int]:
        return frozenset(self._held)

    def release_all(self, t: float = 0.0) -> list[NoteEvent]:
        """Synthesize note-offs for every currently held pitch, then clear."""
        events = [
            NoteEvent(t=t, pitch=p, on=False, velocity=0, channel=0)
            for p in sorted(self._held)
        ]
        self._held.clear()
        return events


def apply_to_session(events: Sequence[NoteEvent], session) -> None:
    """Feed decoded events into a `practice.PracticeSession`-shaped consumer.

    Duck-typed on purpose (just needs `note_on(pitch)` / `note_off(pitch)`) rather than
    importing `PracticeSession`, so this module stays the integration point without
    adding a hard dependency in either direction; `practice.py` itself is untouched.
    """
    for event in events:
        if event.on:
            session.note_on(event.pitch)
        else:
            session.note_off(event.pitch)


# ============================================================================
# Layer 2: backend adapters -- USB, BLE, and the synthetic replay backend
# ============================================================================


class MidiSource(Protocol):
    """What `MidiIngest` needs from a backend, USB/BLE/replay alike."""

    name: str

    def is_available(self) -> bool:
        """Whether this source is currently connected/usable."""

    def read(self) -> bytes:
        """Return any raw bytes received since the last call; `b""` if none."""

    def close(self) -> None:
        """Release the source. Idempotent."""


# --- USB backend -------------------------------------------------------------------
# Guarded: mido / python-rtmidi are NOT installed on this machine (no hardware either),
# so importing fingerlight.midi must not fail here.  Verified by
# `python -c "import fingerlight.midi"` (see firmware/README.md).
try:
    import mido  # type: ignore
except ImportError:
    mido = None  # type: ignore


def pick_input_port(names: Sequence[str]) -> str | None:
    """The first input that is a real device, or None.

    On a Pi, ALSA's "Midi Through" loopback (client 14) is always present and always
    listed first, so "take the first port" opens a port nothing ever plays into: the app
    looks healthy and hears no notes.  Skip it.
    """
    for name in names:
        if not name.lower().startswith("midi through"):
            return name
    return None


class UsbMidiBackend:
    """USB MIDI, primary per D-06.  The Pi is the USB host; the PX-S1100 is the device.

    UNVERIFIED on this machine: no `mido`/`python-rtmidi`, no piano attached. Built
    against mido's documented `mido.open_input()` / non-blocking `poll()` API; must be
    exercised on the Pi with the piano attached before being trusted.
    """

    name = "usb"

    def __init__(self, port_name: str | None = None) -> None:
        if mido is None:
            raise RuntimeError(
                "mido/python-rtmidi is not installed; USB MIDI backend unavailable"
            )
        self._port_name = port_name or self._first_port()
        self._port = mido.open_input(self._port_name)  # type: ignore[union-attr]

    @staticmethod
    def _first_port() -> str:
        names = mido.get_input_names()  # type: ignore[union-attr]
        port = pick_input_port(names)
        if port is None:
            raise RuntimeError("no USB MIDI input ports found (only: %r)" % (names,))
        return port

    def is_available(self) -> bool:
        return self._port is not None and not self._port.closed

    def read(self) -> bytes:
        raw = bytearray()
        for msg in self._port.iter_pending():
            raw.extend(msg.bytes())
        return bytes(raw)

    def close(self) -> None:
        if self._port is not None and not self._port.closed:
            self._port.close()


# --- BLE backend -------------------------------------------------------------------
try:
    import bleak  # type: ignore
except ImportError:
    bleak = None  # type: ignore

#: BLE-MIDI GATT identifiers, SS3 of the spec cited in `decode_ble_packet`.
BLE_MIDI_SERVICE_UUID = "03b80e5a-ede8-4b33-a751-6ce34ec4c700"
BLE_MIDI_CHARACTERISTIC_UUID = "7772e5db-3868-4112-a1a9-f2669d106bf3"


class BleMidiBackend:
    """BLE MIDI, secondary per D-06 (PX-S1100 has Bluetooth 5.0 GATT MIDI).

    UNVERIFIED on this machine: no BLE stack, no adapter, no piano. Packet *framing* is
    implemented and tested in the pure core (`decode_ble_packet`); this class is only
    the untestable plumbing that hands raw notify-characteristic payloads to it. Written
    against `bleak`'s documented central-role notify API; must be exercised on the Pi
    with the piano's BLE MIDI advertising before being trusted.
    """

    name = "ble"

    def __init__(self, address: str) -> None:
        if bleak is None:
            raise RuntimeError("bleak is not installed; BLE MIDI backend unavailable")
        self._address = address
        self._connected = False
        self._buffer = bytearray()
        # A real implementation drives bleak's asyncio client from the Pi's event loop
        # and appends each notification payload to self._buffer; left as an interface
        # stub here because there is no BLE stack or adapter to test it against.

    def is_available(self) -> bool:
        return self._connected

    def read(self) -> bytes:
        data, self._buffer = bytes(self._buffer), bytearray()
        return data

    def close(self) -> None:
        self._connected = False


@dataclass
class ReplayBackend:
    """Feeds a canned sequence of raw byte chunks. Pure, hardware-free, used by tests.

    Each `read()` pops the next queued chunk (`b""` once exhausted); `disconnect()`
    simulates the source going away mid-session, which `MidiIngest` must survive.
    """

    name: str = "replay"
    chunks: list[bytes] = field(default_factory=list)
    _available: bool = True

    def is_available(self) -> bool:
        return self._available

    def read(self) -> bytes:
        if not self.chunks:
            return b""
        return self.chunks.pop(0)

    def close(self) -> None:
        self._available = False

    def disconnect(self) -> None:
        self._available = False


# ============================================================================
# Layer 3: source-selection policy
# ============================================================================


class MidiIngest:
    """USB-preferred, BLE-fallback source selection (decision D-06).

    Polls backends in the order given (USB first, BLE second, by convention of how the
    caller constructs it) and reports which one is live via `active_source`.  When a
    backend stops being available -- unplugged USB, a BLE disconnect -- `poll()` does
    **not** raise: it synthesizes note-offs for anything the lost source still had held
    (via `NoteTracker.release_all()`, so `practice.py` never sees a stuck key) and moves
    to the next backend.  Does not currently retry a higher-priority backend once
    failed over; call `reset()` to re-arm the preferred order (e.g. from a periodic
    "did USB come back" check).
    """

    def __init__(
        self, backends: Sequence[MidiSource], decoder: MidiDecoder | None = None
    ) -> None:
        if not backends:
            raise ValueError("MidiIngest needs at least one backend")
        self._backends = list(backends)
        self.decoder = decoder or MidiDecoder()
        self._tracker = NoteTracker()
        self._active_index = 0

    @property
    def active_source(self) -> str | None:
        if self._active_index >= len(self._backends):
            return None
        return self._backends[self._active_index].name

    def reset(self) -> None:
        """Re-arm the preferred backend order (index 0) for the next `poll()`."""
        self._active_index = 0

    def poll(self) -> list[NoteEvent]:
        """Read and decode whatever is available; fail over on a dead source.

        Returns whatever note events resulted -- possibly synthesized note-offs from a
        failover, possibly decoded play, possibly an empty list if nothing changed.
        """
        while self._active_index < len(self._backends):
            backend = self._backends[self._active_index]
            if not backend.is_available():
                released = self._tracker.release_all()
                self._active_index += 1
                return released
            try:
                data = backend.read()
            except Exception:
                released = self._tracker.release_all()
                self._active_index += 1
                return released
            if not data:
                return []
            events = self.decoder.feed(data)
            for event in events:
                self._tracker.apply(event)
            return events
        return []
