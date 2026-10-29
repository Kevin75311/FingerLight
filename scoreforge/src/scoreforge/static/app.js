/* ScoreForge frontend. Same-origin by default; when hosted elsewhere (the
   Cloudflare Pages copy) it asks for the local engine's address and stores it. */

"use strict";

const $ = (id) => document.getElementById(id);

/* ── engine base URL ─────────────────────────────────────────────────── */

let API = localStorage.getItem("scoreforge-engine") || "";

const els = {
  chips: $("engine-chips"),
  chipEngine: $("chip-engine"),
  connectBar: $("connect-bar"),
  connectForm: $("connect-form"),
  engineUrl: $("engine-url"),
  dropzone: $("dropzone"),
  fileInput: $("file-input"),
  dropLabel: $("drop-label"),
  dropSub: $("drop-sub"),
  forgeBtn: $("forge-btn"),
  stave: $("stave"),
  staveStatus: $("stave-status"),
  logCard: $("log-card"),
  log: $("log"),
  logTime: $("log-time"),
  resultsCard: $("results-card"),
  summary: $("summary"),
  downloads: $("downloads"),
  againBtn: $("again-btn"),
  melodyRow: $("opt-melody-row"),
  melody: $("opt-melody"),
  chordsRow: $("opt-chords-row"),
  chords: $("opt-chords"),
  pdf: $("opt-pdf"),
  pdfHint: $("pdf-hint"),
  debug: $("opt-debug"),
  maxFret: $("opt-maxfret"),
  fretRow: $("opt-fret-row"),
};

let meta = null;
let file = null;
let stages = [];        // [{id, label}]
let activeSource = null; // EventSource

/* ── tab-staff stepper ───────────────────────────────────────────────── */
/* Stages plotted like a rising riff: stage 1 on the low-E string (bottom),
   later stages on higher strings. Skipped stages render as an "x" — the tab
   symbol for a muted note. */

function buildStave() {
  els.stave.innerHTML = "";
  const n = stages.length;
  stages.forEach((s, i) => {
    const node = document.createElement("div");
    node.className = "stage";
    node.dataset.stage = s.id;
    node.setAttribute("role", "listitem");
    const string = n - 1 - i;              // row index from top (0 = high e)
    node.style.left = `${((i + 0.5) / n) * 100}%`;
    node.style.top = `calc((${string} + 0.5) / 6 * 100%)`;
    node.innerHTML =
      `<span class="fret">${i + 1}</span><span class="lbl">${s.label}</span>`;
    els.stave.appendChild(node);
  });
}

function setStage(id, state) {
  const node = els.stave.querySelector(`[data-stage="${id}"]`);
  if (!node) return;
  node.classList.remove("active", "done", "skipped", "error");
  if (state) node.classList.add(state);
  if (state === "skipped") node.querySelector(".fret").textContent = "x";
}

function resetStave() {
  stages.forEach((s, i) => {
    const node = els.stave.querySelector(`[data-stage="${s.id}"]`);
    if (!node) return;
    node.classList.remove("active", "done", "skipped", "error");
    node.querySelector(".fret").textContent = i + 1;
  });
}

/* ── engine discovery ────────────────────────────────────────────────── */

async function loadMeta() {
  try {
    const r = await fetch(`${API}/api/meta`);
    if (!r.ok) throw new Error(`engine answered ${r.status}`);
    meta = await r.json();
  } catch {
    els.chipEngine.textContent = "engine: not found";
    els.chipEngine.classList.add("err");
    els.connectBar.hidden = false;
    return;
  }
  els.connectBar.hidden = true;
  stages = meta.stages;
  buildStave();

  els.chips.innerHTML = "";
  const chip = (label, on) => {
    const c = document.createElement("span");
    c.className = `chip ${on ? "on" : ""}`;
    c.textContent = label;
    els.chips.appendChild(c);
  };
  chip(`engine v${meta.version}`, true);
  chip(meta.musescore ? "musescore: ready" : "musescore: not found", meta.musescore);
  chip(meta.piano_model ? "piano model: ready" : "piano model: basic pitch", meta.piano_model);
  chip(meta.chord_detection ? "chords: ready" : "chords: unavailable", meta.chord_detection);

  if (!meta.musescore) {
    els.pdf.checked = false;
    els.pdfHint.textContent = "MuseScore not found on PATH — PDF unavailable";
  }
  if (!meta.chord_detection) {
    els.chords.checked = false;
    els.chordsRow.classList.add("disabled");
  }
  updateEnablement();
}

