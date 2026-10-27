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
