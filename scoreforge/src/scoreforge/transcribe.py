"""Stage 2: automatic music transcription via Spotify's Basic Pitch.

Shells out to the ``basic-pitch`` CLI (installed via
``pip install -e external/basic-pitch``, see README) rather than importing it as a
library, so this module has no hard dependency on basic-pitch/tensorflow just to be
imported.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Dict, Optional

from .util import find_executable


def _convert_to_pcm16(wav_path: Path) -> None:
    """Basic Pitch's ``--sonify-midi`` writes 64-bit float WAV, which most
    lightweight players (including VS Code's built-in preview) can't open even
    though it's a valid file. Convert in place to 16-bit PCM, the same format
    Demucs's stems already use, so every debug .wav is consistently listenable."""
    try:
        import soundfile as sf
    except ImportError:
        print(f"[transcribe] Note: {wav_path} is 64-bit float WAV and may not open in "
              f"some players; install `soundfile` to auto-convert to 16-bit PCM.")
        return

    try:
        data, samplerate = sf.read(wav_path)
        sf.write(wav_path, data, samplerate, subtype="PCM_16")
    except Exception as e:  # pragma: no cover - best-effort convenience step
        print(f"[transcribe] Warning: failed to convert {wav_path} to PCM16 ({e}); "
              f"the file is still valid, just possibly hard to open in some players.")


def transcribe(
    audio_path: Path,
    output_dir: Path,
    debug: bool = False,
    onset_threshold: Optional[float] = None,
    frame_threshold: Optional[float] = None,
    minimum_note_length: Optional[float] = None,
    minimum_frequency: Optional[float] = None,
    maximum_frequency: Optional[float] = None,
) -> Dict[str, Optional[Path]]:
    """Run Basic Pitch on ``audio_path``, writing outputs under ``output_dir``.

    The tuning parameters (``onset_threshold``/``frame_threshold``/etc.) map directly
    to Basic Pitch's own CLI flags (``basic-pitch --help`` for the exact semantics of
    each) and are left unset (Basic Pitch's own defaults) unless given -- they exist so
    the transcription stage can be experimented on directly, e.g. constraining
    ``minimum_frequency``/``maximum_frequency`` to a guitar's real range (~E2=82Hz to
    ~E6=1319Hz) to reject low-frequency bleed being misheard as bass notes, or raising
    ``onset_threshold`` to cut down on spurious/extra notes.

    When ``debug=True``, also requests Basic Pitch's ``--sonify-midi`` (renders the
    transcribed MIDI back to audio, for A/B listening against ``audio_path``) and
    ``--save-note-events`` (raw note list as CSV) outputs.

    Returns a dict: {"midi": Path, "sonified_wav": Path|None, "note_events_csv": Path|None}.
    """
    audio_path = Path(audio_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # basic-pitch's CLI silently skips writing an output (exit 0, no error) if its
    # target file already exists -- e.g. from a prior pipeline run. Clear any expected
    # target first so every run produces fresh output instead of quietly reusing stale
    # results.
    stem = audio_path.stem
    expected = {
        "midi": output_dir / f"{stem}_basic_pitch.mid",
        "sonified_wav": output_dir / f"{stem}_basic_pitch.wav",
        "note_events_csv": output_dir / f"{stem}_basic_pitch.csv",
    }
    for path in expected.values():
        if path.exists():
            path.unlink()

    basic_pitch_exe = find_executable("basic-pitch")
    cmd = [basic_pitch_exe, str(output_dir), str(audio_path)]
    if debug:
        cmd += ["--sonify-midi", "--save-note-events"]
    if onset_threshold is not None:
        cmd += ["--onset-threshold", str(onset_threshold)]
    if frame_threshold is not None:
        cmd += ["--frame-threshold", str(frame_threshold)]
    if minimum_note_length is not None:
        cmd += ["--minimum-note-length", str(minimum_note_length)]
    if minimum_frequency is not None:
        cmd += ["--minimum-frequency", str(minimum_frequency)]
    if maximum_frequency is not None:
        cmd += ["--maximum-frequency", str(maximum_frequency)]

    print(f"[transcribe] Running: {' '.join(cmd)}")
    # basic-pitch prints emoji; when its stdout is a pipe (e.g. under the web
    # GUI), Windows Python defaults to cp1252 and crashes with UnicodeEncodeError
    # before writing any output. Force UTF-8 for the child process.
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    subprocess.run(cmd, check=True, env=env)

    if expected["sonified_wav"].exists():
        _convert_to_pcm16(expected["sonified_wav"])

    if not expected["midi"].exists():
        candidates = sorted(output_dir.glob("*.mid"))
        if not candidates:
            raise FileNotFoundError(
                f"basic-pitch did not produce a .mid file under {output_dir}. Check "
                f"that basic-pitch is installed (pip install -e external/basic-pitch) "
                f"and ran without errors."
            )
        expected["midi"] = candidates[0]

    return {
        "midi": expected["midi"],
        "sonified_wav": expected["sonified_wav"] if expected["sonified_wav"].exists() else None,
        "note_events_csv": (
            expected["note_events_csv"] if expected["note_events_csv"].exists() else None
        ),
        "backend": "basic-pitch",
    }


def transcribe_piano(
    audio_path: Path,
    output_dir: Path,
    debug: bool = False,
    **basic_pitch_kwargs,
) -> Dict[str, Optional[Path]]:
    """Piano-specialized transcription: bytedance's high-resolution piano model if
    installed, Basic Pitch otherwise.

    ``piano_transcription_inference`` (MIT, ``pip install
    piano-transcription-inference``) is trained specifically on piano (MAESTRO;
    onset F1 ~96.7% per its paper -- see docs/research/model-survey.md) and
    substantially outperforms the instrument-agnostic Basic Pitch on piano audio,
    including pedal/sustain handling. It's a soft dependency: on ImportError this
    falls back to the regular Basic Pitch path so piano mode never hard-fails.
    First use downloads its ~165MB checkpoint to the user's home directory.

    Returns the same dict shape as :func:`transcribe` (piano backend has no
    sonified/CSV debug outputs).
    """
    audio_path = Path(audio_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        from piano_transcription_inference import (  # type: ignore
            PianoTranscription,
            load_audio,
            sample_rate,
        )
    except ImportError:
        print("[transcribe] piano_transcription_inference not installed -- falling "
              "back to Basic Pitch for piano. For noticeably better piano accuracy: "
              "pip install piano-transcription-inference")
        return transcribe(audio_path, output_dir, debug=debug, **basic_pitch_kwargs)

    try:
        import torch  # already installed via demucs
        device = "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:  # pragma: no cover - torch is a demucs dependency
        device = "cpu"

    midi_path = output_dir / f"{audio_path.stem}_piano_transcription.mid"
    print(f"[transcribe] Running bytedance piano_transcription_inference on {device} "
          f"(first run downloads a ~165MB checkpoint)...")
    audio, _ = load_audio(str(audio_path), sr=sample_rate, mono=True)
    transcriptor = PianoTranscription(device=device)
    transcriptor.transcribe(audio, str(midi_path))

    if not midi_path.exists():
        raise FileNotFoundError(
            f"piano_transcription_inference did not produce {midi_path}; check its "
            f"output above for errors."
        )

    return {
        "midi": midi_path,
        "sonified_wav": None,
        "note_events_csv": None,
        "backend": "piano_transcription_inference",
    }
