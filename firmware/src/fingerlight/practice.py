"""The practice state machine (B3).

Wait-for-it practice: the bar lights the next event, the player plays it, the bar
advances.  Nothing is time-driven -- the score's timestamps order the events but do not
pace them, because the whole point is that a sight-reader sets their own pace.

What this module is careful about
---------------------------------
* **Chords advance as a unit.** An event is every note sharing an onset; the cursor moves
  only when all of its pitches are held.  Partial chords stay lit.
* **Extra notes are wrong, but not fatal.** Hitting a note that is not in the current
  event is reported as an error and does not advance the cursor.  Practice tools that
  hard-stop on a fumble are miserable to use.
* **Preview is a parameter, not a decision.** How far ahead to show upcoming notes is
  the project's open question Q-06.  `preview` defaults to 1 and the renderer dims
  previewed events; the right number is a UX call to be made on real hardware.

Pure logic: no MIDI, no LEDs, no threads, no clock.  It consumes note-on/note-off events
and emits a frame describing what the bar should show.  Tested on a PC.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .colors import pair_colors, scale
from .lightscore import Event, LightScore
from .notemap import StripSettings, note_to_leds

#: How much to dim a previewed (not-yet-current) event relative to the current one.
#: A guess pending Q-06; low enough that "now" is unmistakable.
PREVIEW_BRIGHTNESS = 0.18


@dataclass(frozen=True)
class Frame:
    """What the bar should display right now.

    `pixels` maps LED index -> RGB.  A dict rather than a full 176-entry list because the
    caller usually wants to clear-and-set only what changed.
    """

    index: int                      # cursor position, in events
    pixels: dict[int, tuple[int, int, int]]
    waiting_for: frozenset[int]     # pitches still needed to advance
    finished: bool = False


@dataclass
class PracticeSession:
    """Cursor over a light-score, driven by what the player actually plays."""

    score: LightScore
    settings: StripSettings
    preview: int = 1
    palette: str | None = None

    index: int = 0
    held: set[int] = field(default_factory=set)
    errors: int = 0
    wrong_notes: list[tuple[int, int]] = field(default_factory=list)  # (index, pitch)
    _satisfied: set[int] = field(default_factory=set)

    # ------------------------------------------------------------------ properties

    @property
    def finished(self) -> bool:
        return self.index >= len(self.score.events)

    @property
    def current(self) -> Event | None:
        return None if self.finished else self.score.events[self.index]

    @property
    def waiting_for(self) -> frozenset[int]:
        """Pitches of the current event not yet accounted for."""
        cur = self.current
        if cur is None:
            return frozenset()
        return frozenset(cur.pitches - self._satisfied)

    # --------------------------------------------------------------------- driving

    def note_on(self, pitch: int) -> bool:
        """Feed a note-on.  Returns True if it was a *correct* note.

        A pitch is credited once per event, so a repeated strike of a held chord tone is
        neither an error nor progress.
        """
        self.held.add(pitch)
        cur = self.current
        if cur is None:
            return False
        if pitch in cur.pitches:
            self._satisfied.add(pitch)
            if cur.pitches <= self._satisfied:
                self._advance()
            return True
        self.errors += 1
        self.wrong_notes.append((self.index, pitch))
        return False

    def note_off(self, pitch: int) -> None:
        """Feed a note-off.

        Releasing does **not** un-satisfy an event: the player may roll a chord and
        release the first note before striking the last, which is normal playing, not a
        mistake.
        """
        self.held.discard(pitch)

    def _advance(self) -> None:
        self.index += 1
        self._satisfied.clear()

    def skip(self) -> None:
        """Move on without playing it -- for a passage the player wants to pass."""
        if not self.finished:
            self._advance()

    def seek(self, index: int) -> None:
        """Jump the cursor to event `index` (clamped to 0..len), keeping the error tally.

        For loops and "back one event": the app moves the cursor, the player keeps the
        record of how the attempt went.  Partial credit on the old event is dropped.
        """
        self.index = max(0, min(int(index), len(self.score.events)))
        self._satisfied.clear()

    def reset(self) -> None:
        self.index = 0
        self.held.clear()
        self._satisfied.clear()
        self.errors = 0
        self.wrong_notes.clear()

    # -------------------------------------------------------------------- rendering

    def frame(self) -> Frame:
        """Build the frame for the current cursor position.

        Later (nearer-future) events are drawn first and the current event last, so that
        where a preview pixel and a current pixel collide -- which they will, since
        adjacent keys share LEDs at 144 LED/m -- **the current event wins**.
        """
        pixels: dict[int, tuple[int, int, int]] = {}
        if self.finished:
            return Frame(index=self.index, pixels=pixels, waiting_for=frozenset(),
                         finished=True)

        upcoming = range(
            min(self.index + self.preview, len(self.score.events) - 1),
            self.index - 1,
            -1,
        )
        for i in upcoming:
            is_current = i == self.index
            bright = 1.0 if is_current else PREVIEW_BRIGHTNESS
            for note in self.score.events[i].notes:
                # A satisfied note of the current event dims: it is done, but the rest of
                # the chord is still waiting, so it must not simply vanish.
                if is_current and note.p in self._satisfied:
                    bright = 0.30
                elif is_current:
                    bright = 1.0
                lo, hi = note_to_leds(note.p, self.settings)
                hand_px, finger_px = pair_colors(
                    note.h, note.f, note.p, brightness=bright, palette=self.palette
                )
                pixels[lo] = hand_px
                pixels[hi] = finger_px

        return Frame(
            index=self.index,
            pixels=pixels,
            waiting_for=self.waiting_for,
            finished=False,
        )

    # ---------------------------------------------------------------------- progress

    def progress(self) -> tuple[int, int]:
        """(events completed, total events)."""
        return (min(self.index, len(self.score.events)), len(self.score.events))
