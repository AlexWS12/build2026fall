"""Stub segmenter: the ellipse inscribed in each box. No model, no GPU (F-15).

Lets the tests and CPU laptops run the whole mask path (cache, renderer) with a shape that is
clearly not the box: the box's corners stay outside it.
"""

from collections.abc import Sequence

import numpy as np

from vibe.modules.vision.detection import Box


class EllipseSegmenter:
    def __init__(self, fail_every: int = 0) -> None:
        """``fail_every=n`` returns no mask for every n-th box, to exercise the fallback."""
        self.name = "stub-ellipse"
        self.settings = {"fail_every": fail_every}
        self._fail_every = fail_every
        self._seen = 0

    def segment(self, image: np.ndarray, boxes: Sequence[Box]) -> list[np.ndarray | None]:
        height, width = image.shape[:2]
        ys, xs = np.mgrid[0:height, 0:width]
        cx, cy = (xs + 0.5) / width, (ys + 0.5) / height
        masks: list[np.ndarray | None] = []
        for x1, y1, x2, y2 in boxes:
            self._seen += 1
            if self._fail_every and self._seen % self._fail_every == 0:
                masks.append(None)
                continue
            rx, ry = (x2 - x1) / 2, (y2 - y1) / 2
            inside = ((cx - (x1 + rx)) / rx) ** 2 + ((cy - (y1 + ry)) / ry) ** 2 <= 1
            masks.append(inside if inside.any() else None)
        return masks
