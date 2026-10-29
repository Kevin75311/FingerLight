---
name: deployment-gui-survey
aliases: [Deployment GUI survey]
tags: [research, gui]
---

# Deployment + GUI architecture survey — Guitar Tab Generator

Researched 2026-07-11. All findings via WebSearch/WebFetch against primary docs; exact
quotes/numbers below with source URLs. Marked `[ASSUMED]` where a claim could not be
directly sourced.

---

## 1. Can the Demucs+Basic Pitch pipeline run on Cloudflare (mid-2026)?

**Bottom line: No, for all "edge" products (Workers, Pages Functions, Workers AI). Cloudflare
Containers is the only Cloudflare product that could physically run this pipeline, but it
has no GPU, so it would be slow (CPU-only Demucs/Basic Pitch) and non-trivial to package.**

### Workers — NO
- Runtime is a V8 isolate: no native binaries, no Python/PyTorch/TensorFlow execution
  environment at all (confirmed: the limits page has zero mention of native binary or
  Python support — this is a JS/Wasm sandbox by design).
- Memory: **128 MB per isolate** (JS heap + Wasm allocations combined) — far below what
  a ~1-3 GB PyTorch model needs just to load.
- CPU time: default **30 seconds**, raisable on the Paid plan to a max of **5 minutes
  (300,000 ms)**; Free plan is **10 ms**. Even the 5-minute ceiling is tight for
  multi-stage Demucs+Basic Pitch inference on a full song, and moot given the memory cap.
- Source: https://developers.cloudflare.com/workers/platform/limits/ (dateModified
  2026-07-05)

