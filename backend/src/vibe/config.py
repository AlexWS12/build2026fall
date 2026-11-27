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


DEFAULT_VISION_DETECTORS = ("tv-frcnn-mobile", "yunet:weights=@yunet,threshold=0.6,grow=2.2")
"""People (torchvision's Faster R-CNN MobileNet, BSD-3) and faces (YuNet, MIT), with head boxes for
faces YuNet misses (D-022, Proposed). YuNet's threshold is 0.6 and its boxes are grown 2.2x to the
whole face: on the three face-labelled clips that left 0 face boxes exposed and masked the least
of the settings that did (17-42% of the frame vs 20-58% at threshold 0.3 grow 2.6; 1.8x leaked one
face). Needs PyTorch (`uv sync --extra cpu` or `cu128`) and OpenCV (`--extra vision`); set
VIBE_VISION_DETECTORS="yunet:weights=@yunet,grow=2.6" to run faces only, without PyTorch (it then
misses faces hidden by hair: 8/23 exposed on one clip)."""

DEFAULT_VISION_EVERY = 3
"""Detect on every 3rd frame (the tracker carries boxes between): still 0 faces exposed on the
labelled clips, at a third of the time (D-022)."""


def vision_detectors() -> list[str]:
    """Detector specs the app's Analyse button runs (``VIBE_VISION_DETECTORS``, ``;``-separated).

    E.g. ``rfdetr-s;yunet:weights=@yunet``. See ``python -m vibe.modules.vision detectors``.
    """
    value = os.environ.get("VIBE_VISION_DETECTORS", "")
    specs = [s.strip() for s in value.split(";") if s.strip()]
    return specs or list(DEFAULT_VISION_DETECTORS)


def vision_every() -> int:
    """Detect on every Nth frame in the app's analysis (``VIBE_VISION_EVERY``, default 3)."""
    value = os.environ.get("VIBE_VISION_EVERY", "").strip()
    if not value:
        return DEFAULT_VISION_EVERY
    try:
        every = int(value)
    except ValueError:
        raise ValueError(f"VIBE_VISION_EVERY must be a whole number, got {value!r}") from None
    if every < 1:
        raise ValueError(f"VIBE_VISION_EVERY must be at least 1, got {every}")
    return every


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


def planner_llm() -> str | None:
    """Which LLM plans requests no template reads (``VIBE_PLANNER_LLM``): ``claude``, or unset
    for templates only (the default). See ``vibe.agent.llm``."""
    value = os.environ.get("VIBE_PLANNER_LLM", "").strip().lower()
    if value in ("", "none", "off"):
        return None
    if value != "claude":
        raise ValueError(f"VIBE_PLANNER_LLM must be 'claude' or unset, got {value!r}")
    return value


def vlm_url() -> str:
    """The server the F-13 description judge asks (``VIBE_VLM_URL``): anything that speaks the
    OpenAI chat API with images, such as Ollama (the default, port 11434), llama.cpp or vLLM."""
    return os.environ.get("VIBE_VLM_URL") or "http://localhost:11434"


def vlm_model() -> str | None:
    """The model the description judge asks for (``VIBE_VLM_MODEL``, e.g. ``gemma3:12b``).

    No default: which VLM to use is D-020, still Proposed, and not a choice to make silently."""
    return os.environ.get("VIBE_VLM_MODEL") or None
