"""Stub detector: finds a block of one color. No model, no GPU (F-10).

Used by the tests, which draw colored squares into generated clips, and to try the whole
pipeline (decode, detect, track, cache, benchmark) on a CPU laptop.
"""

import numpy as np

from vibe.modules.vision.detection import Detection, normalize_box

COLORS = {
    "red": (255, 0, 0),
    "green": (0, 255, 0),
    "blue": (0, 0, 255),
    "white": (255, 255, 255),
}


class ColorDetector:
    """One box around every pixel within ``tolerance`` of ``color`` (one object per color)."""

    def __init__(
        self, label: str = "person", color: str = "red", tolerance: int = 60, min_pixels: int = 4
    ) -> None:
        if color not in COLORS:
            raise ValueError(f"color must be one of {sorted(COLORS)}, got {color!r}")
        self.name = f"stub-{color}"
        self.labels = frozenset({label})
        self.settings = {
            "label": label,
            "color": color,
            "tolerance": tolerance,
            "min_pixels": min_pixels,
        }
        self._label = label
        self._rgb = np.array(COLORS[color], dtype=np.int16)
        self._tolerance = tolerance
        self._min_pixels = min_pixels

    def detect(self, image: np.ndarray) -> list[Detection]:
        close = np.all(np.abs(image.astype(np.int16) - self._rgb) <= self._tolerance, axis=2)
        if int(close.sum()) < self._min_pixels:
            return []
        ys, xs = np.nonzero(close)
        height, width = image.shape[:2]
        box = normalize_box(xs.min(), ys.min(), xs.max() + 1, ys.max() + 1, width, height)
        return [] if box is None else [Detection(self._label, box, 1.0)]
