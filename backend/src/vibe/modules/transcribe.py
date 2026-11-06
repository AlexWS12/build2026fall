"""Transcription with word timestamps (F-31), cached per video.

Times are source seconds (D-013): ``extract_audio`` puts sample 0 at the clip's time 0, so a
word's ``start``/``end`` can go straight into a ``TimeRange`` for ``mute`` or ``cut_range``.

Backends (``VIBE_BACKEND``, docs/ARCHITECTURE.md "Compute"):
- ``stub``: no model. ``transcribe_video`` replays a cached transcript (e.g. one copied from a
  teammate's ``cache/<sha256>/transcript/``) and raises if there is none, never an empty one.
- ``local``: faster-whisper (MIT; tentative, no decision row yet). Not a project dependency:
  install it by hand with ``uv pip install faster-whisper``. The first run downloads the model.
- ``remote``: not built; raises.

``transcribe`` takes samples, not a file, so a per-chunk caller (live) can reuse it later.

CLI: ``uv run python -m vibe.modules.transcribe CLIP [--model small]``.
"""

import argparse
import sys
from pathlib import Path

import numpy as np

from vibe.config import Backend, model_backend
from vibe.contract.common import Model
from vibe.storage import Storage, StorageError, VideoRef

# shortcut: private storage helper; use a public one when storage exports it (shared file)
from vibe.storage.store import _atomic_write
from vibe.video import VideoError, extract_audio
from vibe.video.audio import DEFAULT_SAMPLE_RATE

DEFAULT_MODEL = "small"
COMPUTE_TYPE = "int8"  # shortcut: CPU int8 only; add a device option when someone needs a GPU
TRANSCRIPT_VERSION = 1
"""Bump when the cached format or the conversion below changes, so old caches aren't reused."""


class TranscribeError(Exception):
    """The backend can't transcribe (stub with no cache, package missing, not built)."""


class Word(Model):
    start: float
    end: float
    text: str
    probability: float


class Segment(Model):
    """Same field names as F-09's ``SpeechSegment``; ``speaker`` comes with F-32."""

    start: float
    end: float
    text: str
    words: list[Word]


class Transcript(Model):
    model: str
    language: str | None
    segments: list[Segment]


def transcript_key(model: str = DEFAULT_MODEL) -> str:
    """The analysis cache key, e.g. ``faster-whisper-small-int8@1``."""
    return f"faster-whisper-{model}-{COMPUTE_TYPE}@{TRANSCRIPT_VERSION}"


def transcribe(
    samples: np.ndarray,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    model: str = DEFAULT_MODEL,
    backend: Backend | None = None,
) -> Transcript:
    """Transcribe mono float32 samples at 16 kHz. Times are seconds from the first sample."""
    backend = backend or model_backend()
    if backend != "local":
        raise TranscribeError(
            f"the {backend!r} backend can't run Whisper (stub only replays cached transcripts); "
            "set VIBE_BACKEND=local"
        )
    if sample_rate != DEFAULT_SAMPLE_RATE or samples.ndim != 1:
        raise ValueError(
            f"Whisper needs mono audio at {DEFAULT_SAMPLE_RATE} Hz, "
            f"got shape {samples.shape} at {sample_rate} Hz"
        )
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise TranscribeError(
            "faster-whisper isn't installed: run `uv pip install faster-whisper` in backend/"
        ) from e
    whisper = WhisperModel(model, device="cpu", compute_type=COMPUTE_TYPE)
    segments, info = whisper.transcribe(samples.astype(np.float32), word_timestamps=True)
    return Transcript(
        model=transcript_key(model),
        language=info.language,
        segments=[
            Segment(
                start=s.start,
                end=s.end,
                text=s.text.strip(),
                words=[
                    Word(start=w.start, end=w.end, text=w.word.strip(), probability=w.probability)
                    for w in s.words or []
                ],
            )
            for s in segments  # a generator: Whisper runs as it's consumed
        ],
    )


def cached_transcript(
    storage: Storage, video: VideoRef, model: str = DEFAULT_MODEL
) -> Transcript | None:
    """The cached transcript, or ``None`` if the video was never transcribed with ``model``."""
    path = storage.cache_path(video.sha256, "transcript", transcript_key(model))
    return Transcript.model_validate_json(path.read_bytes()) if path.exists() else None


def transcribe_video(
    storage: Storage,
    video: VideoRef,
    model: str = DEFAULT_MODEL,
    backend: Backend | None = None,
) -> Transcript:
    """The video's transcript from the analysis cache, transcribing and caching it if needed."""
    backend = backend or model_backend()
    if (cached := cached_transcript(storage, video, model)) is not None:
        return cached
    path = storage.cache_path(video.sha256, "transcript", transcript_key(model))
    if backend == "stub":
        raise TranscribeError(
            f"no cached transcript {path.name} for video {video.sha256[:12]}: run with "
            f"VIBE_BACKEND=local, or copy cache/{video.sha256}/transcript/ from a teammate"
        )
    samples, rate = extract_audio(storage.video_path(video))
    transcript = transcribe(samples, rate, model, backend)
    _atomic_write(path, transcript.model_dump_json() + "\n")
    return transcript


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m vibe.modules.transcribe",
        description="Transcribe a video with word timestamps (F-31) into the analysis cache. "
        "The video is stored in VIBE_DATA_DIR first, like an upload.",
    )
    parser.add_argument("video", type=Path)
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Whisper size (default: small)")
    args = parser.parse_args(argv)
    # Windows pipes default to cp1252, which can't print every language's transcript.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    storage = Storage()
    try:
        ref = storage.add_video(args.video)
        transcript = transcribe_video(storage, ref, args.model)
    except (TranscribeError, StorageError, VideoError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(storage.cache_path(ref.sha256, "transcript", transcript_key(args.model)))
    for s in transcript.segments:
        print(f"{s.start:8.2f} {s.end:8.2f}  {s.text}")
        print("    " + " ".join(f"{w.text}@{w.start:.2f}" for w in s.words))
    return 0


if __name__ == "__main__":
    sys.exit(main())
