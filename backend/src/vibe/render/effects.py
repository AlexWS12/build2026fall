"""The three ways to hide a target: blur, pixelate and a solid black box. Pure numpy.

Sizes scale with the target's shorter side, so a face far away and one close up are hidden
alike. Every effect has a floor: even strength 0 has to hide what's under it.
"""

import math

import numpy as np

from vibe.contract import Effect
from vibe.render.errors import RenderError
from vibe.render.geometry import Rect

DEFAULT_STRENGTH = 0.5
"""What ``strength: null`` means."""

BLUR_RADIUS = (0.2, 0.5)
"""Blur radius as a fraction of the target's shorter side, at strength 0 and at 1."""

BLUR_PASSES = 3
"""Box blurs in a row. Three look close to a Gaussian blur."""

PIXELATE_BLOCKS = (8, 3)
"""Blocks across the target's shorter side, at strength 0 and at 1 (fewer is coarser)."""


def apply_effect(image: np.ndarray, rect: Rect, effect: Effect) -> None:
    """Hide ``rect`` of ``image`` (height, width, channels) in place."""
    x1, y1, x2, y2 = rect
    if x2 <= x1 or y2 <= y1:
        return
    region = image[y1:y2, x1:x2]
    strength = DEFAULT_STRENGTH if effect.strength is None else effect.strength
    match effect.type:
        case "box":
            region[...] = 0
        case "pixelate":
            region[...] = _pixelate(region, strength)
        case "blur":
            region[...] = _blur(region, strength)
        case _:
            raise RenderError(f"unknown effect {effect.type!r}")


def _lerp(ends: tuple[float, float], strength: float) -> float:
    return ends[0] + (ends[1] - ends[0]) * strength


def _pixelate(region: np.ndarray, strength: float) -> np.ndarray:
    """Each block becomes its average color."""
    h, w = region.shape[:2]
    size = max(2, math.ceil(min(h, w) / _lerp(PIXELATE_BLOCKS, strength)))
    # Pad to whole blocks with the edge pixels, average each block, then crop back.
    padded = np.pad(region, ((0, -h % size), (0, -w % size), (0, 0)), mode="edge")
    ph, pw = padded.shape[:2]
    blocks = padded.reshape(ph // size, size, pw // size, size, -1).mean(axis=(1, 3))
    out = np.repeat(np.repeat(blocks, size, axis=0), size, axis=1)[:h, :w]
    return np.rint(out).astype(region.dtype)


def _blur(region: np.ndarray, strength: float) -> np.ndarray:
    """A separable box blur, run ``BLUR_PASSES`` times. Uses only the pixels inside the region,
    extended at its edges, so nothing outside the target leaks in or changes."""
    h, w = region.shape[:2]
    radius = max(2, round(min(h, w) * _lerp(BLUR_RADIUS, strength)))
    out = region.astype(np.float64)
    for _ in range(BLUR_PASSES):
        out = _box_blur_rows(out, radius)
        out = _box_blur_rows(out.transpose(1, 0, 2), radius).transpose(1, 0, 2)
    return np.clip(np.rint(out), 0, 255).astype(region.dtype)


def _box_blur_rows(a: np.ndarray, radius: int) -> np.ndarray:
    """Average each pixel with ``radius`` pixels above and below it (a running sum)."""
    padded = np.pad(a, ((radius + 1, radius), (0, 0), (0, 0)), mode="edge")
    sums = np.cumsum(padded, axis=0)
    width = 2 * radius + 1
    return (sums[width:] - sums[:-width]) / width
