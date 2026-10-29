"""ScoreForge local web GUI: a FastAPI server wrapping the pipeline.

Architecture (see docs/research/deployment-gui-survey.md for why):
  - Local-first: this server runs on the user's machine (the pipeline needs
    PyTorch/TensorFlow and minutes of compute -- Cloudflare Workers can't host it).
  - It serves its own bundled static frontend from ``scoreforge/static/`` --
    same-origin, so no CORS/mixed-content issues in normal use. CORS is still
    enabled so the Cloudflare-Pages-hosted copy of the same frontend can talk to
    a local engine if the user opts into that.
  - Jobs run on ONE background worker thread (Demucs alone can eat all RAM/CPU;
    parallel conversions would thrash), with per-stage progress streamed to the
    browser over SSE (simpler than WebSocket for one-directional updates).

Run with:  python -m scoreforge   (see __main__.py; default http://127.0.0.1:8765)
"""

from __future__ import annotations

import contextlib
import io
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .pipeline import INSTRUMENTS, SOURCES, run_pipeline
from .render import find_musescore

STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_OUTPUT_ROOT = Path("output")

ALLOWED_UPLOAD_SUFFIXES = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".opus", ".wma"}

# Stage ids in pipeline order; the frontend renders these as the progress stepper.
STAGES: List[Dict[str, str]] = [
    {"id": "separate", "label": "Separate stems"},
    {"id": "transcribe", "label": "Transcribe notes"},
    {"id": "chords", "label": "Detect chords"},
    {"id": "melody", "label": "Extract vocal melody"},
    {"id": "tab", "label": "Build score"},
    {"id": "render", "label": "Render PDF"},
]
_STAGE_IDS = [s["id"] for s in STAGES]

# Pipeline result keys that are downloadable files, with human labels.
_DOWNLOADABLE = {
    "gp5": "Guitar tab (.gp5)",
    "tab_txt": "Guitar tab (plain text)",
    "piano_midi": "Piano MIDI (.mid)",
    "melody_gp5": "Vocal melody tab (.gp5)",
    "melody_tab_txt": "Vocal melody tab (plain text)",
    "pdf": "Score PDF",
    "midi": "Raw transcription MIDI",
    "debug_csv": "Fret-assignment debug CSV",
}


@dataclass
class Job:
    id: str
    filename: str
    options: Dict[str, object]
    status: str = "queued"  # queued | running | done | error
    error: Optional[str] = None
    events: List[Dict[str, object]] = field(default_factory=list)
    files: Dict[str, Path] = field(default_factory=dict)
    summary: Dict[str, object] = field(default_factory=dict)
    created: float = field(default_factory=time.time)
    upload_path: Optional[Path] = None
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def emit(self, event: Dict[str, object]) -> None:
        with self.lock:
            event["seq"] = len(self.events)
            event["t"] = round(time.time() - self.created, 2)
            self.events.append(event)

    def public_state(self) -> Dict[str, object]:
        with self.lock:
            return {
                "id": self.id,
                "filename": self.filename,
                "options": self.options,
                "status": self.status,
                "error": self.error,
                "created": self.created,
                "events": list(self.events),
                "files": {
                    key: {"label": _DOWNLOADABLE.get(key, key), "name": path.name}
                    for key, path in self.files.items()
                },
                "summary": self.summary,
            }


class JobRunner:
    """Single-worker job queue. One conversion at a time, by design."""

    def __init__(self, output_root: Path = DEFAULT_OUTPUT_ROOT) -> None:
        self.output_root = Path(output_root)
        self.jobs: Dict[str, Job] = {}
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def submit(self, job: Job) -> None:
        self.jobs[job.id] = job
        job.emit({"type": "status", "status": "queued",
                  "message": "Queued (one conversion runs at a time)"})
        self._queue.put(job.id)

    def _worker(self) -> None:
        while True:
            job_id = self._queue.get()
            job = self.jobs.get(job_id)
            if job is None:
                continue
            self._run_job(job)

    def _run_job(self, job: Job) -> None:
        job.status = "running"
        job.emit({"type": "status", "status": "running", "message": "Started"})

        active_stage: Dict[str, Optional[str]] = {"id": None}

        def progress(stage: str, message: str) -> None:
            prev = active_stage["id"]
            if prev and prev != stage:
                job.emit({"type": "stage", "stage": prev, "state": "done"})
            active_stage["id"] = stage
            skipped = "skipped" in message.lower()
            job.emit({"type": "stage", "stage": stage,
                      "state": "skipped" if skipped else "active",
                      "message": message})

        class _LogWriter(io.TextIOBase):
            def write(self, text: str) -> int:  # type: ignore[override]
                for line in text.splitlines():
                    if line.strip():
                        job.emit({"type": "log", "line": line})
                return len(text)

        try:
            opts = job.options
            with contextlib.redirect_stdout(_LogWriter()):
                result = run_pipeline(
                    song_path=job.upload_path,
                    output_root=self.output_root,
                    instrument=str(opts["instrument"]),
                    source=str(opts["source"]),
                    vocal_melody=bool(opts["vocal_melody"]),
                    skip_chords=bool(opts["skip_chords"]),
                    skip_render=bool(opts["skip_render"]),
                    max_fret=int(opts["max_fret"]),
                    debug=bool(opts["debug"]),
                    jobs=int(opts["jobs"]),
                    progress=progress,
                )
            if active_stage["id"]:
                job.emit({"type": "stage", "stage": active_stage["id"], "state": "done"})

            job.files = {
                key: Path(result[key])  # type: ignore[arg-type]
                for key in _DOWNLOADABLE
                if result.get(key) and Path(str(result[key])).exists()
            }
            job.summary = {
                "instrument": result["instrument"],
                "source": result["source"],
                "transcription_backend": result["transcription_backend"],
                "measure_chords": result["measure_chords"],
                "chord_mismatch_count": result["chord_mismatch_count"],
                "capo_suggestion": result["capo_suggestion"],
                "melody_note_count": result["melody_note_count"],
            }
            job.status = "done"
            job.emit({"type": "status", "status": "done", "message": "Conversion complete"})
        except Exception as exc:  # noqa: BLE001 - job errors go to the UI, not a crash
            job.status = "error"
            job.error = f"{type(exc).__name__}: {exc}"
            job.emit({"type": "status", "status": "error", "message": job.error})


