"""Runtime settings, read from the environment."""

import os
from pathlib import Path
from typing import Literal

Backend = Literal["stub", "local", "remote"]

_BACKENDS: tuple[Backend, ...] = ("stub", "local", "remote")


def model_backend() -> Backend:
    """Which AI module backend to use (docs/ARCHITECTURE.md, "Compute").

    Defaults to ``stub`` so everything runs without a GPU. Set ``VIBE_BACKEND`` to change it.
    """
    value = os.environ.get("VIBE_BACKEND", "stub")
    if value not in _BACKENDS:
        raise ValueError(f"VIBE_BACKEND must be one of {_BACKENDS}, got {value!r}")
    return value


def transcribe_backend() -> Backend:
    """The backend for transcripts only (F-31), from ``VIBE_TRANSCRIBE_BACKEND``; unset means
    ``VIBE_BACKEND``. For a machine that sends the description judge to a remote GPU
    (``remote``) but runs Whisper itself (``local``)."""
    value = os.environ.get("VIBE_TRANSCRIBE_BACKEND", "").strip()
    if not value:
        return model_backend()
    if value not in _BACKENDS:
        raise ValueError(f"VIBE_TRANSCRIBE_BACKEND must be one of {_BACKENDS}, got {value!r}")
    return value


_REPO_ROOT = Path(__file__).resolve().parents[3]


def data_dir() -> Path:
    """Where projects, videos and the analysis cache live (docs/ARCHITECTURE.md, "Storage").

    Defaults to ``data/`` at the repo root (gitignored). Set ``VIBE_DATA_DIR`` to change it.
    """
    value = os.environ.get("VIBE_DATA_DIR")
    return Path(value) if value else _REPO_ROOT / "data"


def models_dir() -> Path:
    """Where downloaded model weights go (``python -m vibe.modules download``).

    Defaults to ``data/models/`` at the repo root (gitignored). Set ``VIBE_MODELS_DIR`` to change
    it, e.g. to a bigger disk.
    """
    value = os.environ.get("VIBE_MODELS_DIR")
    return Path(value) if value else data_dir() / "models"


def remote_url() -> str | None:
    """The model server the ``remote`` backend calls, e.g. ``http://alex-5070:8765``."""
    return os.environ.get("VIBE_REMOTE_URL") or None


def remote_hosts() -> frozenset[str]:
    """Hosts the ``remote`` backend may send footage to: team-controlled machines only (D-017).

    Comma-separated in ``VIBE_REMOTE_HOSTS``. Empty means none, so ``remote`` refuses to run.
    """
    value = os.environ.get("VIBE_REMOTE_HOSTS", "")
    return frozenset(h.strip().lower() for h in value.split(",") if h.strip())


def remote_token() -> str | None:
    """Shared secret between the ``remote`` backend and the model server (``.env``, never git)."""
    return os.environ.get("VIBE_REMOTE_TOKEN") or None


def detectors() -> list[str]:
    """Detectors the app runs on a video that has no analysis yet (F-10), from ``VIBE_DETECTORS``:
    one or more specs ``name[:option=value,...]`` separated by ``;``, e.g. ``tv-frcnn-mobile`` or
    ``rfdetr-s;yunet:weights=data/models/yunet.onnx``. ``python -m vibe.modules.vision
    detectors`` lists them.

    Empty (the default) means the app only replays the analysis cache. There is no default
    detector: which one is D-015, still Proposed, and not a choice to make silently."""
    value = os.environ.get("VIBE_DETECTORS", "")
    return [spec.strip() for spec in value.split(";") if spec.strip()]


def tracker() -> str:
    """The tracker for those analyses, from ``VIBE_TRACKER``: ``iou`` (the default) or
    ``bytetrack``, optionally with ``TrackerSettings`` as ``:name=value,...``, e.g.
    ``bytetrack:min_conf=0.85,iou_match=0.2,max_gap=3``."""
    return os.environ.get("VIBE_TRACKER", "").strip() or "iou"


def detect_every() -> int:
    """Detect on every how many frames when the app analyses a video (``VIBE_DETECT_EVERY``,
    default 1: every frame). Higher is faster and may miss short appearances."""
    value = os.environ.get("VIBE_DETECT_EVERY", "").strip() or "1"
    try:
        every = int(value)
    except ValueError:
        every = 0
    if every < 1:
        raise ValueError(f"VIBE_DETECT_EVERY must be a whole number of at least 1, got {value!r}")
    return every
