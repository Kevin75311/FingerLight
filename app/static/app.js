/* Finger_Light touchscreen app.  Vanilla JS, no build step, no network beyond the Pi.
 *
 * One state snapshot arrives from the server on every change (server-sent events) and
 * the current screen is re-rendered from it.  Every control is a <button data-act=...>
 * handled by one delegated listener -- no handler is ever assembled into a string.
 */
"use strict";

const $ = (sel) => document.querySelector(sel);
const view = $("#view");
const FINGER = { 1: "THUMB", 2: "INDEX", 3: "MIDDLE", 4: "RING", 5: "LITTLE" };

let S = null;                 // last state snapshot
let screen = (location.hash || "#library").slice(1);
let selected = null;          // library selection
let handsPick = null;         // library hand filter (null = settings default)
let lastSummary = null;       // summary id already shown
let dragging = false;         // brightness slider in use: do not re-render under it

// ------------------------------------------------------------------- utilities

function esc(v) {
  return String(v ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function icon(name) {
  const p = {
    play: '<path d="M7 4l14 8-14 8z"/>',
    pause: '<path d="M6 4h4v16H6zM14 4h4v16h-4z"/>',
    prev: '<path d="M5 4h3v16H5zM20 4L9 12l11 8z"/>',
    next: '<path d="M16 4h3v16h-3zM4 4l11 8-11 8z"/>',
    stop: '<path d="M6 6h12v12H6z"/>',
    loop: '<path d="M7 7h9a4 4 0 0 1 0 8h-1v-2.5h1a1.5 1.5 0 0 0 0-3H7V13L2.5 8.25 7 3.5zM17 17H8a4 4 0 0 1 0-8h1v2.5H8a1.5 1.5 0 0 0 0 3h9V11l4.5 4.75L17 20.5z"/>',
  }[name];
  return '<svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">' + p + "</svg>";
}

let toastTimer = null;
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove("show"), 3500);
}