def create_app(output_root: Path = DEFAULT_OUTPUT_ROOT) -> FastAPI:
    app = FastAPI(title="ScoreForge", version="0.2.0")
    runner = JobRunner(output_root=output_root)
    app.state.runner = runner

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # allows the Cloudflare-Pages-hosted frontend copy
        allow_methods=["*"],
        allow_headers=["*"],
    )

    upload_dir = Path(output_root) / "_uploads"

    @app.get("/api/meta")
    def meta() -> Dict[str, object]:
        try:
            import importlib.util
            has_piano_model = importlib.util.find_spec("piano_transcription_inference") is not None
            has_madmom = importlib.util.find_spec("madmom") is not None
        except Exception:  # pragma: no cover
            has_piano_model = has_madmom = False
        return {
            "name": "ScoreForge",
            "version": "0.2.0",
            "stages": STAGES,
            "instruments": list(INSTRUMENTS),
            "sources": list(SOURCES),
            "musescore": bool(find_musescore()),
            "piano_model": has_piano_model,
            "chord_detection": has_madmom,
        }

    @app.post("/api/jobs")
    async def create_job(
        file: UploadFile = File(...),
        instrument: str = Form("guitar"),
        source: str = Form("mix"),
        vocal_melody: bool = Form(False),
        skip_chords: bool = Form(False),
        skip_render: bool = Form(False),
        debug: bool = Form(False),
        max_fret: int = Form(19),
        jobs: int = Form(0),
    ) -> JSONResponse:
        if instrument not in INSTRUMENTS:
            raise HTTPException(422, f"instrument must be one of {INSTRUMENTS}")
        if source not in SOURCES:
            raise HTTPException(422, f"source must be one of {SOURCES}")
        suffix = Path(file.filename or "song.mp3").suffix.lower()
        if suffix not in ALLOWED_UPLOAD_SUFFIXES:
            raise HTTPException(
                422, f"unsupported audio type {suffix!r}; expected one of "
                     f"{sorted(ALLOWED_UPLOAD_SUFFIXES)}")

        job_id = uuid.uuid4().hex[:12]
        upload_dir.mkdir(parents=True, exist_ok=True)
        # Keep the song's own stem: pipeline output dirs are named after it.
        safe_stem = "".join(
            c for c in Path(file.filename or "song").stem if c.isalnum() or c in "-_ "
        ).strip() or "song"
        upload_path = upload_dir / f"{safe_stem}{suffix}"
        n = 1
        while upload_path.exists():
            upload_path = upload_dir / f"{safe_stem}_{n}{suffix}"
            n += 1
        upload_path.write_bytes(await file.read())

        job = Job(
            id=job_id,
            filename=file.filename or upload_path.name,
            upload_path=upload_path,
            options={
                "instrument": instrument, "source": source,
                "vocal_melody": vocal_melody, "skip_chords": skip_chords,
                "skip_render": skip_render, "debug": debug,
                "max_fret": max_fret, "jobs": jobs,
            },
        )
        runner.submit(job)
        return JSONResponse(job.public_state(), status_code=201)

    @app.get("/api/jobs")
    def list_jobs() -> List[Dict[str, object]]:
        return [
            {k: v for k, v in job.public_state().items() if k != "events"}
            for job in sorted(runner.jobs.values(), key=lambda j: j.created, reverse=True)
        ]

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> Dict[str, object]:
        job = runner.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "no such job")
        return job.public_state()

    @app.get("/api/jobs/{job_id}/events")
    async def job_events(job_id: str) -> StreamingResponse:
        job = runner.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "no such job")

        async def stream():
            import asyncio
            import json
            sent = 0
            while True:
                with job.lock:
                    pending = job.events[sent:]
                    status = job.status
                for event in pending:
                    yield f"data: {json.dumps(event)}\n\n"
                sent += len(pending)
                if status in ("done", "error") and sent >= len(job.events):
                    yield "event: end\ndata: {}\n\n"
                    return
                await asyncio.sleep(0.4)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    @app.get("/api/jobs/{job_id}/files/{key}")
    def download(job_id: str, key: str) -> FileResponse:
        job = runner.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "no such job")
        path = job.files.get(key)
        if path is None or not path.exists():
            raise HTTPException(404, f"job has no downloadable file {key!r}")
        return FileResponse(path, filename=path.name)

    # Static frontend last, so /api/* wins. html=True serves index.html at "/".
    if STATIC_DIR.exists():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

    return app
