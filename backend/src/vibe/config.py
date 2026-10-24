"""Runtime settings, read from the environment."""

import os
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
