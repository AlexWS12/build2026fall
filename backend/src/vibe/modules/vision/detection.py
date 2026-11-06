"""What a detector returns, and the one interface every detector implements (F-10).

A detector looks at **one RGB frame** and returns boxes. It knows nothing about time, tracks or
the cache, so the same detector serves the offline path (``analyze_video``) and, later, the
per-frame live path (docs/ARCHITECTURE.md, "AI modules").
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

import numpy as np

Box = tuple[float, float, float, float]
"""[x1, y1, x2, y2], normalized to 0..1 of the frame, like contract tracks."""

_DIGITS = 6
"""Boxes are rounded to 1e-6 of the frame (well under a pixel at 8K) to keep the cache small."""


@dataclass(frozen=True)
class Detection:
    label: str
    """Contract label: ``person``, ``face``, ``license_plate``, or the model's class name."""
    box: Box
    conf: float


class Detector(Protocol):
    name: str
    """Model, size and weights, e.g. ``rfdetr-l`` or ``yolo11s``. Goes into the cache key, so it
    must change whenever the detector's output would."""
    labels: frozenset[str] | None
    """The labels it can return; ``None`` for an open set (text prompts)."""
    settings: Mapping[str, object]
    """Everything else that changes its output (threshold, prompts...), as JSON values. Hashed
    into the cache key with ``name``."""

    def detect(self, image: np.ndarray) -> list[Detection]:
        """Boxes in one RGB ``uint8`` frame shaped (height, width, 3)."""
        ...


class NotInstalled(RuntimeError):
    """A detector's or tracker's package or weights aren't here. The message says how to get
    them. Model packages are installed by hand, not from ``pyproject.toml`` (see the package
    docstring), so CI and CPU laptops never need them."""


def normalize_box(
    x1: float, y1: float, x2: float, y2: float, width: int, height: int
) -> Box | None:
    """A pixel box as a normalized box, clipped to the frame. ``None`` if nothing is left of it.

    Detectors return boxes that poke past the frame edge or have no area; the contract rejects
    both, so they're fixed here, once, for every detector.
    """
    nx1 = round(min(max(x1 / width, 0.0), 1.0), _DIGITS)
    ny1 = round(min(max(y1 / height, 0.0), 1.0), _DIGITS)
    nx2 = round(min(max(x2 / width, 0.0), 1.0), _DIGITS)
    ny2 = round(min(max(y2 / height, 0.0), 1.0), _DIGITS)
    if nx2 <= nx1 or ny2 <= ny1:
        return None
    return (nx1, ny1, nx2, ny2)


def iou(a: Box, b: Box) -> float:
    """Intersection over union of two boxes: 0 when apart, 1 when identical."""
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0
