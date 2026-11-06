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
