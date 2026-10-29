"""The app engine: everything between the firmware's state machine and the touchscreen.

It owns one practice attempt at a time and feeds it from whichever input is live
(USB-MIDI, the mic, or the on-screen keys of the preview), pushes each new frame to the
LED bar, and keeps the small amount of state the screens need that the firmware does not
have: the score library, settings, attempt history, loops, tempo and the play-along clock.

No HTTP here and no hardware imports -- `server.py` serves it, `inputs.py` and `leds.py`
plug the hardware in.  Everything is guarded by one re-entrant lock; every change bumps
`version` and wakes the server's event stream.

What is app-level rather than firmware (docs/app.md section 5 lists these as missing
from the state machine; they live here so the state machine stays pure):

* **Play-along mode** -- a score clock scaled by the tempo; when it passes the end of an
  event the player has not finished, the unplayed notes are recorded as *missed* and
  the cursor moves on.  A player who is *ahead* of the clock is never held back.
* **Loops** -- an inclusive event range; passing its end seeks back to its start.
* **Attempt history** -- one JSON file, so the Session screen can show a delta.
* **Beats** -- derived from the score's tempo map.  Bars are NOT shown anywhere: the
  light-score format has no time signature, so a bar number would be invented.
"""

from __future__ import annotations

import bisect
import datetime as _dt
import json
import os
import statistics
import threading
import time
from dataclasses import dataclass, field

from fingerlight import colors
from fingerlight.lightscore import LightScore, LightScoreError, group_events
from fingerlight.lightscore import load as load_score
from fingerlight.notemap import StripSettings
from fingerlight.practice import PracticeSession

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
FINGER_NAMES = {1: "thumb", 2: "index", 3: "middle", 4: "ring", 5: "little"}

SCORE_SUFFIX = ".lightscore.json"
#: Files a player may drop into the scores folder that the Pi cannot play yet: the score
#: compiler is a PC-side step (D-23), so these show as "not compiled", never as missing.
SOURCE_SUFFIXES = (".musicxml", ".mxl", ".mid", ".midi", ".pdf", ".png", ".jpg", ".jpeg")

#: Play-along lead-in before the first event (and after a loop wraps), in score ms.
LEAD_IN_MS = 1000
#: How long the last event of a score stays "due" in play-along when it is very short.
MIN_LAST_EVENT_MS = 600

#: A hesitation, in wait mode: an event that took longer than both of these.  A
#: heuristic pending real use -- the relative term makes it adapt to a slow reader, the
#: absolute floor stops a fast run from flagging every ordinary pause.
HESITATION_MIN_S = 1.5
HESITATION_X_MEDIAN = 2.5

#: Worst-case draw of one WS2812B at full white (D-20: 176 LEDs -> 10.6 A).
AMPS_PER_LED_WHITE = 0.060

DEFAULT_SETTINGS = {
    "mode": "wait",          # "wait" (wait for me) | "along" (play along)
    "palette": colors.PALETTE,
    "brightness": 0.40,      # global cap, 0.05..1.0
    "lookahead": 1,          # events dim-lit ahead of now (Q-06)
    "input": "midi",         # "midi" | "mic"
    "hands": "both",         # library default: "both" | "L" | "R"
    "led_order": "GRB",      # wire order, from `led_test.py order`
    "led_one_pattern": "1110",  # SPI bit pattern for a logical 1, from bench stage 3
}