els.connectForm.addEventListener("submit", (e) => {
  e.preventDefault();
  API = els.engineUrl.value.trim().replace(/\/+$/, "");
  localStorage.setItem("scoreforge-engine", API);
  loadMeta();
});

/* ── option interlocks ───────────────────────────────────────────────── */

function opt(name) {
  return document.querySelector(`input[name="${name}"]:checked`).value;
}

function updateEnablement() {
  const instrument = opt("instrument");
  const source = opt("source");
  // vocal melody needs a vocals stem -> mix only
  els.melodyRow.classList.toggle("disabled", source === "solo");
  if (source === "solo") els.melody.checked = false;
  // chords + max fret are embedded in the guitar tab only
  const guitar = instrument === "guitar";
  els.chordsRow.classList.toggle("disabled", !guitar || !(meta && meta.chord_detection));
  els.fretRow.style.display = guitar ? "" : "none";
  els.forgeBtn.disabled = !file || !meta;
}

document.querySelectorAll('input[name="instrument"], input[name="source"]')
  .forEach((r) => r.addEventListener("change", updateEnablement));

/* ── file selection ──────────────────────────────────────────────────── */

function takeFile(f) {
  if (!f) return;
  file = f;
  els.dropzone.classList.add("has-file");
  els.dropLabel.textContent = f.name;
  els.dropSub.textContent = `${(f.size / 1e6).toFixed(1)} MB — ready`;
  updateEnablement();
}

els.fileInput.addEventListener("change", () => takeFile(els.fileInput.files[0]));
["dragenter", "dragover"].forEach((ev) =>
  els.dropzone.addEventListener(ev, (e) => {
    e.preventDefault();
    els.dropzone.classList.add("drag");
  }));
["dragleave", "drop"].forEach((ev) =>
  els.dropzone.addEventListener(ev, (e) => {
    e.preventDefault();
    els.dropzone.classList.remove("drag");
  }));
els.dropzone.addEventListener("drop", (e) => takeFile(e.dataTransfer.files[0]));

/* ── job submission + SSE progress ───────────────────────────────────── */

els.forgeBtn.addEventListener("click", async () => {
  if (!file) return;
  els.forgeBtn.disabled = true;
  els.resultsCard.hidden = true;
  els.log.textContent = "";
  els.logCard.hidden = false;
  resetStave();
  els.staveStatus.classList.remove("error");
  els.staveStatus.textContent = "Sending the song to the engine…";

  const fd = new FormData();
  fd.append("file", file);
  fd.append("instrument", opt("instrument"));
  fd.append("source", opt("source"));
  fd.append("vocal_melody", els.melody.checked);
  fd.append("skip_chords", !els.chords.checked);
  fd.append("skip_render", !els.pdf.checked);
  fd.append("debug", els.debug.checked);
  fd.append("max_fret", els.maxFret.value || 19);

  let job;
  try {
    const r = await fetch(`${API}/api/jobs`, { method: "POST", body: fd });
    if (!r.ok) throw new Error((await r.json()).detail || `engine answered ${r.status}`);
    job = await r.json();
  } catch (err) {
    els.staveStatus.textContent = `Couldn't start: ${err.message}`;
    els.staveStatus.classList.add("error");
    els.forgeBtn.disabled = false;
    return;
  }
  followJob(job.id);
});

