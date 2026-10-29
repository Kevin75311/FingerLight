"""Continuous (weighted) note -> LED model, and the light field a diffuser shows.

This is a **sim-side prototype**. It does not change `data/led_map.csv`, the
firmware, or the settings overlay -- it reads the same geometry those are built
from (`data/led_map.json`: octave span, tape origin, pitch, LED count) and
proposes a different way to drive the strip, so the two can be compared before
anything is committed.

The idea
--------
Stop thinking of the tape as 176 addressable dots and treat it as a continuous
line. A key occupies a slot on that line -- 13.73 mm for C1..B7, 15.70 mm for
A0..B0, 23.54 mm for C8, against a 6.9444 mm LED pitch. Lighting that key means
reproducing its slot as an intensity profile, which lands on 2-4 LEDs with the
middle one brightest.

`weights(midi)` convolves the key's slot with a Gaussian of `SIGMA_MM` (the
diffuser's blur) and samples it at each LED centre, normalised so the peak is 1.
`SIGMA_MM = 3.5` corresponds to the 6-10 mm LED-to-diffuser gap the hardware
note recommends for clean per-key blobs.

Two things are modelled because they, not the algorithm, decide whether this
looks continuous on real hardware:

* **gamma** -- a WS2812's PWM is linear in duty, the eye is not. Weights are
  raised to 1/GAMMA... no: they are raised to GAMMA when converted to PWM, which
  is the direction that makes a 0.5 weight *look* half as bright.
* **8-bit quantisation** -- after gamma a 0.20 weight is PWM 7 of 255, where a
  WS2812 steps visibly and shifts hue. `quantise()` returns the value actually
  sent, so the sim shows the stepped edge instead of a mathematically smooth one.

The diffuser
------------
`field(x, active)` is the light the diffuser surface shows: every driven LED
contributes a Gaussian of the same sigma, summed and clipped. That is what the
eye sees through the profile, and it is what `make_scene.py`'s diffuser segments
are coloured with -- so the sim shows a continuous glow rather than dots under a
lid. It is a visual model, not photometry: shape and relative brightness are
meaningful, absolute brightness is not.
"""

from __future__ import annotations

import json
import math
import os

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# --- optics ------------------------------------------------------------------
# The blur is not a free parameter: it comes from how far the diffusing surface
# sits above the emitters. SIGMA_PER_GAP is the modelling assumption -- a thin
# scattering screen at distance d spreads a point source by roughly 0.45 d for
# the profiles this design uses. It is NOT measured from the part Kevin bought;
# one photo of a single lit key will let us fit it, and everything here is a
# relative comparison that survives a wrong coefficient.
# The gap is set by continuity, not by taste: `ripple()` drives every LED
# equally and measures how much the sum still undulates -- i.e. whether the dots
# show through. Measured on this model: 4 mm -> 69%, 5 mm -> 39%, 6 mm -> 12%,
# 7 mm -> 0.0%. That is what the 6-10 mm recommendation in docs/hardware.md is
# really about, and it means the blob cannot be narrowed by lowering the cap.
DIFFUSER_GAP_MM = 7.0
SIGMA_PER_GAP = 0.45
SIGMA_MM = SIGMA_PER_GAP * DIFFUSER_GAP_MM      # 3.15 mm

# --- how much of the key to claim --------------------------------------------
# The drive aims at a band centred on the key, narrower than the key slot. This
# is the width knob, and with the gap pinned by continuity it is the only one:
#   realised FWHM ~ sqrt((slot*TARGET_SLOT_FRAC)^2 + (2.355*sigma)^2)
# Measured at gap 7 mm: 0.5 -> 10.2 mm, 0.7 -> 11.5 mm, 1.0 -> 14.9 mm, against
# a 13.73 mm key. 0.7 puts the lit width at 0.84 of the key it marks.
TARGET_SLOT_FRAC = 0.7

WEIGHT_FLOOR = 0.25   # below this a WS2812 quantises visibly: weight 0.25 is
                      # PWM 12/255 after gamma, 0.18 is PWM 6 -- dimmer than an
                      # unlit package looks, and the sim read it back as dark.