def note_name(p: int) -> str:
    return "%s%d" % (NOTE_NAMES[p % 12], p // 12 - 1)


def hex_rgb(rgb) -> str:
    return "#%02x%02x%02x" % tuple(rgb)


def _now_iso() -> str:
    return _dt.datetime.now().replace(microsecond=0).isoformat()


def _write_json(path: str, data) -> None:
    """Write-then-rename: a crash mid-write must not leave settings truncated."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)
    os.replace(tmp, path)


def _read_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def filter_hands(score: LightScore, hands: str) -> LightScore:
    """The score as one hand would practise it.  `hands` is "both", "L" or "R"."""
    if hands == "both":
        return score
    notes = tuple(n for n in score.notes if n.h == hands)
    if not notes:
        raise LightScoreError("this score has no %s-hand notes"
                              % ("left" if hands == "L" else "right"))
    return LightScore(meta=score.meta, tempo=score.tempo, notes=notes,
                      events=group_events(notes))


def beat_at(t_ms: float, tempo) -> float | None:
    """Beats elapsed at `t_ms` under the score's tempo map, or None without one."""
    segs = sorted((int(s["t"]), float(s["bpm"])) for s in tempo or () if s.get("bpm"))
    if not segs:
        return None
    if segs[0][0] > 0:                      # no entry at 0: the first tempo holds from 0
        segs.insert(0, (0, segs[0][1]))
    beats = 0.0
    for i, (t0, bpm) in enumerate(segs):
        t1 = segs[i + 1][0] if i + 1 < len(segs) else float("inf")
        if t_ms <= t0:
            break
        beats += (min(t_ms, t1) - t0) / 60000.0 * bpm
    return beats


# =============================================================================== library


@dataclass
class LibraryEntry:
    id: str
    path: str
    title: str
    status: str                 # "ready" | "invalid" | "source"
    detail: str = ""
    events: int = 0
    notes: int = 0
    hands: str = ""             # "L+R", "R", "L"
    duration_s: float = 0.0
    fingering: str = ""


def scan_library(dirs: list[tuple[str, str]]) -> list[LibraryEntry]:
    """Every light-score (and every uncompiled source) in `dirs`, as (label, path)."""
    out: list[LibraryEntry] = []
    for label, d in dirs:
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            path = os.path.join(d, fn)
            low = fn.lower()
            if low.endswith(SCORE_SUFFIX):
                sid = "%s/%s" % (label, fn[: -len(SCORE_SUFFIX)])
                try:
                    sc = load_score(path)
                except (OSError, ValueError, LightScoreError) as exc:
                    out.append(LibraryEntry(sid, path, fn, "invalid", str(exc)))
                    continue
                hs = sorted({n.h for n in sc.notes})
                out.append(LibraryEntry(
                    id=sid, path=path,
                    title=str(sc.meta.get("title") or fn[: -len(SCORE_SUFFIX)]),
                    status="ready", events=len(sc.events), notes=len(sc.notes),
                    hands="L+R" if hs == ["L", "R"] else hs[0],
                    duration_s=round(sc.duration_ms / 1000.0, 1),
                    fingering=str(sc.meta.get("fingering") or ""),
                ))
            elif low.endswith(SOURCE_SUFFIXES):
                out.append(LibraryEntry(
                    "%s/%s" % (label, fn), path, fn, "source",
                    "not compiled -- run the score compiler on the PC"))
    return out


# =============================================================================== attempt


@dataclass
class EventRecord:
    """What happened at one event, accumulated over loop passes."""

    wrong: list[int] = field(default_factory=list)      # pitches struck instead
    missed: list[int] = field(default_factory=list)     # pitches never played (along)
    secs: list[float] = field(default_factory=list)     # time-to-complete, wait mode
    skipped: int = 0


@dataclass
class Attempt:
    number: int
    score_id: str
    title: str
    hands: str
    mode: str
    tempo: int                                  # percent, play-along only
    score: LightScore
    session: PracticeSession
    running: bool
    started_wall: str
    onsets: list[int]
    loop: tuple[int, int] | None = None
    loop_pending: int | None = None             # A set, waiting for B
    loop_passes: int = 0
    clock_ms: float = 0.0
    last_tick: float = 0.0
    event_started: float = 0.0
    paused_at: float | None = None
    credited: int = 0
    missed: int = 0
    skipped: int = 0
    records: dict[int, EventRecord] = field(default_factory=dict)
    lo: int = 0                                 # first event index this attempt covered
    hi: int = 0                                 # last event index it reached

    def rec(self, i: int) -> EventRecord:
        return self.records.setdefault(i, EventRecord())


# ================================================================================ engine


class Engine:
    def __init__(self, home: str, score_dirs: list[tuple[str, str]], led_sink=None,
                 strip: StripSettings | None = None, clock=time.monotonic):
        self.home = home
        self.score_dirs = list(score_dirs)
        self.strip = strip or StripSettings.load()
        self.led_sink = led_sink
        self.clock = clock
        self._lock = threading.RLock()
        self._changed = threading.Condition(self._lock)
        self.version = 0

        self.settings_path = os.path.join(home, "settings.json")
        self.history_path = os.path.join(home, "history.json")
        saved = _read_json(self.settings_path, {})
        self.settings = dict(DEFAULT_SETTINGS)
        for k, v in saved.items():
            if k in DEFAULT_SETTINGS:
                try:
                    self.settings[k] = self._validate(k, v)
                except ValueError:
                    pass
        self.history: dict[str, list[dict]] = _read_json(self.history_path, {})

        self.library: list[LibraryEntry] = []
        self.attempt: Attempt | None = None
        self.summary: dict | None = None
        self.error: str | None = None
        self.held: set[int] = set()
        self.inputs: dict[str, dict] = {
            "midi": {"state": "unavailable", "detail": "not started"},
            "mic": {"state": "unavailable", "detail": "not started"},
        }
        self._pixels: dict[int, tuple[int, int, int]] = {}
        self._attempt_no = 0
        self.rescan()

    # ------------------------------------------------------------------ plumbing

    def _bump(self) -> None:
        self.version += 1
        self._render_leds()
        self._changed.notify_all()

    def wait_for_change(self, seen: int, timeout: float) -> int:
        with self._changed:
            self._changed.wait_for(lambda: self.version != seen, timeout=timeout)
            return self.version

    def _render_leds(self) -> None:
        a = self.attempt
        pixels = a.session.frame().pixels if (a and not a.session.finished) else {}
        if pixels == self._pixels:
            return
        self._pixels = dict(pixels)
        if self.led_sink is not None:
            try:
                self.led_sink.show(self._pixels, self.settings["brightness"])
            except Exception as exc:  # a dead strip must not take the UI down with it
                self.error = "LED output failed: %s" % exc

    # ------------------------------------------------------------------ settings

    @staticmethod
    def _validate(key: str, value):
        if key == "mode" and value in ("wait", "along"):
            return value
        if key == "palette" and value in colors.PALETTES:
            return value
        if key == "brightness":
            return round(min(1.0, max(0.05, float(value))), 2)
        if key == "lookahead":
            return min(3, max(0, int(value)))
        if key == "input" and value in ("midi", "mic"):
            return value
        if key == "hands" and value in ("both", "L", "R"):
            return value
        if key == "led_order" and value in ("GRB", "RGB", "BRG", "RBG", "GBR", "BGR"):
            return value
        if key == "led_one_pattern" and value in ("1110", "1100"):
            return value
        raise ValueError("bad value for %s: %r" % (key, value))

    def set_setting(self, key: str, value) -> None:
        with self._lock:
            if key not in DEFAULT_SETTINGS:
                raise ValueError("unknown setting %r" % key)
            self.settings[key] = self._validate(key, value)
            _write_json(self.settings_path, self.settings)
            a = self.attempt
            if a is not None:
                if key == "palette":
                    a.session.palette = self.settings["palette"]
                elif key == "lookahead":
                    a.session.preview = self.settings["lookahead"]
                elif key == "mode" and a.mode != value:
                    a.mode = value
                    a.running = value == "wait"
                    self._rewind_clock(a)
            if key == "brightness" and self.led_sink is not None:
                self._pixels = {}           # force a re-send at the new level
            self._bump()

    def set_input_status(self, name: str, state: str, detail: str = "") -> None:
        with self._lock:
            new = {"state": state, "detail": detail}
            if self.inputs.get(name) != new:
                self.inputs[name] = new
                self._bump()

    # ------------------------------------------------------------------- library

    def rescan(self) -> None:
        with self._lock:
            self.library = scan_library(self.score_dirs)
            self._bump()

    def _entry(self, score_id: str) -> LibraryEntry:
        for e in self.library:
            if e.id == score_id:
                return e
        raise KeyError("no score %r" % score_id)

    # ------------------------------------------------------------------ attempts

    def open(self, score_id: str, hands: str | None = None, tempo: int | None = None,
             loop: tuple[int, int] | None = None) -> None:
        with self._lock:
            entry = self._entry(score_id)
            if entry.status != "ready":
                raise LightScoreError("%s: %s" % (entry.title, entry.detail))
            hands = hands or self.settings["hands"]
            score = filter_hands(load_score(entry.path), hands)
            session = PracticeSession(score, self.strip, preview=self.settings["lookahead"],
                                      palette=self.settings["palette"])
            prev = self.history.get(score_id, [])
            self._attempt_no = len(prev) + 1
            mode = self.settings["mode"]
            now = self.clock()
            a = Attempt(
                number=self._attempt_no, score_id=score_id, title=entry.title,
                hands=hands, mode=mode, tempo=tempo or 100, score=score,
                session=session, running=(mode == "wait"), started_wall=_now_iso(),
                onsets=[e.t for e in score.events], last_tick=now, event_started=now,
            )
            n = len(score.events)
            if loop is not None:
                lo, hi = sorted((max(0, loop[0]), min(n - 1, loop[1])))
                a.loop = (lo, hi)
                session.seek(lo)
            a.lo = a.hi = session.index
            self.attempt = a
            self.summary = None
            self.error = None
            self._rewind_clock(a)
            self._bump()

    def retry(self, tempo_delta: int = 0, loop: tuple[int, int] | None = None) -> None:
        """Try again: same score, hands and loop, optionally slower or a new loop."""
        with self._lock:
            src = self.attempt or None
            sid = src.score_id if src else (self.summary or {}).get("score_id")
            if sid is None:
                raise LightScoreError("nothing to try again")
            hands = src.hands if src else self.summary["hands"]
            tempo = (src.tempo if src else self.summary["tempo"]) + tempo_delta
            tempo = min(150, max(30, tempo))
            if loop is None and src is not None:
                loop = src.loop
            elif loop is None and self.summary:
                lp = self.summary.get("loop")
                loop = tuple(lp) if lp else None
        self.open(sid, hands=hands, tempo=tempo, loop=loop)

    def _rewind_clock(self, a: Attempt) -> None:
        """Play-along: put the clock one lead-in before the cursor's event."""
        i = min(a.session.index, len(a.onsets) - 1)
        a.clock_ms = a.onsets[i] - LEAD_IN_MS * a.tempo / 100.0
        a.last_tick = self.clock()
        a.event_started = a.last_tick

    def _due_end(self, a: Attempt, i: int) -> float:
        """Score time at which event `i` stops being "now" in play-along."""
        if i + 1 < len(a.onsets):
            return a.onsets[i + 1]
        ev = a.score.events[i]
        return max(max(n.end for n in ev.notes), ev.t + MIN_LAST_EVENT_MS)

    # ----------------------------------------------------------------- transport

    def transport(self, cmd: str, arg=None) -> None:
        with self._lock:
            a = self.attempt
            if a is None:
                raise LightScoreError("no score open")
            s = a.session
            now = self.clock()
            if cmd in ("play", "pause", "toggle"):
                want = {"play": True, "pause": False}.get(cmd, not a.running)
                if want and not a.running:
                    if a.paused_at is not None:
                        a.event_started += now - a.paused_at
                    a.paused_at = None
                    a.last_tick = now
                elif not want and a.running:
                    a.paused_at = now
                a.running = want
            elif cmd == "prev":
                floor = a.loop[0] if a.loop else 0
                s.seek(max(floor, s.index - 1))
                self._rewind_clock(a)
            elif cmd == "next":
                if not s.finished:
                    ev = s.current
                    a.rec(s.index).skipped += 1
                    a.skipped += len(ev.pitches)
                    s.skip()
                    a.event_started = now
                    self._after_move(a)
            elif cmd == "loop":
                self._loop_button(a)
            elif cmd == "loop_spot":
                i = int(arg if arg is not None else s.index)
                n = len(a.score.events)
                a.loop = (max(0, i - 2), min(n - 1, i + 2))
                a.loop_pending = None
                s.seek(a.loop[0])
                self._rewind_clock(a)
            elif cmd == "tempo":
                a.tempo = min(150, max(30, a.tempo + int(arg or 0)))
                if not a.running:
                    self._rewind_clock(a)       # the lead-in is one second at any tempo
            elif cmd == "stop":
                self._finish(a, stopped=True)
            else:
                raise ValueError("unknown transport command %r" % cmd)
            self._bump()

    def _loop_button(self, a: Attempt) -> None:
        """One button, three taps: set A here; set B here and loop; clear."""
        s = a.session
        here = min(s.index, len(a.score.events) - 1)
        if a.loop is not None:
            a.loop = None
            a.loop_pending = None
        elif a.loop_pending is None:
            a.loop_pending = here
        else:
            lo, hi = sorted((a.loop_pending, here))
            a.loop = (lo, hi)
            a.loop_pending = None
            s.seek(lo)
            self._rewind_clock(a)

    # -------------------------------------------------------------------- input

    def note(self, pitch: int, on: bool, source: str = "keys") -> bool | None:
        """Feed one note from any input.  Returns whether a note-on was correct."""
        with self._lock:
            pitch = int(pitch)
            if not 21 <= pitch <= 108:
                return None
            if not on:
                self.held.discard(pitch)
                if self.attempt is not None:
                    self.attempt.session.note_off(pitch)
                self._bump()
                return None
            self.held.add(pitch)
            a = self.attempt
            result = None
            if a is not None and not a.session.finished:
                if not a.running and a.mode == "wait":
                    self.transport("play")      # playing in wait mode means "go"
                if a.running:
                    result = self._note_on(a, pitch)
            self._bump()
            return result

    def _note_on(self, a: Attempt, pitch: int) -> bool:
        s = a.session
        before, n_wrong = s.index, len(s.wrong_notes)
        ok = s.note_on(pitch)
        if len(s.wrong_notes) > n_wrong:
            a.rec(before).wrong.append(pitch)
        if s.index != before:
            now = self.clock()
            a.credited += len(a.score.events[before].pitches)
            if a.mode == "wait":
                a.rec(before).secs.append(now - a.event_started)
            a.event_started = now
            self._after_move(a)
        return ok

    def autoplay(self) -> None:
        """Preview helper: play the current event correctly, as a piano would."""
        with self._lock:
            a = self.attempt
            if a is None or a.session.finished:
                return
            for p in sorted(a.session.waiting_for):
                self.note(p, True, "auto")
            for p in list(self.held):
                self.note(p, False, "auto")

    def _after_move(self, a: Attempt) -> None:
        s = a.session
        a.hi = max(a.hi, min(s.index, len(a.score.events) - 1))
        if a.loop is not None and s.index > a.loop[1]:
            a.loop_passes += 1
            s.seek(a.loop[0])
            self._rewind_clock(a)
        elif s.finished:
            self._finish(a, stopped=False)

    # -------------------------------------------------------------------- clock

    def tick(self) -> None:
        """Advance the play-along clock.  Called ~50x a second by the server."""
        with self._lock:
            a = self.attempt
            if a is None or a.mode != "along" or not a.running or a.session.finished:
                return
            now = self.clock()
            a.clock_ms += (now - a.last_tick) * 1000.0 * a.tempo / 100.0
            a.last_tick = now
            moved = False
            for _ in range(len(a.onsets) + 1):      # bounded: one pass per event at most
                s = a.session
                if s.finished or not a.running or a.clock_ms < self._due_end(a, s.index):
                    break
                i, ev = s.index, s.current
                done = ev.pitches - s.waiting_for
                gone = sorted(ev.pitches - done)
                a.credited += len(done)
                a.missed += len(gone)
                a.rec(i).missed.extend(gone)
                s.seek(i + 1)
                a.event_started = now
                self._after_move(a)
                moved = True
                if a.loop is not None and s.index == a.loop[0]:
                    break                           # wrapped: the clock was rewound
            if moved:
                self._bump()

    # ------------------------------------------------------------------- results

    def _finish(self, a: Attempt, stopped: bool) -> None:
        a.running = False
        sc = a.score
        wrong = sum(len(r.wrong) for r in a.records.values())
        denom = a.credited + wrong + a.missed
        acc = round(100.0 * a.credited / denom) if denom else None

        times = [t for r in a.records.values() for t in r.secs]
        med = statistics.median(times) if times else 0.0
        limit = max(HESITATION_MIN_S, HESITATION_X_MEDIAN * med)
        problems = []
        hesitations = 0
        for i in sorted(a.records):
            r = a.records[i]
            ev = sc.events[i]
            lead = ev.notes[-1] if ev.notes[-1].h == "R" else ev.notes[0]
            where = self._where(sc, ev.t)
            slow = max(r.secs) if r.secs else 0.0
            if slow > limit:
                hesitations += 1
                problems.append(self._problem(i, ev, lead, where, "hesitated",
                                              "%.1f s" % slow))
            for p in sorted(set(r.wrong)):
                problems.append(self._problem(i, ev, lead, where, "wrong key",
                                              "played %s" % note_name(p)))
            if r.skipped:
                problems.append(self._problem(i, ev, lead, where, "skipped",
                                              "passed over"))
            if r.missed:
                gone = sorted(set(r.missed))
                problems.append(self._problem(
                    i, ev, next((n for n in ev.notes if n.p == gone[0]), lead), where,
                    "missed", "not played: " + " ".join(note_name(p) for p in gone)))

        prev = self.history.get(a.score_id, [])
        last_acc = prev[-1]["accuracy"] if prev else None
        summary = {
            "id": "%s#%d#%s" % (a.score_id, a.number, a.started_wall),
            "score_id": a.score_id, "title": a.title, "attempt": a.number,
            "hands": a.hands, "mode": a.mode, "tempo": a.tempo,
            "loop": list(a.loop) if a.loop else None, "loop_passes": a.loop_passes,
            "stopped": stopped, "at": _now_iso(),
            "accuracy": acc, "credited": a.credited, "wrong": wrong,
            "missed": a.missed, "skipped": a.skipped, "hesitations": hesitations,
            "hesitation_limit_s": round(limit, 2),
            "events_from": a.lo, "events_to": a.hi, "events_total": len(sc.events),
            "delta": (acc - last_acc) if (acc is not None and last_acc is not None)
            else None,
            "problems": problems,
        }
        self.summary = summary
        if a.credited or wrong or a.missed:
            rows = prev + [{k: summary[k] for k in (
                "at", "attempt", "accuracy", "credited", "wrong", "missed",
                "hesitations", "mode", "tempo", "hands", "loop", "stopped")}]
            self.history[a.score_id] = rows[-50:]
            _write_json(self.history_path, self.history)

    def _where(self, sc: LightScore, t: int) -> str:
        b = beat_at(t, sc.tempo)
        return ("beat %d" % (int(b) + 1)) if b is not None else ("%.1f s" % (t / 1000))

    @staticmethod
    def _problem(i, ev, note, where, kind, detail) -> dict:
        return {"event": i, "note": note_name(note.p), "finger": note.f,
                "finger_name": FINGER_NAMES[note.f], "hand": note.h,
                "where": where, "kind": kind, "detail": detail}

    # --------------------------------------------------------------------- state

    def state(self) -> dict:
        """Everything the screens draw, as one JSON-able snapshot."""
        with self._lock:
            st = self.settings
            led_n = self.strip.led_count
            return {
                "version": self.version,
                "settings": dict(st),
                "palettes": {k: [hex_rgb(v[f]) for f in range(1, 6)]
                             for k, v in colors.PALETTES.items()},
                "hand_colors": {h: hex_rgb(c) for h, c in colors.HAND_COLORS.items()},
                "inputs": self.inputs,
                "led": {
                    "count": led_n,
                    "sink": getattr(self.led_sink, "describe", lambda: "none")(),
                    "pixels": [[i, *rgb] for i, rgb in sorted(self._pixels.items())],
                    "amps_cap": round(led_n * AMPS_PER_LED_WHITE * st["brightness"], 1),
                    "amps_full": round(led_n * AMPS_PER_LED_WHITE, 1),
                },
                "held": sorted(self.held),
                "practice": self._practice_state(),
                "summary": self.summary,
                "error": self.error,
                "library": [self._entry_state(e) for e in self.library],
            }

    def _entry_state(self, e: LibraryEntry) -> dict:
        hist = self.history.get(e.id, [])
        last = hist[-1] if hist else None
        return {
            "id": e.id, "title": e.title, "status": e.status, "detail": e.detail,
            "events": e.events, "notes": e.notes, "hands": e.hands,
            "duration_s": e.duration_s, "fingering": e.fingering,
            "attempts": len(hist),
            "last_at": last["at"] if last else None,
            "last_accuracy": last["accuracy"] if last else None,
            "best_accuracy": max((h["accuracy"] for h in hist
                                  if h["accuracy"] is not None), default=None),
        }

    def _practice_state(self) -> dict | None:
        a = self.attempt
        if a is None:
            return None
        s = a.session
        sc = a.score
        n = len(sc.events)
        done, total = s.progress()

        def chips(i, with_state):
            ev = sc.events[i]
            waiting = s.waiting_for if with_state else frozenset()
            return [{"note": note_name(nt.p), "pitch": nt.p, "hand": nt.h,
                     "finger": nt.f, "finger_name": FINGER_NAMES[nt.f],
                     "waiting": nt.p in waiting,
                     "done": with_state and nt.p not in waiting}
                    for nt in ev.notes]

        now_chips = chips(s.index, True) if not s.finished else []
        nxt = [chips(i, False) for i in range(s.index + 1, min(n, s.index + 3))]
        hands_now = sorted({c["hand"] for c in now_chips})
        wrong = sum(len(r.wrong) for r in a.records.values())
        denom = a.credited + wrong + a.missed
        t_now = sc.events[min(s.index, n - 1)].t
        beat = beat_at(t_now, sc.tempo)
        return {
            "attempt": a.number, "score_id": a.score_id, "title": a.title,
            "hands": a.hands, "mode": a.mode, "tempo": a.tempo,
            "running": a.running, "finished": s.finished,
            "index": s.index, "total": total, "done": done,
            "now": now_chips, "now_hands": hands_now, "next": nxt,
            "accuracy": round(100.0 * a.credited / denom) if denom else None,
            "wrong": wrong, "missed": a.missed, "skipped": a.skipped,
            "beat": None if beat is None else int(beat) + 1,
            "time_s": round(t_now / 1000.0, 1),
            "duration_s": round(sc.duration_ms / 1000.0, 1),
            "clock_s": round(a.clock_ms / 1000.0, 2) if a.mode == "along" else None,
            "loop": list(a.loop) if a.loop else None,
            "loop_pending": a.loop_pending, "loop_passes": a.loop_passes,
        }
