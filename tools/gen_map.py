#!/usr/bin/env python3
"""
PX-S1100 LED bar geometry + note->LED map generator.

Model: rear key-tail line (where a strip mounted behind the keys sits).
At that line all 88 key tails are side-by-side, so a *note-uniform* slot model
is correct inside each octave -- which the naive `2*(note-20)` mapping is not.
"""
import json, csv, sys, os

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, 'data')

OCT   = 164.1            # octave span (7 white keys) [mm] -- measure & override
NW    = 52
PW    = OCT / 7          # white-key pitch
SPAN  = NW * PW          # A0 left edge -> C8 right edge
LEDS_PER_M = 144
LPITCH = 1000.0 / LEDS_PER_M
NLED  = 176
NAMES = ['C','C#','D','D#','E','F','F#','G','G#','A','A#','B']

def name(midi): return f"{NAMES[midi%12]}{midi//12-1}"
def is_black(midi): return NAMES[midi%12].endswith('#')

def tail_geometry(oct_span=OCT):
    """Return (centre_mm, width_mm) per MIDI note 21..108, x=0 at A0 left edge."""
    pw = oct_span/7
    out, x = [], 0.0
    w = 2*pw/3                       # A0,A#0,B0 share two white pitches
    for i in range(3): out.append((x + w*(i+0.5), w))
    x = 2*pw
    w = oct_span/12                  # C1..B7: 7 octaves of 12 equal tails
    for _ in range(7):
        for i in range(12): out.append((x + w*(i+0.5), w))
        x += oct_span
    out.append((x + pw/2, pw))       # C8
    return out

def build(oct_span=OCT, nled=NLED, lpitch=LPITCH, origin=None):
    """origin: None -> sweep for the smallest worst-case centre error (default);
    'centre' -> split the leftover evenly so the tape is symmetric on the
    keyboard; a number -> that offset from A0's left edge, in mm."""
    geo = tail_geometry(oct_span)
    span = 52*oct_span/7

    def place(off):
        idx = [min(nled-1, max(0, round((c-off)/lpitch - 0.5))) for c,_ in geo]
        err = max(abs(c - (off + lpitch*(i+0.5))) for (c,_),i in zip(geo,idx))
        return err, off, idx

    if origin is None:
        mode = 'min-error sweep'
        err, off, idx = min((place(k*0.25) for k in range(-80, 81)), key=lambda t: t[0])
    elif origin == 'centre':
        mode = 'centred on the playing width'
        err, off, idx = place((span - nled*lpitch)/2)
    else:
        mode = 'explicit'
        err, off, idx = place(float(origin))
    rows = []
    for n,((c,w),i) in enumerate(zip(geo, idx)):
        midi = 21+n
        lo, hi = c-w/2, c+w/2
        inside = [j for j in range(nled) if lo <= off + lpitch*(j+0.5) < hi]
        rows.append(dict(midi=midi, name=name(midi), black=is_black(midi),
                         centre_mm=round(c,2), tail_mm=round(w,2),
                         led=i, leds_in_key=inside,
                         err_mm=round(c-(off+lpitch*(i+0.5)),2),
                         naive_led=2*(midi-20)))
    return dict(octave_span_mm=oct_span, white_pitch_mm=round(pw:=oct_span/7,4),
                playing_width_mm=round(span,2), leds_per_m=LEDS_PER_M,
                led_pitch_mm=round(lpitch,4), led_count=nled,
                strip_origin_mm=round(off,3), strip_origin_mode=mode,
                worst_centre_err_mm=round(err,2),
                keys=rows)

def plv_note_offsets(m):
    """Piano-LED-Visualizer stores a cumulative piecewise offset table:
       note_offset = sum(offset for threshold,offset if note>threshold) - shift
       led         = int(density*(note-20) - note_offset)
       Emit the minimal (threshold, offset) list plus the shift reproducing our LUT.

    So with d = naive_led - led we have d = sum(...) - shift, and at the bottom
    note no threshold has been passed yet, hence shift = -d(21). The sign was
    wrong here until 2026-09-21 and nothing caught it, because every map this
    tool had produced happened to give d(21) = 0."""
    d = {r['midi']: r['naive_led'] - r['led'] for r in m['keys']}
    out, prev = [], None
    for midi in range(21, 109):
        cur = d[midi]
        if prev is None:
            shift = -cur
        elif cur != prev:
            out.append([midi-1, cur-prev])
        prev = cur
    return shift, out

if __name__ == "__main__":
    args = sys.argv[1:]
    origin = None
    if "--origin" in args:
        i = args.index("--origin")
        origin = args[i+1]
        del args[i:i+2]
    span = float(args[0]) if args else OCT
    m = build(span, origin=origin)
    base, offs = plv_note_offsets(m)
    m['plv_shift'] = base
    m['plv_note_offsets'] = offs
    with open(os.path.join(OUT,'led_map.json'),'w') as f: json.dump(m,f,indent=1)
    with open(os.path.join(OUT,'led_map.csv'),'w',newline='') as f:
        w = csv.writer(f); w.writerow(['midi','name','black','tail_centre_mm','tail_width_mm','primary_led','leds_in_key','centre_err_mm','naive_led'])
        for r in m['keys']:
            w.writerow([r['midi'],r['name'],int(r['black']),r['centre_mm'],r['tail_mm'],r['led'],
                        '|'.join(map(str,r['leds_in_key'])),r['err_mm'],r['naive_led']])
    print(f"octave span      {m['octave_span_mm']} mm -> white pitch {m['white_pitch_mm']} mm")
    print(f"playing width    {m['playing_width_mm']} mm")
    print(f"strip            {m['led_count']} LED @ {m['leds_per_m']}/m, pitch {m['led_pitch_mm']} mm, span {round(m['led_count']*m['led_pitch_mm'],2)} mm")
    print(f"strip origin     {m['strip_origin_mm']:+.3f} mm relative to A0 left edge ({m['strip_origin_mode']})")
    print(f"strip margins    {m['strip_origin_mm']:+.2f} mm left / {m['playing_width_mm']-m['strip_origin_mm']-m['led_count']*m['led_pitch_mm']:+.2f} mm right")
    print(f"worst centre err {m['worst_centre_err_mm']} mm  ({m['worst_centre_err_mm']/m['white_pitch_mm']*100:.0f}% of a white key)")
    from collections import Counter
    print("LEDs inside key:", dict(Counter(len(r['leds_in_key']) for r in m['keys'])))
    print("unique primary LEDs:", len({r['led'] for r in m['keys']}), "/ 88")
    print(f"PLV shift = {base}, note_offsets entries = {len(offs)}")
    print(f"PLV note_offsets = {offs}")
