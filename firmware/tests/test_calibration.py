"""Tests for calibration mode (D-18).

The property that matters is not "it produces a number" but that it produces the
**right kind** of correction: a bar that is glued slightly off must be diagnosed as a
translation (fixable with `shift`), and a bad M1 must be diagnosed as a scale error
(NOT fixable with `shift`).  Confusing the two is the failure that would waste a
measuring session, so each is tested by *synthesising* that exact fault.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fingerlight.calibration import (  # noqa: E402
    DEFAULT_ANCHORS,
    NEGLIGIBLE_MM,
    Anchor,
    CalibrationSession,
    load_key_centres,
    solve,
)
from fingerlight.notemap import StripSettings, get_note_position  # noqa: E402


@pytest.fixture(scope="module")
def settings():
    return StripSettings.load()


@pytest.fixture(scope="module")
def centres():
    return load_key_centres()


# ------------------------------------------------------------------ the session


def test_session_walks_the_three_anchors(settings):
    s = CalibrationSession(settings)
    seen = []
    while not s.finished:
        seen.append((s.current_note, s.current_led()))
        s.confirm(s.current_note)          # player confirms the map is right
    assert [n for n, _ in seen] == list(DEFAULT_ANCHORS)
    assert all(led is not None for _, led in seen)
    assert s.finished and len(s.responses) == 3


def test_led_lit_is_the_one_the_map_predicts(settings):
    s = CalibrationSession(settings)
    assert s.current_led() == get_note_position(s.current_note, settings)


def test_confirming_after_the_end_raises(settings):
    s = CalibrationSession(settings)
    for _ in DEFAULT_ANCHORS:
        s.confirm(s.current_note)
    with pytest.raises(RuntimeError):
        s.confirm(60)


def test_reset_clears(settings):
    s = CalibrationSession(settings)
    s.confirm(s.current_note)
    s.reset()
    assert s.index == 0 and s.responses == []


# ------------------------------------------------------- a perfectly aligned bar


def test_perfect_alignment_reports_no_change_needed(settings, centres):
    s = CalibrationSession(settings)
    while not s.finished:
        s.confirm(s.current_note)          # every anchor exactly right
    c = s.solve(centres)
    assert abs(c.translation_mm) < 0.01
    assert abs(c.scale_error) < 1e-9
    assert not c.needs_remeasure
    assert "good" in c.verdict.lower()


# --------------------------------------------------- synthesising the two faults
#
# A0 and C8 are the physical ENDS of the keyboard, so a uniform rightward translation
# has nowhere to land at C8 -- the response clamps and a pure translation then looks
# like a scale error.  The synthetic-fault tests therefore use interior anchors, which
# have room on both sides.  (This is a real property of the method, not a test
# convenience: see `test_translation_at_the_board_ends_cannot_be_synthesised`.)
INTERIOR_ANCHORS = (36, 60, 84)   # C2, C4, C6


def _nearest_note(centres, target_mm):
    return min(centres, key=lambda n: abs(centres[n] - target_mm))


def _responses(settings, centres, anchors, displace):
    """Build responses where the lit LED actually sits `displace(mm)` away."""
    out = []
    for note in anchors:
        actual = _nearest_note(centres, displace(centres[note]))
        out.append(
            Anchor(led=get_note_position(note, settings), expected_note=note,
                   actual_note=actual)
        )
    return out


def test_uniform_offset_is_diagnosed_as_translation(settings, centres):
    """Every anchor displaced by the SAME distance => the bar is glued off-centre.

    Note this is a *constant mm* offset, not a constant number of semitones: a
    semitone is a different distance at different points on the board.
    """
    responses = _responses(settings, centres, INTERIOR_ANCHORS, lambda mm: mm + 30.0)
    c = solve(responses, settings, centres)
    assert abs(c.scale_error) < 0.005, (
        "a pure offset must NOT be reported as a scale error (got %+.4f)" % c.scale_error
    )
    assert c.translation_mm > NEGLIGIBLE_MM
    assert not c.needs_remeasure, "a translation must not demand re-measuring M1"
    assert c.suggested_shift != 0
    assert "TRANSLATION" in c.verdict


def test_progressive_error_is_diagnosed_as_scale(settings, centres):
    """Error growing with position => M1 is wrong; a shift cannot fix it."""
    responses = _responses(settings, centres, INTERIOR_ANCHORS, lambda mm: mm * 1.03)
    c = solve(responses, settings, centres)
    assert c.scale_error > 0.005, (
        "expected a clear positive scale error, got %+.4f" % c.scale_error
    )
    assert c.needs_remeasure, "a scale error must demand re-measuring, not a shift"
    assert c.suggested_octave_span_mm is not None
    assert c.suggested_octave_span_mm > settings.octave_span_mm
    assert "SCALE" in c.verdict
    assert "shift cannot fix" in c.verdict


def test_the_two_faults_are_actually_distinguishable(settings, centres):
    """The whole point of three anchors.  If this fails, calibration is worthless."""
    translated = solve(
        _responses(settings, centres, INTERIOR_ANCHORS, lambda mm: mm + 30.0),
        settings, centres,
    )
    scaled = solve(
        _responses(settings, centres, INTERIOR_ANCHORS, lambda mm: mm * 1.03),
        settings, centres,
    )
    assert translated.needs_remeasure is False
    assert scaled.needs_remeasure is True
    assert abs(scaled.scale_error) > abs(translated.scale_error) * 3, (
        "translation %+.4f vs scale %+.4f are not separated"
        % (translated.scale_error, scaled.scale_error)
    )


def test_translation_at_the_board_ends_cannot_be_synthesised(settings, centres):
    """Documents a real limitation rather than hiding it.

    C8 is the top key, so a rightward offset has nowhere to go: the nearest-key
    response clamps, the top anchor shows zero error, and a pure translation is
    misread as a scale error.  Calibration anchors should therefore not sit hard
    against the ends if translation is what you are trying to measure.
    """
    top = _nearest_note(centres, centres[108] + 30.0)
    assert top == 108, "expected the top anchor to clamp"
    clamped = solve(
        _responses(settings, centres, DEFAULT_ANCHORS, lambda mm: mm + 30.0),
        settings, centres,
    )
    assert clamped.needs_remeasure, (
        "clamping at the ends should show up as an apparent scale error -- if this "
        "ever stops being true, the limitation note above needs revisiting"
    )


def test_quantisation_floor_of_the_method(settings, centres):
    """A player can only press a whole key, so a displacement smaller than half a key
    is invisible.  Worth knowing before chasing a 2 mm discrepancy."""
    tiny = _responses(settings, centres, INTERIOR_ANCHORS, lambda mm: mm + 2.0)
    assert all(r.actual_note == r.expected_note for r in tiny), (
        "a 2 mm offset should be undetectable by key-press confirmation"
    )
    c = solve(tiny, settings, centres)
    assert not c.needs_remeasure
    assert "good" in c.verdict.lower()


# ------------------------------------------------------------------ guard rails


def test_two_anchors_minimum(settings, centres):
    with pytest.raises(ValueError):
        solve([Anchor(led=0, expected_note=21, actual_note=21)], settings, centres)


def test_anchors_at_one_spot_are_rejected(settings, centres):
    same = [Anchor(led=0, expected_note=60, actual_note=60) for _ in range(3)]
    with pytest.raises(ValueError, match="spread them out"):
        solve(same, settings, centres)


def test_unknown_note_is_rejected(settings, centres):
    bad = [
        Anchor(led=0, expected_note=21, actual_note=21),
        Anchor(led=1, expected_note=60, actual_note=200),   # not an 88-key note
    ]
    with pytest.raises(ValueError):
        solve(bad, settings, centres)


def test_residuals_expose_a_mis_entered_response(settings, centres):
    """A fat-fingered confirmation does not fit the linear model; the residuals show it."""
    good = [
        Anchor(led=get_note_position(n, settings), expected_note=n, actual_note=n)
        for n in DEFAULT_ANCHORS
    ]
    clean = solve(good, settings, centres)
    assert max(abs(r) for r in clean.residuals_mm) < 0.01

    good[1] = Anchor(led=good[1].led, expected_note=60, actual_note=79)  # way off
    messy = solve(good, settings, centres)
    assert max(abs(r) for r in messy.residuals_mm) > 10.0, (
        "a wildly inconsistent anchor should leave a large residual"
    )