GAMMA = 2.2
MONO_RGB = (255, 232, 196)   # one colour for every key while shapes are compared

_MAP = None


def geometry():
    """(slots, origin_mm, pitch_mm, n_leds) straight from data/led_map.json."""
    global _MAP
    if _MAP is None:
        with open(os.path.join(ROOT, "data", "led_map.json"), "rb") as f:
            _MAP = json.load(f)
    m = _MAP
    slots = {r["midi"]: (float(r["centre_mm"]), float(r["tail_mm"])) for r in m["keys"]}
    return slots, float(m["strip_origin_mm"]), float(m["led_pitch_mm"]), int(m["led_count"])


def led_centres():
    _slots, origin, pitch, n = geometry()
    return origin + pitch * (np.arange(n) + 0.5)


def target(midi, frac=TARGET_SLOT_FRAC):
    """(centre, width) of the band the drive aims at, in the design frame."""
    slots, _origin, _pitch, _n = geometry()
    c, w = slots[midi]
    return c, w * frac


def intended(x, midi, sigma=SIGMA_MM, frac=TARGET_SLOT_FRAC):
    """The profile the bar is *trying* to show: the target band, blurred once.

    A box convolved with a Gaussian is a difference of two error functions --
    exact, no numerical integration. `fidelity()` scores how close the discrete
    LEDs get to this.
    """
    c, w = target(midi, frac)
    lo, hi = c - w / 2.0, c + w / 2.0
    s2 = sigma * math.sqrt(2.0)
    x = np.asarray(x, dtype=float)
    f = 0.5 * (np.vectorize(math.erf)((hi - x) / s2) - np.vectorize(math.erf)((lo - x) / s2))
    return f / f.max() if f.max() > 0 else f


def weights(midi, sigma=SIGMA_MM, floor=WEIGHT_FLOOR, frac=TARGET_SLOT_FRAC):
    """{led index: weight 0..1} for one key: how much of each LED's cell falls
    inside the key's target band, normalised so the peak is 1.

    This is the "treat the tape as continuous and divide it by the key" step, and
    it deliberately does NOT pre-blur. An earlier version sampled the *blurred*
    profile at the LED centres and then let the optics blur it again, which put
    16.5 mm of light on a 13.73 mm key -- the drive must carry the sharp band and
    let the diffuser do the smoothing exactly once.
    """
    _slots, _origin, pitch, _n = geometry()
    c, w = target(midi, frac)
    x = led_centres()
    lo, hi = c - w / 2.0, c + w / 2.0
    a = np.maximum(x - pitch / 2.0, lo)
    b = np.minimum(x + pitch / 2.0, hi)
    ov = np.clip(b - a, 0.0, None) / pitch
    if ov.max() <= 0:                      # band narrower than the gap between cells
        ov = np.zeros_like(x)
        ov[int(np.argmin(np.abs(x - c)))] = 1.0
    ov = ov / ov.max()
    return {int(i): float(ov[i]) for i in np.nonzero(ov >= floor)[0]}


def fidelity(midi, sigma=SIGMA_MM, frac=TARGET_SLOT_FRAC, active=None, span=2.0):
    """How well the driven LEDs reproduce `intended`, over +-span key widths.

    Returns rms error (both curves peak-normalised), the peak-position error, and
    the realised FWHM. This is the check that the diffusing model is doing what
    it claims rather than merely producing a plausible glow.
    """
    slots, _o, _p, _n = geometry()
    c, w = slots[midi]
    x = np.linspace(c - span * w, c + span * w, 2001)
    want = intended(x, midi, sigma, frac)
    got = field(x, active if active is not None else weights(midi, sigma, frac=frac), sigma)
    got = got / got.max() if got.max() > 0 else got
    above = x[got >= got.max() / 2.0]
    ref = x[want >= want.max() / 2.0]
    # Centre of the half-maximum span, not the argmax: these profiles are
    # flat-topped, so argmax wanders by half a pitch on noise and reported an
    # 8 mm "peak error" for a blob whose centre is within 1 mm.
    return {"rms": float(np.sqrt(np.mean((got - want) ** 2))),
            "centre_err_mm": float((above.max() + above.min()) / 2.0
                                   - (ref.max() + ref.min()) / 2.0),
            "fwhm_mm": float(above.max() - above.min()),
            "slot_mm": w}