### Pages Functions — NO (same verdict, same underlying runtime)
- Pages Functions run on the same Workers runtime ("Pages supports the Standard usage
  model" and requests count against the same Workers quota), so the same 128 MB memory
  and CPU-time ceilings apply. No separate, more permissive limit exists for Pages
  Functions.
- Source: https://developers.cloudflare.com/pages/platform/limits/ ,
  cross-ref https://developers.cloudflare.com/workers/platform/limits/

### Workers AI — NO (no matching model in the catalog)
- Catalog has audio models, but none fit this use case: Whisper / Whisper-large-v3-turbo
  (speech-to-text, not music transcription), Deepgram Nova-3/Flux and Aura (speech
  STT/TTS for voice agents), MiniMax music-generation (text/lyrics → song, the opposite
  direction). Nothing does source separation (Demucs) or polyphonic instrument
  transcription (Basic Pitch's job) — no stem-splitting or note-transcription model
  exists in the catalog.
- Source: https://developers.cloudflare.com/workers-ai/models/

### Cloudflare Containers — Technically capable of running it, but CPU-only and requires
packaging as a Docker image; verdict is "possible but not attractive" rather than a clean yes.
- **GA date:** announced 2026-04-13 ("Containers and Sandboxes are now generally
  available"). Source:
  https://developers.cloudflare.com/changelog/post/2026-04-13-containers-sandbox-ga/
- **Instance types** (six predefined, plus custom), from
  https://developers.cloudflare.com/containers/platform-details/limits/ (modified
  2026-07-03):

  | Type | vCPU | Memory | Disk |
  |---|---|---|---|
  | lite | 1/16 | 256 MiB | 2 GB |
  | basic | 1/4 | 1 GiB | 4 GB |
  | standard-1 | 1/2 | 4 GiB | 8 GB |
  | standard-2 | 1 | 6 GiB | 12 GB |
  | standard-3 | 2 | 8 GiB | 16 GB |
  | standard-4 | 4 | 12 GiB | 20 GB |

  Account caps: 6 TiB concurrent memory, 1,500 concurrent vCPU, 30 TB concurrent disk,
  50 GB total image storage.
- **GPU: not offered.** Neither the limits page nor the pricing page mentions GPU
  instance types — Demucs/Basic Pitch would run CPU-only, i.e. slow (this repo's own
  CLAUDE.md already describes Demucs as "minutes of CPU/GPU per song"; CPU-only on a
  max 4-vCPU instance would push that toward the high end of minutes-to-tens-of-minutes
  per song).
- **Pricing** (https://developers.cloudflare.com/containers/pricing/ , modified
  2026-04-21), on top of the required $5/mo Workers Paid plan, billed in 10ms
  increments, active-CPU model:
  - vCPU: **$0.000020 per vCPU-second** beyond 375 included vCPU-minutes/month
  - Memory: **$0.0000025 per GiB-second** beyond 25 included GiB-hours/month
  - Disk: **$0.00000007 per GB-second** beyond 200 included GB-hours/month
  - No free tier (Free plan column is "N/A" across the board).
- Practical blocker not costed above: shipping PyTorch + TensorFlow + Demucs + Basic
  Pitch + their model weights (~GBs) inside a container image, cold-start pull time, and
  fitting within 50 GB total image storage account-wide — workable but real packaging
  effort, not a deploy-as-is.

**Verdict per option:**
| Option | Can run this pipeline? |
|---|---|
| Workers | No — 128 MB memory / no Python runtime |
| Pages Functions | No — same Workers runtime/limits |
| Workers AI | No — no source-separation or instrument-transcription model in catalog |
| Cloudflare Containers | Technically yes, CPU-only, no free tier, real packaging effort |

---

## 2. Free/cheap real-compute alternatives — light scan, one line each

- **Hugging Face Spaces**: Free CPU Spaces (2 vCPU / 16 GB RAM) exist, plus free
  "ZeroGPU" dynamic H200 access, but ZeroGPU is capped at **3.5 minutes/day** for
  free/authenticated users (25 min/day on the $9/mo PRO plan) — workable for a public
  demo of light jobs, not for repeated full-pipeline runs.
  Source: https://huggingface.co/docs/hub/en/spaces-zerogpu ,
  https://huggingface.co/docs/hub/en/spaces-gpus
- **Modal**: Serverless containers (CPU+GPU), **$30/month free credit** on the Starter
  plan, pay-per-second (CPU ≈ $0.0000131/core/sec; GPU e.g. T4 ≈ $0.000164/sec, H100 ≈
  $0.001097/sec) — good fit technically (arbitrary Docker/Python, real GPUs), free
  credit likely covers light personal use only. Source: https://modal.com/pricing
- **Replicate**: No free tier; pay-per-second by hardware class (CPU ≈ $0.09–0.36/hr,
  GPU T4 ≈ $0.81/hr up to 8×H100 ≈ $43.92/hr); supports deploying arbitrary custom
  models via **Cog** (their Docker-packaging tool) — viable but costs from the first run.
  Source: https://replicate.com/pricing

---

## 3. Local Windows GUI framework for a long-running pipeline

Comparison for: file upload, option selection, per-stage progress streaming, file
download, **polished custom dark-aesthetic UI with a stepper/progress view**.

| | FastAPI + custom static SPA | Gradio | NiceGUI |
|---|---|---|---|
| Visual control | Full — hand-authored HTML/CSS/JS, any design system | Limited — Tailwind-based theming, not built for bespoke pixel-level design; the Gradio team's own tracking issue notes Tailwind "doesn't exclusively use CSS variables," requiring a rebuild for new themes (gradio-app/gradio#2353) | High — built on Quasar/Vue + Tailwind utility classes (`.classes()`/`.props()`/`.style()`), but NiceGUI 3.0 had to introduce CSS layers specifically to fix Quasar `!important` vs. user-CSS precedence fights, and users report custom CSS getting harder to override post-3.0 (zauberzeug/nicegui #5240) |
| Progress channel | Either SSE or WebSocket, you choose and implement | **SSE**, built in — "when a request finishes, the Gradio server returns the result using server-side events," generator functions stream automatically | **WebSocket**, built in — "NiceGUI sits on FastAPI for HTTP and uses WebSockets for live, push-based updates" |
| Effort for a bespoke stepper UI | Highest effort, but no fighting a component library | Not really designed for this — it's a components-in-a-box demo tool | Medium — real page/routing control, but still inheriting Quasar's component look-and-feel underneath |
| Source | — | https://gradio.app/guides/server-mode , https://gradio.app/guides/custom-CSS-and-JS | https://nicegui.io/documentation , https://deepwiki.com/zauberzeug/nicegui/7.2-styling-and-theming |

**Recommendation: FastAPI + a custom static SPA**, for the stated goal ("polished,
custom-designed dark-aesthetic UI with a stepper/progress view"). Both Gradio and
NiceGUI impose a visible component-library identity (Gradio's Tailwind demo look;
NiceGUI's Quasar/Vue look) that fights bespoke design rather than enabling it — exactly
the failure mode this repo should avoid per the "taste calls" framing. FastAPI as the
backend is also the natural fit for this project already being a Python pipeline
(subprocess orchestration of Demucs/Basic Pitch CLIs, per `pipeline.py`) with no
framework-imposed UI layer to route around.

**SSE vs. WebSocket, per option:**
- **FastAPI + custom SPA**: **SSE is the saner channel.** Progress is one-directional
  (server → browser: stage name, percent, log lines); SSE is plain HTTP (survives
  simple reverse proxies, auto-reconnects natively via `EventSource`), avoids
  WebSocket's extra handshake/keepalive machinery for a link that never needs
  client → server push mid-job. Reserve WebSocket only if two-way control (e.g.
  cancel-job) is wanted on the same channel.
- **Gradio**: SSE is what you get regardless — it's the framework's built-in mechanism,
  not a choice.
- **NiceGUI**: WebSocket is what you get regardless — same reasoning, it's baked into
  the framework's live-update model.

---

## 4. Cloudflare Pages (https) frontend calling a local http://localhost:PORT backend

**Verdict: workable in principle today (mid-2026), but fragile and getting more fragile
— local mode should ship its own frontend copy rather than depend on the hosted one.**

### Mixed content: localhost/loopback IS exempt, with one landmine
- MDN, verbatim: *"Local resources are considered to be from secure origins, just like
  HTTPS origins. This includes `file:` URLs, and content accessed from loopback
  addresses such as `http://127.0.0.1/` or `http://localhost/`."*
  Source: https://developer.mozilla.org/en-US/docs/Web/Security/Defenses/Mixed_content
- Firefox: confirmed same behavior — Firefox 84+ treats `http://localhost/` and
  `http://*.localhost/` as loopback and therefore secure-context-safe (Bugzilla
  1488740, cross-checked via search).
- **Landmine**: MDN's own mixed-content-upgrade section notes upgradable mixed content
  is blocked, not upgraded, "if the URL's host is an IP address rather than a domain
  name" — i.e. `http://127.0.0.1/...` is treated differently from `http://localhost/...`
  in the *upgrade* pathway specifically, even though both are exempted from being
  flagged as insecure in the first place per the quote above. Practical guidance: use
  `http://localhost:PORT`, not `http://127.0.0.1:PORT`, in the frontend's fetch URL, to
  stay clear of any host-is-IP edge case.

### New Chrome complication (post mixed-content): Local Network Access permission
- Chrome is layering a **separate, additional permission prompt** on top of the
  mixed-content exemption, specifically for public-site → local-network requests
  (CSRF-on-your-router protection). Verbatim: local-network-access permission-gated
  requests are *"exempted from mixed content checks if Chrome knows that the requests
  will be going to the local network before resolving the destination."* So the
  mixed-content exemption still holds, but a **new user-facing permission prompt** is
  being inserted in front of it.
- Rollout per Chrome's own blog: Chrome 138 opt-in via flag, Chrome 139+ origin trial,
  **Chrome 142: "The Local Network Access permission prompt is launching."** No firm
  date given yet for mandatory/default-on enforcement.
  Source: https://developer.chrome.com/blog/local-network-access
- Practical effect for this project: a visitor on a current-enough Chrome will likely
  see an extra "allow this site to access your local network" prompt before the hosted
  Pages frontend can reach `localhost`. That's a real UX hit (silent failures if the
  user dismisses it) that Firefox does not currently impose.

### CORS: required regardless of the mixed-content answer
- Mixed-content exemption only stops the browser from blocking the *request* as
  insecure; it does **not** grant cross-origin access. The local FastAPI backend must
  still send CORS headers because the request's origin is the Pages domain (e.g.
  `https://guitar-tab.pages.dev`), not `localhost`:
  - `Access-Control-Allow-Origin: https://guitar-tab.pages.dev` (or the specific Pages
    origin(s) in use — not `*` if credentials/cookies are involved)
  - `Access-Control-Allow-Methods: GET, POST, OPTIONS` (whatever the API surface needs)
  - `Access-Control-Allow-Headers:` echoing whatever custom headers the SPA sends
  - For SSE progress specifically: the `EventSource`/fetch-stream request is a GET, so
    it needs the same `Access-Control-Allow-Origin` — SSE has no separate CORS carve-out.
  - Source: general CORS spec behavior via MDN mixed-content page context; FastAPI's
    `CORSMiddleware` is the standard way to add these headers
    [not independently re-verified against FastAPI docs this pass — `ASSUMED` at the
    "how to configure in FastAPI" level of detail, not at the "these headers are
    required" level, which is standard CORS spec behavior].

### Recommendation
"Hosted frontend + local engine" is **workable today** (loopback mixed-content
exemption is real and stable in both engines, CORS is solvable with standard
middleware), but two things argue against depending on it long-term for this project:
1. Chrome's Local Network Access prompt (landing as of Chrome 142) adds a user-facing
   permission gate with no committed enforcement timeline — a dependency this project
   doesn't control and that will change under it.
2. It requires the local backend to always run with correct CORS config scoped to the
   specific Pages origin, which is one more moving part to keep in sync across
   local/hosted deploys.

**Recommendation: local mode should serve its own frontend copy** (FastAPI serving the
built SPA's static files directly, `http://localhost:PORT/` same-origin, zero CORS/
mixed-content/local-network-permission surface at all). Keep the Cloudflare
Pages-hosted copy as a separate, purely-informational/demo deployment (e.g. showing a
recorded run or a cloud-executed lightweight preview), not as the primary way to drive
a user's local pipeline.

---

## Verified vs. assumed

- **Verified** (fetched primary docs, quotes above sourced): Workers CPU/memory limits,
  Pages Functions runtime identity with Workers, Workers AI model catalog contents,
  Cloudflare Containers GA date/instance types/pricing/no-GPU, MDN mixed-content
  loopback exemption wording, Chrome Local Network Access blog wording/rollout,
  Modal/Replicate/HF Spaces pricing figures (via WebFetch on modal.com/pricing,
  replicate.com/pricing; HF Spaces figures via WebSearch summarizing
  huggingface.co/docs, not independently re-fetched this pass), Gradio SSE-by-default
  and NiceGUI WebSocket-by-default behavior (via their own guide pages), Gradio/NiceGUI
  theming friction (via their own GitHub issues/discussions).
- **Assumed / not independently re-verified this pass**: exact FastAPI `CORSMiddleware`
  configuration syntax (standard, well-documented, but not re-fetched from
  fastapi.tiangolo.com this pass); HF Spaces free-CPU-tier "2 vCPU/16GB" figure and
  ZeroGPU "3.5 min/day" figure came from a WebSearch synthesis rather than a direct
  WebFetch of huggingface.co/docs — treat as reliable but not primary-source-quoted;
  no firm date exists yet for Chrome's Local Network Access default-enforcement (Chrome
  142 is confirmed only for the *prompt launching*, not full rollout) — flagged as an
  open unknown, not guessed.