function followJob(id) {
  const t0 = Date.now();
  const timer = setInterval(() => {
    els.logTime.textContent = `${Math.round((Date.now() - t0) / 1000)}s`;
  }, 1000);

  if (activeSource) activeSource.close();
  const es = new EventSource(`${API}/api/jobs/${id}/events`);
  activeSource = es;

  es.onmessage = (m) => {
    const ev = JSON.parse(m.data);
    if (ev.type === "log") {
      appendLog(ev.line);
    } else if (ev.type === "stage") {
      setStage(ev.stage, ev.state);
      if (ev.state === "active" || ev.state === "skipped") {
        els.staveStatus.textContent = ev.message || "";
      }
    } else if (ev.type === "status") {
      if (ev.status === "queued") els.staveStatus.textContent = ev.message;
      if (ev.status === "running") els.staveStatus.textContent = "Working…";
      if (ev.status === "error") {
        els.staveStatus.textContent = ev.message;
        els.staveStatus.classList.add("error");
        const active = els.stave.querySelector(".stage.active");
        if (active) { active.classList.remove("active"); active.classList.add("error"); }
      }
      if (ev.status === "done") els.staveStatus.textContent = "Done.";
    }
  };

  es.addEventListener("end", async () => {
    es.close();
    clearInterval(timer);
    els.forgeBtn.disabled = false;
    const r = await fetch(`${API}/api/jobs/${id}`);
    const job = await r.json();
    if (job.status === "done") showResults(job);
  });

  es.onerror = () => {
    // connection dropped (engine stopped?) — leave the log as-is
    clearInterval(timer);
  };
}

function appendLog(line) {
  const safe = line.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
  els.log.insertAdjacentHTML("beforeend",
    safe.replace(/^(\[[a-z]+\])/, '<span class="tag">$1</span>') + "\n");
  els.log.scrollTop = els.log.scrollHeight;
}

/* ── results ─────────────────────────────────────────────────────────── */

function showResults(job) {
  els.resultsCard.hidden = false;
  els.summary.innerHTML = "";
  const chip = (text) => {
    const c = document.createElement("span");
    c.className = "chip";
    c.textContent = text;
    els.summary.appendChild(c);
  };
  const s = job.summary || {};
  chip(`${s.instrument} · ${s.source}`);
  if (s.transcription_backend) chip(`notes: ${s.transcription_backend}`);
  if (s.melody_note_count) chip(`vocal melody: ${s.melody_note_count} notes`);
  const capo = s.capo_suggestion || {};
  if (capo.total) chip(`capo ${capo.capo}: ${capo.open_shape_count}/${capo.total} open shapes`);
  if (s.chord_mismatch_count) chip(`${s.chord_mismatch_count} measures worth re-checking`);

  els.downloads.innerHTML = "";
  Object.entries(job.files || {}).forEach(([key, f]) => {
    const a = document.createElement("a");
    a.className = "dl";
    a.href = `${API}/api/jobs/${job.id}/files/${key}`;
    a.innerHTML = `<b>${f.label}</b><small>${f.name}</small>`;
    els.downloads.appendChild(a);
  });
  els.resultsCard.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

els.againBtn.addEventListener("click", () => {
  file = null;
  els.fileInput.value = "";
  els.dropzone.classList.remove("has-file");
  els.dropLabel.textContent = "Drop a song here";
  els.dropSub.textContent = "mp3 · wav · flac · m4a — or click to browse";
  els.resultsCard.hidden = true;
  els.logCard.hidden = true;
  resetStave();
  els.staveStatus.classList.remove("error");
  els.staveStatus.textContent = "Waiting for a song.";
  updateEnablement();
  window.scrollTo({ top: 0, behavior: "smooth" });
});

/* ── go ──────────────────────────────────────────────────────────────── */

loadMeta();