async function api(path, body) {
  try {
    const r = await fetch("/api/" + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    const j = await r.json();
    if (!j.ok) toast(j.error || "request failed");
    return j.ok;
  } catch (e) {
    toast("the app server is not answering");
    return false;
  }
}

function go(name) {
  screen = name;
  history.replaceState(null, "", "#" + name);
  render();
}

function fit() {
  const s = Math.min(window.innerWidth / 800, window.innerHeight / 480);
  document.documentElement.style.setProperty("--scale", s.toFixed(4));
}
window.addEventListener("resize", fit);
window.addEventListener("hashchange", () => {
  const h = (location.hash || "#library").slice(1);
  if (h !== screen) { screen = h; render(); }
});
fit();

const handWord = (h) => ({ L: "left hand", R: "right hand" }[h] || "both hands");
const pct = (v) => (v === null || v === undefined ? "&mdash;" : v + "%");
const ringClass = (v) => (v === null || v === undefined ? "" : v >= 90 ? "good" : v >= 70 ? "mid" : "");
function clock(s) {
  const m = Math.floor(s / 60), r = Math.floor(s % 60);
  return m + ":" + String(r).padStart(2, "0");
}
function ago(iso) {
  if (!iso) return "never played";
  const d = new Date(iso), now = new Date();
  const days = Math.floor((now.setHours(0, 0, 0, 0) - new Date(iso).setHours(0, 0, 0, 0)) / 864e5);
  if (days <= 0) return "played today " + d.toTimeString().slice(0, 5);
  if (days === 1) return "yesterday";
  return days + " days ago";
}

// ------------------------------------------------------------------ rendering

function render() {
  if (!S) return;
  // the screen's finger colours are whatever palette the bar is using right now
  const pal = S.palettes[S.settings.palette];
  pal.forEach((c, i) => document.documentElement.style.setProperty("--f" + (i + 1), c));

  document.querySelectorAll("#tabs .tab").forEach((b) =>
    b.classList.toggle("on", b.dataset.go === screen));
  renderPills();
  if (screen === "settings" && dragging) return;
  const fn = { library: renderLibrary, play: renderPlay, session: renderSession,
               settings: renderSettings }[screen] || renderLibrary;
  view.innerHTML = fn();
}

function renderPills() {
  const m = S.inputs.midi, k = S.inputs.mic;
  const midi = m.state === "live" ? '<div class="pill live">USB-MIDI</div>'
    : m.state === "absent" ? '<div class="pill warn">no piano</div>'
    : '<div class="pill off">no MIDI</div>';
  const mic = k.state === "live" ? '<div class="pill live">mic</div>'
    : k.state === "idle" ? '<div class="pill off">mic idle</div>'
    : "";
  $("#pills").innerHTML = midi + mic;
}

// ---- library

function renderLibrary() {
  const lib = S.library;
  if (!lib.length) {
    return '<div class="body empty" style="height:424px"><div class="big-msg">No scores yet</div>' +
      "<div>Copy <b>*.lightscore.json</b> files into <b>~/.fingerlight/scores</b> on the Pi, then rescan.</div>" +
      '<button class="btn" data-act="rescan">Rescan</button></div>';
  }
  if (!selected || !lib.some((e) => e.id === selected)) {
    const cur = S.practice && lib.find((e) => e.id === S.practice.score_id);
    selected = (cur || lib.find((e) => e.status === "ready") || lib[0]).id;
  }
  const sel = lib.find((e) => e.id === selected);
  const hands = handsPick || S.settings.hands;
  const rows = lib.map((e) => {
    let meta, badge = "";
    if (e.status === "ready") {
      meta = [e.events + " events", e.hands === "L+R" ? "both hands" : handWord(e.hands),
              clock(e.duration_s), ago(e.last_at)];
      if (e.attempts) meta.push(e.attempts + (e.attempts === 1 ? " attempt" : " attempts"));
    } else {
      badge = '<span class="badge' + (e.status === "invalid" ? " bad" : "") + '">' +
        (e.status === "invalid" ? "invalid" : "not compiled") + "</span>";
      meta = [e.detail];
    }
    return '<div class="item' + (e.id === selected ? " on" : "") +
      (e.status !== "ready" ? " disabled" : "") + '" data-act="select" data-id="' + esc(e.id) + '">' +
      '<div style="min-width:0"><div class="name">' + esc(e.title) + badge + "</div>" +
      '<div class="meta">' + meta.map(esc).join(" &middot; ") + "</div></div>" +
      '<div class="ring ' + ringClass(e.last_accuracy) + '">' + pct(e.last_accuracy) + "</div></div>";
  }).join("");
  const more = lib.length > 4 ? '<div class="more">' + (lib.length - 4) + " more &mdash; swipe to scroll</div>" : "";
  const can = (h) => sel && sel.status === "ready" && (h === "both" || sel.hands.includes(h));
  const hb = (h, label) => '<button class="' + (hands === h ? "on" : "") + '" data-act="hands" data-v="' + h + '"' +
    (can(h) ? "" : " disabled") + ">" + label + "</button>";
  return '<div class="body" style="grid-template-rows:1fr auto;height:424px">' +
    '<div class="card list">' + rows + more + "</div>" +
    '<div style="display:flex;gap:10px;align-items:center">' +
    '<div class="seg hands">' + hb("both", "both hands") + hb("R", "right") + hb("L", "left") + "</div>" +
    '<button class="btn small ghost" data-act="rescan">Rescan</button>' +
    '<div style="flex:1"></div>' +
    '<button class="btn primary" style="min-width:150px" data-act="practise"' +
    (sel && sel.status === "ready" && can(hands) ? "" : " disabled") + ">Practise " + icon("play") + "</button>" +
    "</div></div>";
}

// ---- play

function chip(c, i, n) {
  const cls = "chip f" + c.finger + (c.waiting ? " waiting" : "") + (c.done ? " done" : "");
  return '<div class="' + cls + '"><i class="hand" style="background:' + S.hand_colors[c.hand] +
    '"></i>' + esc(c.note) + '<span class="fng">' + FINGER[c.finger] + "</span></div>";
}
function mini(c, faded) {
  return '<span class="mini" style="background:var(--f' + c.finger + ")" +
    (faded ? ";opacity:.55" : "") + '">' + esc(c.note) + "</span>";
}

function renderPlay() {
  const p = S.practice;
  if (!p) {
    return '<div class="body empty" style="height:424px"><div class="big-msg">Nothing open</div>' +
      "<div>Pick a score in the library.</div>" +
      '<button class="btn primary" data-go="library">Library</button></div>';
  }
  const along = p.mode === "along";
  let center;
  if (p.finished) {
    center = '<div class="chips" style="align-self:center"><div class="chip finished">Done &mdash; ' +
      pct(p.accuracy) + ' correct</div></div><div class="next"><button class="btn small" data-go="session">See the session &rarr;</button></div>';
  } else {
    const nx = p.next[0] ? "next " + p.next[0].map((c) => mini(c, false)).join("") : "last event";
    // "then" only while it fits beside "next": a clipped preview row reads as a bug
    const th = p.next[1] && p.next[0].length + p.next[1].length <= 8 ? '<span style="margin-left:10px">then</span> ' + p.next[1].map((c) => mini(c, true)).join("") : "";
    center = '<div class="chips' + (p.now.length > 4 ? " many" : "") + '" style="align-self:center">' +
      p.now.map(chip).join("") + "</div>" + '<div class="next">' + nx + th + "</div>";
  }
  let overlay = "";
  if (!p.finished && !p.running) {
    overlay = along
      ? '<div class="overlay"><div>Press ' + "&#9654;" + " to start<small>one-second lead-in &middot; " + p.tempo + "% tempo</small></div></div>"
      : '<div class="overlay"><div>Paused<small>play the lit key, or press &#9654;</small></div></div>';
  }
  const where = (p.beat ? "beat " + p.beat + " &middot; " : "") + clock(p.time_s) + " / " + clock(p.duration_s);
  const loop = p.loop ? "events " + (p.loop[0] + 1) + "&ndash;" + (p.loop[1] + 1) + (p.loop_passes ? " &middot; &times;" + p.loop_passes : "")
    : p.loop_pending !== null ? "from " + (p.loop_pending + 1) + " &hellip;" : "off";
  const misses = p.wrong + p.missed;
  const prog = p.total ? Math.round(100 * p.done / p.total) : 0;
  const loopBtn = p.loop ? "btn small on" : p.loop_pending !== null ? "btn small ghost pending" : "btn small ghost";
  const loopLbl = p.loop ? "loop on" : p.loop_pending !== null ? "set end" : "loop";
  return '<div style="display:grid;grid-template-rows:1fr auto;height:424px">' +
    '<div class="body cue">' +
    '<div class="card now"><div class="label"><span class="ttl">' + esc(p.title) + "</span><span>" +
    (p.finished ? "FINISHED" : "PLAY NOW &mdash; " + handWord(p.now_hands.length === 1 ? p.now_hands[0] : "both")) +
    "</span></div>" + center + overlay + "</div>" +
    '<div class="card meter">' +
    '<div class="row"><span class="k">this attempt</span><span class="v">' + pct(p.accuracy) + "</span></div>" +
    '<div class="bar"><i style="width:' + (p.accuracy || 0) + '%"></i></div>' +
    '<div class="row"><span class="k">progress</span><span class="v small">' + p.done + " / " + p.total + " events</span></div>" +
    '<div class="bar"><i style="width:' + prog + '%"></i></div>' +
    '<div class="row"><span class="k">position</span><span class="v small">' + where + "</span></div>" +
    '<div class="row"><span class="k">loop</span><span class="v small">' + loop + "</span></div>" +
    '<div class="row"><span class="k">missed so far</span><span class="v small" style="color:' +
    (misses ? "var(--warn)" : "var(--text)") + '">' + misses + "</span></div>" +
    "</div></div>" +
    '<div class="transport">' +
    '<button class="' + loopBtn + '" data-act="t" data-cmd="loop"' + (p.finished ? " disabled" : "") + ">" + icon("loop") + "<span>" + loopLbl + "</span></button>" +
    '<button class="btn nav" data-act="t" data-cmd="prev" aria-label="previous event"' + (p.finished ? " disabled" : "") + ">" + icon("prev") + "</button>" +
    '<button class="btn primary play" data-act="t" data-cmd="toggle" aria-label="play or pause"' +
    (p.finished ? " disabled" : "") + ">" + icon(p.running ? "pause" : "play") + "</button>" +
    '<button class="btn nav" data-act="t" data-cmd="next" aria-label="skip event"' + (p.finished ? " disabled" : "") + ">" + icon("next") + "</button>" +
    '<div class="grow"></div>' +
    '<div class="tempo"><button class="btn small" data-act="t" data-cmd="tempo" data-arg="-10"' + (along ? "" : " disabled") + ">&minus;</button>" +
    '<div class="val"' + (along ? "" : ' style="opacity:.35"') + ">" + p.tempo + '<span style="font-size:13px;color:var(--dim)">%</span></div>' +
    '<button class="btn small" data-act="t" data-cmd="tempo" data-arg="10"' + (along ? "" : " disabled") + ">+</button></div>" +
    '<button class="btn small ghost mode" data-act="set" data-k="mode" data-v="' + (along ? "wait" : "along") + '">' +
    (along ? "mode: along" : "mode: wait") + "</button>" +
    '<button class="btn small ghost" data-act="t" data-cmd="stop" aria-label="end attempt"' + (p.finished ? " disabled" : "") + ">" + icon("stop") + "</button>" +
    "</div></div>";
}

// ---- session

function renderSession() {
  const s = S.summary;
  if (!s) {
    return '<div class="body empty" style="height:424px"><div class="big-msg">No attempt yet</div>' +
      "<div>Finish (or end) an attempt and its review appears here.</div></div>";
  }
  const played = s.credited + s.missed + s.skipped;
  const range = "events " + (s.events_from + 1) + "&ndash;" + (s.events_to + 1) + " of " + s.events_total;
  const delta = s.delta === null ? "&mdash;" : (s.delta > 0 ? "+" : "") + s.delta + " pts";
  const dColor = s.delta === null ? "var(--dim)" : s.delta >= 0 ? "var(--ok)" : "var(--bad)";
  const tagCls = { "hesitated": "late", "skipped": "late", "wrong key": "wrong", "missed": "missed" };
  const rows = s.problems.map((m) =>
    '<div class="miss"><div class="note" style="color:var(--f' + m.finger + ')">' + esc(m.note) + "</div>" +
    '<div class="why">' + esc(m.where) + " &middot; " + esc(m.finger_name) + " &middot; " + esc(m.detail) + "</div>" +
    '<div class="tag ' + tagCls[m.kind] + '">' + esc(m.kind) + "</div>" +
    '<button class="goto" data-act="loopspot" data-ev="' + m.event + '">loop here</button></div>').join("");
  // the spot worth looping is the event with the most problems
  const count = {};
  s.problems.forEach((m) => { count[m.event] = (count[m.event] || 0) + 1; });
  const worst = Object.keys(count).sort((a, b) => count[b] - count[a])[0];
  return '<div class="body" style="grid-template-rows:1fr auto;height:424px">' +
    '<div class="grid2">' +
    '<div class="card stat">' +
    '<div><div class="k">ATTEMPT ' + s.attempt + " &middot; " + range + "</div>" +
    '<div class="k" style="margin-top:2px">' + esc(s.title) + " &middot; " +
    (s.mode === "along" ? "play-along " + s.tempo + "%" : "wait mode") + (s.stopped ? " &middot; ended early" : "") + "</div></div>" +
    '<div><div class="k">NOTES CORRECT</div><div class="big">' + (s.accuracy === null ? "&mdash;" : s.accuracy + "<small>%</small>") + "</div></div>" +
    '<div class="bar"><i style="width:' + (s.accuracy || 0) + '%"></i></div>' +
    '<div class="row"><span class="k">notes played</span><span class="v small">' + s.credited + " / " + played + "</span></div>" +
    '<div class="row"><span class="k">wrong keys</span><span class="v small"' + (s.wrong ? ' style="color:var(--bad)"' : "") + ">" + s.wrong + "</span></div>" +
    '<div class="row"><span class="k">hesitations</span><span class="v small"' + (s.hesitations ? ' style="color:var(--warn)"' : "") + ">" + s.hesitations + "</span></div>" +
    '<div class="row"><span class="k">vs. last attempt</span><span class="v small" style="color:' + dColor + '">' + delta + "</span></div>" +
    "</div>" +
    '<div class="card misses"><div class="k" style="font-weight:600;letter-spacing:.6px;margin:2px 4px 8px">WHERE IT WENT WRONG</div>' +
    '<div class="scroll">' + (rows || '<div class="clean">Clean run &mdash; nothing to fix.</div>') + "</div></div>" +
    "</div>" +
    '<div style="display:flex;gap:10px;align-items:center">' +
    '<button class="btn ghost" data-act="retry" data-tempo="-10"' + (s.mode === "along" ? "" : " disabled") + ">Slower &minus;10%</button>" +
    '<button class="btn ghost" data-act="loopspot" data-ev="' + (worst ?? "") + '"' + (worst === undefined ? " disabled" : "") + ">Loop the worst spot</button>" +
    '<div style="flex:1"></div>' +
    '<button class="btn primary" style="min-width:190px" data-act="retry" data-tempo="0">Try again ' + icon("play") + "</button>" +
    "</div></div>";
}

// ---- settings

function renderSettings() {
  const st = S.settings;
  const seg = (k, opts) => '<div class="seg">' + opts.map(([v, label, dis]) =>
    '<button class="' + (String(st[k]) === String(v) ? "on" : "") + '" data-act="set" data-k="' + k +
    '" data-v="' + v + '"' + (dis ? " disabled" : "") + ">" + label + "</button>").join("") + "</div>";
  const sw = Object.entries(S.palettes).map(([name, cols]) =>
    '<button class="sw' + (st.palette === name ? " on" : "") + '" data-act="set" data-k="palette" data-v="' + name +
    '" aria-label="' + name + ' palette">' + cols.map((c) => '<i style="background:' + c + '"></i>').join("") + "</button>").join("");
  const midiLive = S.inputs.midi.state === "live";
  const b = Math.round(st.brightness * 100);
  return '<div class="body" style="height:424px"><div class="card rows" style="min-height:0">' +
    '<div class="srow"><div class="k">Practice mode<small>what the bar does when you are late</small></div>' +
    seg("mode", [["wait", "Wait for me"], ["along", "Play along"]]) + "</div>" +
    '<div class="srow"><div class="k">Finger palette<small>' + esc(st.palette) + " &middot; undecided until the bar is built</small></div>" +
    '<div class="swatches">' + sw + "</div></div>" +
    '<div class="srow"><div class="k">Brightness<small>global cap &middot; also caps current draw</small></div>' +
    '<div class="range"><input type="range" id="bright" min="5" max="100" step="5" value="' + b + '">' +
    '<span class="val" id="brightval"><b>' + b + "%</b> &middot; &le; " + S.led.amps_cap + " A</span></div></div>" +
    '<div class="srow"><div class="k">Look ahead<small>events dim-lit before now (Q-06)</small></div>' +
    seg("lookahead", [[0, "off"], [1, "1"], [2, "2"], [3, "3"]]) + "</div>" +
    '<div class="srow"><div class="k">Input<small>the mic is a backup for no-MIDI instruments (D-29)</small></div>' +
    seg("input", [["midi", "USB-MIDI"], ["mic", "Microphone", midiLive]]) + "</div>" +
    '<div class="srow"><div class="k">LED bar<small>wire order and timing live in settings.json</small></div>' +
    '<div class="info"><b>' + S.led.count + "</b> LEDs &middot; " + esc(S.led.sink) + " &middot; order <b>" + esc(st.led_order) +
    "</b> &middot; 1-bit <b>" + esc(st.led_one_pattern) + "</b><br>MIDI: " + esc(S.inputs.midi.detail || S.inputs.midi.state) +
    "</div></div>" +
    "</div></div>";
}

// -------------------------------------------------------------------- actions

document.addEventListener("click", async (e) => {
  const nav = e.target.closest("[data-go]");
  if (nav) return go(nav.dataset.go);
  const b = e.target.closest("[data-act]");
  if (!b || b.disabled) return;
  const d = b.dataset;
  switch (d.act) {
    case "select": selected = d.id; handsPick = null; return render();
    case "hands": handsPick = d.v; return render();
    case "rescan": return api("rescan");
    case "practise": {
      const ok = await api("open", { id: selected, hands: handsPick || S.settings.hands });
      if (ok) go("play");
      return;
    }
    case "t": return api("transport", { cmd: d.cmd, arg: d.arg === undefined ? null : Number(d.arg) });
    case "set": {
      const v = d.k === "lookahead" ? Number(d.v) : d.v;
      return api("settings", { [d.k]: v });
    }
    case "retry": {
      const ok = await api("retry", { tempo_delta: Number(d.tempo || 0) });
      if (ok) go("play");
      return;
    }
    case "loopspot": {
      if (d.ev === "") return;
      const ev = Number(d.ev);
      const ok = await api("retry", { loop: [Math.max(0, ev - 2), ev + 2] });
      if (ok) go("play");
      return;
    }
  }
});

document.addEventListener("input", (e) => {
  if (e.target.id !== "bright") return;
  dragging = true;
  const v = Number(e.target.value);
  const amps = (S.led.amps_full * v / 100).toFixed(1);
  $("#brightval").innerHTML = "<b>" + v + "%</b> &middot; &le; " + amps + " A";
});
document.addEventListener("change", async (e) => {
  if (e.target.id !== "bright") return;
  dragging = false;
  await api("settings", { brightness: Number(e.target.value) / 100 });
});

// -------------------------------------------------------------------- the feed

function accept(state) {
  const fresh = state.summary && state.summary.id !== lastSummary;
  if (S === null && state.summary) lastSummary = state.summary.id;   // not "new" on load
  else if (fresh) {
    lastSummary = state.summary.id;
    if (screen === "play") screen = "session";
  }
  if (S === null && state.practice && !state.practice.finished && !location.hash) screen = "play";
  if (state.error && (!S || S.error !== state.error)) toast(state.error);
  S = state;
  render();
}

function connect() {
  const es = new EventSource("/api/events");
  es.onmessage = (m) => accept(JSON.parse(m.data));
  es.onerror = () => {
    $("#pills").innerHTML = '<div class="pill warn">reconnecting</div>';
  };
}

fetch("/api/state").then((r) => r.json()).then(accept).catch(() => toast("the app server is not answering"));
connect();
