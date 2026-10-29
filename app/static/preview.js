/* PC preview: draws the 176-LED bar and the 88 keys at true scale from data/led_map.json,
 * lights the LEDs from the server's state, and turns clicks / computer keys into notes.
 */
"use strict";

const NS = "http://www.w3.org/2000/svg";
const svg = document.getElementById("kb");
const KEY_LEN = 95, BLACK_LEN = 58, BAR_Y = 2, BAR_H = 8, KEY_Y = 16;
let geo = null, ledEls = [], keyEls = {};
let octave = 4;
const held = new Set();

function el(tag, attrs) {
  const e = document.createElementNS(NS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  return e;
}

function post(path, body) {
  return fetch("/api/" + path, { method: "POST", headers: { "Content-Type": "application/json" },
                                  body: JSON.stringify(body || {}) }).then((r) => r.json());
}
const noteOn = (p) => { held.add(p); post("note", { pitch: p, on: true }); };
const noteOff = (p) => { if (held.delete(p)) post("note", { pitch: p, on: false }); };

function draw(g) {
  geo = g;
  const W = g.playing_width_mm, wp = g.white_pitch_mm;
  svg.setAttribute("viewBox", "-4 0 " + (W + 8) + " " + (KEY_Y + KEY_LEN + 4));
  svg.appendChild(el("rect", { x: -4, y: 0, width: W + 8, height: BAR_Y * 2 + BAR_H, rx: 2, fill: "#0d0f13" }));
  for (let i = 0; i < g.led_count; i++) {
    const cx = g.strip_origin_mm + (i + 0.5) * g.led_pitch_mm;
    const r = el("rect", { x: cx - 3, y: BAR_Y + 1, width: 6, height: 6, rx: 1, fill: "#1b1e25" });
    svg.appendChild(r);
    ledEls.push(r);
  }
  // white keys first, then black on top
  let w = 0;
  const whites = g.keys.filter((k) => !k.black);
  whites.forEach((k) => {
    const r = el("rect", { x: w * wp, y: KEY_Y, width: wp, height: KEY_LEN, rx: 1.5, class: "wk" });
    r.dataset.p = k.midi;
    svg.appendChild(r);
    keyEls[k.midi] = r;
    w++;
  });
  g.keys.filter((k) => k.black).forEach((k) => {
    const bw = wp * 0.58;
    const r = el("rect", { x: k.centre_mm - bw / 2, y: KEY_Y, width: bw, height: BLACK_LEN, rx: 1, class: "bk" });
    r.dataset.p = k.midi;
    svg.appendChild(r);
    keyEls[k.midi] = r;
  });
  const c4 = whites.find((k) => k.midi === 60);
  if (c4) {
    const x = whites.indexOf(c4) * wp + wp / 2;
    const t = el("text", { x, y: KEY_Y + KEY_LEN - 6, "text-anchor": "middle", "font-size": 7, fill: "#777" });
    t.textContent = "C4";
    svg.appendChild(t);
  }
}

function show(state) {
  if (!geo) return;
  const lit = {};
  state.led.pixels.forEach(([i, r, g, b]) => { lit[i] = [r, g, b]; });
  ledEls.forEach((e, i) => {
    const c = lit[i];
    if (c) {
      e.setAttribute("fill", "rgb(" + c.join(",") + ")");
      e.setAttribute("filter", "url(#glow)");
    } else {
      e.setAttribute("fill", "#1b1e25");
      e.removeAttribute("filter");
    }
  });
  const want = new Set();
  if (state.practice && !state.practice.finished) state.practice.now.forEach((c) => { if (c.waiting) want.add(c.pitch); });
  const down = new Set(state.held);
  for (const p in keyEls) {
    keyEls[p].classList.toggle("held", down.has(Number(p)));
    keyEls[p].classList.toggle("want", want.has(Number(p)) && !down.has(Number(p)));
  }
  const p = state.practice;
  document.getElementById("log").textContent = p
    ? (p.finished ? "finished: " : "event " + (p.index + 1) + " of " + p.total + ": ") +
      (p.accuracy === null ? "" : p.accuracy + "% correct, ") + (p.wrong + p.missed) + " missed so far"
    : "open a score from the Library on the panel";
}

// glow for lit LEDs
const defs = el("defs", {});
defs.innerHTML = '<filter id="glow" x="-200%" y="-200%" width="500%" height="500%">' +
  '<feGaussianBlur stdDeviation="2.2" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter>';
svg.appendChild(defs);

// ---- pointer input
svg.addEventListener("pointerdown", (e) => {
  const p = e.target.dataset && e.target.dataset.p;
  if (!p) return;
  e.preventDefault();
  noteOn(Number(p));
  const up = () => { noteOff(Number(p)); window.removeEventListener("pointerup", up); };
  window.addEventListener("pointerup", up);
});

// ---- computer keyboard (DAW layout)
const MAP = { a: 0, w: 1, s: 2, e: 3, d: 4, f: 5, t: 6, g: 7, y: 8, h: 9, u: 10, j: 11, k: 12 };
const down = {};
window.addEventListener("keydown", (e) => {
  if (e.repeat) return;
  const k = e.key.toLowerCase();
  if (k === " ") { e.preventDefault(); post("autoplay"); return; }
  if (k === "shift") { post("pedal", { down: true }); return; }
  if (k === "z" || k === "x") {
    octave = Math.min(7, Math.max(1, octave + (k === "x" ? 1 : -1)));
    document.getElementById("oct").textContent = "C" + octave;
    return;
  }
  if (!(k in MAP)) return;
  const p = 12 * (octave + 1) + MAP[k];
  down[k] = p;
  noteOn(p);
});
window.addEventListener("keyup", (e) => {
  const k = e.key.toLowerCase();
  if (k === "shift") { post("pedal", { down: false }); return; }
  if (down[k] !== undefined) { noteOff(down[k]); delete down[k]; }
});

document.getElementById("auto").addEventListener("click", () => post("autoplay"));
document.getElementById("wrong").addEventListener("click", async () => {
  const r = await fetch("/api/state").then((x) => x.json());
  const p = r.practice;
  const want = p && !p.finished ? p.now.map((c) => c.pitch) : [60];
  let q = Math.max(...want) + 1;
  while (want.includes(q)) q++;
  await post("note", { pitch: Math.min(108, q), on: true });
  await post("note", { pitch: Math.min(108, q), on: false });
});

fetch("/api/geometry").then((r) => r.json()).then((g) => {
  draw(g);
  fetch("/api/state").then((r) => r.json()).then(show);
  const es = new EventSource("/api/events");
  es.onmessage = (m) => show(JSON.parse(m.data));
});