def quantise(weight, rgb=MONO_RGB, gamma=GAMMA):
    """Weight -> the 8-bit RGB actually sent to a WS2812."""
    g = max(0.0, min(1.0, weight)) ** gamma
    return tuple(int(round(c * g)) for c in rgb)


def frame_for(notes, sigma=SIGMA_MM, floor=WEIGHT_FLOOR, rgb=MONO_RGB, gamma=GAMMA):
    """{led: (r,g,b)} for a set of MIDI notes, brightest contribution wins.

    Adjacent keys share LEDs, so a shared pixel has to resolve somehow. Taking
    the max (rather than the sum) keeps a key's own profile shape intact and
    never invents a brighter-than-peak pixel between two keys; summing would.
    """
    best: dict[int, float] = {}
    for n in notes:
        for led, w in weights(n, sigma, floor).items():
            if w > best.get(led, 0.0):
                best[led] = w
    return {led: quantise(w, rgb, gamma) for led, w in best.items()}, best


def field(x_mm, active, sigma=SIGMA_MM):
    """Light on the diffuser surface at positions `x_mm`, from {led: weight}.

    Each driven LED contributes a Gaussian of the same sigma that shaped the
    weights; the sum is clipped at 1. Returns a linear intensity, so a caller
    that wants to *display* it should encode it (see `to_display`).
    """
    x = np.asarray(x_mm, dtype=float)
    out = np.zeros_like(x)
    if not active:
        return out
    centres = led_centres()
    for led, w in active.items():
        d = x - centres[led]
        out += w * np.exp(-0.5 * (d / sigma) ** 2)
    return np.clip(out, 0.0, 1.0)


def to_display(intensity, rgb=MONO_RGB, gamma=GAMMA):
    """Linear intensity -> an RGBA a renderer can show, encoded for the eye."""
    e = np.clip(intensity, 0.0, 1.0) ** (1.0 / gamma)
    return np.stack([e * (rgb[0] / 255.0), e * (rgb[1] / 255.0), e * (rgb[2] / 255.0),
                     np.ones_like(e)], axis=-1)


