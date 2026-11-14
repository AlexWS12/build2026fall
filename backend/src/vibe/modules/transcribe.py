"""Transcription with word timestamps (F-31), cached per video through F-08's ``run_analysis``.

Times are source seconds (D-013): ``extract_audio`` puts sample 0 at the clip's time 0, so a
word's ``start``/``end`` can go straight into a ``TimeRange`` for ``mute`` or ``cut_range``.

Backends (``VIBE_BACKEND``, docs/ARCHITECTURE.md "Compute"), picked by ``run_analysis``:
- ``stub``: no model. ``transcribe_video`` replays a cached transcript (e.g. one copied from a
  teammate's ``cache/<sha256>/transcript/``) and raises ``NoAnalysis`` if there is none, never
  an empty one.
- ``local``: faster-whisper (MIT; tentative, no decision row yet). Not a project dependency:
  install it by hand with ``uv pip install faster-whisper``. The first run downloads the model.
- ``remote``: the model server runs :class:`TranscriptAnalysis` (it needs faster-whisper), and
  the result is cached here.

``transcribe`` takes samples, not a file, so a per-chunk caller (live) can reuse it later.

CLI: ``uv run python -m vibe.modules.transcribe CLIP [--model small]``.
"""

import argparse
import functools
import sys
from pathlib import Path

import numpy as np
from pydantic import ValidationError

from vibe.config import Backend, model_backend
from vibe.contract.common import Model
from vibe.modules.backend import Analysis, register, run_analysis
from vibe.modules.cache import ModuleError, read_json
from vibe.storage import Storage, StorageError, VideoRef
from vibe.video import VideoError, extract_audio
from vibe.video.audio import DEFAULT_SAMPLE_RATE

DEFAULT_MODEL = "small"
COMPUTE_TYPE = "int8"  # shortcut: CPU int8 only; add a device option when someone needs a GPU


class TranscribeError(ModuleError):
    """Whisper can't run here (package missing, not the local backend), or a cached transcript
    isn't in this version's format."""


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


@register
class TranscriptAnalysis(Analysis):
    """Whisper on the video's whole audio track. The model server rebuilds it from ``settings``,
    so they match ``__init__``'s arguments exactly."""

    module = "transcript"
    kind = "transcript"
    version = 1
    """Bump when the cached format or the conversion in ``transcribe`` changes."""

    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        super().__init__(model=model)
        self.model = model

    @property
    def name(self) -> str:
        return f"faster-whisper-{self.model}-{COMPUTE_TYPE}"

    def compute(self, video_path: Path) -> object:
        samples, rate = extract_audio(video_path)
        # "local" whatever VIBE_BACKEND says: this is the local run, here or on the model server.
        return transcribe(samples, rate, self.model, backend="local").model_dump(mode="json")


@functools.lru_cache(maxsize=2)
def _whisper(model: str):  # no return type: faster_whisper is optional
    """The loaded model, kept so repeat calls (and per-chunk live use) don't reload weights."""
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise TranscribeError(
            "faster-whisper isn't installed: run `uv pip install faster-whisper` in backend/"
        ) from e
    return WhisperModel(model, device="cpu", compute_type=COMPUTE_TYPE)


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
    segments, info = _whisper(model).transcribe(samples.astype(np.float32), word_timestamps=True)
    return Transcript(
        model=TranscriptAnalysis(model).key,
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
    """The cached transcript, or ``None`` if the video was never transcribed with ``model``.

    A cache file that's corrupt or in another format (e.g. hand-copied from an old version)
    raises ``ModuleError`` naming it, like every F-08 cache: delete it to redo. It's never
    replaced silently.
    """
    key = TranscriptAnalysis(model).key
    data = read_json(storage, video.sha256, TranscriptAnalysis.kind, key)
    if data is None:
        return None
    return _parse(data, storage.cache_path(video.sha256, TranscriptAnalysis.kind, key))


def transcribe_video(
    storage: Storage,
    video: VideoRef,
    model: str = DEFAULT_MODEL,
    backend: Backend | None = None,
) -> Transcript:
    """The video's transcript from the analysis cache, made by ``backend`` and cached if needed.

    Raises ``ModuleError`` when it can't (``NoAnalysis``: stub with nothing cached), and
    ``NoAudioStream`` when the video has no audio (``local``; under ``remote`` that's the model
    server's error, a ``RemoteError``).
    """
    result = run_analysis(storage, video, TranscriptAnalysis(model), backend)
    return _parse(result.data, result.path)


def _parse(data: object, path: Path) -> Transcript:
    try:
        return Transcript.model_validate(data)
    except ValidationError as e:
        raise TranscribeError(
            f"cached transcript {path} isn't in this version's format "
            f"({e.error_count()} problems); delete it to redo"
        ) from e


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
    except (ModuleError, StorageError, VideoError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    key = TranscriptAnalysis(args.model).key
    print(storage.cache_path(ref.sha256, TranscriptAnalysis.kind, key))
    for s in transcript.segments:
        print(f"{s.start:8.2f} {s.end:8.2f}  {s.text}")
        print("    " + " ".join(f"{w.text}@{w.start:.2f}" for w in s.words))
    return 0


if __name__ == "__main__":
    sys.exit(main())