def ripple(sigma=SIGMA_MM, n=40):
    """Modulation depth with every LED driven equally -- "can I see the dots?".

    The diffuser only looks continuous if the sum of the emitters' kernels is
    nearly flat. This drives `n` adjacent LEDs at full weight and returns
    (max-min)/max across the middle of that run, which is the quantity the
    6-10 mm gap in docs/hardware.md is really about.
    """
    centres = led_centres()
    lo = len(centres) // 2 - n // 2
    act = {i: 1.0 for i in range(lo, lo + n)}
    x = np.linspace(centres[lo + n // 4], centres[lo + 3 * n // 4], 4001)
    f = field(x, act, sigma)
    return float((f.max() - f.min()) / f.max())


def pair_for(midi):
    """What the *current* firmware would light: the fixed (hand, finger) pair."""
    import sys

    sys.path.insert(0, os.path.join(ROOT, "firmware", "src"))
    from fingerlight import notemap

    s = notemap.StripSettings.load()
    lo, hi = notemap.note_to_leds(midi, s)
    return {lo: 1.0, hi: 1.0}


def summary(sigma=SIGMA_MM, floor=WEIGHT_FLOOR):
    """Per-key statistics, for the record and for verify runs."""
    slots, _o, pitch, _n = geometry()
    x = led_centres()
    rows = []
    for midi in sorted(slots):
        c, w = slots[midi]
        wt = weights(midi, sigma, floor)
        idx = sorted(wt)
        tot = sum(wt.values())
        centroid = sum(x[i] * wt[i] for i in idx) / tot
        rows.append({"midi": midi, "slot_mm": w, "centre_mm": c,
                     "leds": idx, "weights": [round(wt[i], 3) for i in idx],
                     "centroid_err_mm": centroid - c,
                     "pwm": [quantise(wt[i])[0] for i in idx]})
    return rows


def metrics(midi, sigma=SIGMA_MM, active=None, x=None):
    """Blob width, centroid error and crosstalk for one key, on the light field."""
    slots, _o, _p, _n = geometry()
    c, w = slots[midi]
    if x is None:
        x = np.linspace(-20.0, 1250.0, 25401)      # 0.05 mm
    act = active if active is not None else weights(midi, sigma)
    f = field(x, act, sigma)
    peak = f.max()
    above = x[f >= peak / 2.0]
    nb = [m for m in (midi - 1, midi + 1) if m in slots]
    cross = max(float(np.interp(slots[m][0], x, f)) / peak for m in nb) if nb else 0.0
    return {"slot_mm": w, "fwhm_mm": float(above.max() - above.min()),
            "centroid_err_mm": float((x * f).sum() / f.sum()) - c, "crosstalk": cross}


def report(fracs=(0.7, 0.85, 1.0)):
    """Everything that decides whether this works, measured rather than argued."""
    slots, origin, pitch, n = geometry()
    print("tape %d LEDs, pitch %.4f mm, origin %+.3f mm (from data/led_map.json)"
          % (n, pitch, origin))
    print("diffuser gap %.1f mm -> sigma %.2f mm; ripple with every LED lit %.2f%% "
          "(that is 'can I see the dots')" % (DIFFUSER_GAP_MM, SIGMA_MM, 100 * ripple()))
    print()
    print("%-18s %8s %8s %10s %12s %8s %9s"
          % ("drive", "LEDs/key", "FWHM", "FWHM/slot", "centre err", "rms", "crosstalk"))
    for frac in fracs:
        W = [weights(m, frac=frac) for m in slots]
        fid = [fidelity(m, frac=frac) for m in slots]
        met = [metrics(m, active=w) for m, w in zip(slots, W)]
        print("%-18s %8.2f %7.1fmm %10.2f %10.2fmm %8.3f %8.0f%%"
              % ("band %.0f%% of slot" % (100 * frac), np.mean([len(w) for w in W]),
                 np.mean([f["fwhm_mm"] for f in fid]),
                 np.mean([f["fwhm_mm"] / f["slot_mm"] for f in fid]),
                 max(abs(f["centre_err_mm"]) for f in fid),
                 np.mean([f["rms"] for f in fid]),
                 100 * np.mean([m["crosstalk"] for m in met])))

    pair = [metrics(m, active=pair_for(m)) for m in slots]
    pf = [fidelity(m, active=pair_for(m)) for m in slots]
    print("%-18s %8.2f %7.1fmm %10.2f %10.2fmm %8.3f %8.0f%%"
          % ("today's fixed pair", 2.0, np.mean([f["fwhm_mm"] for f in pf]),
             np.mean([f["fwhm_mm"] / f["slot_mm"] for f in pf]),
             max(abs(f["centre_err_mm"]) for f in pf),
             np.mean([f["rms"] for f in pf]),
             100 * np.mean([m["crosstalk"] for m in pair])))

    # Control: the same scoring against a drive that is deliberately one LED out.
    # Without this the rms column is just a number that looks small.
    bad = []
    for m in slots:
        w = weights(m)
        shifted = {min(k + 1, n - 1): v for k, v in w.items()}   # C8 has no LED to its right
        bad.append(fidelity(m, active=shifted))
    print("%-18s %8s %7.1fmm %10s %10.2fmm %8.3f %9s"
          % ("(control: +1 LED)", "-", np.mean([f["fwhm_mm"] for f in bad]), "-",
             max(abs(f["centre_err_mm"]) for f in bad),
             np.mean([f["rms"] for f in bad]), "-"))
    print()
    print("the control row is the same key driven one LED to the right: if the rms and")
    print("centre-error columns did not move, they would not be measuring anything.")


if __name__ == "__main__":
    report()
