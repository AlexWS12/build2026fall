"""Hide a target's shape instead of its box, when a segmentation mask is there for it (F-15).

The masks come from the analysis cache, already read (``vision.masks.MaskSet``), so the
per-frame path still touches no files. Each mask belongs to one keyframe and its box. Between
two keyframes, both neighbours' masks are moved and scaled from their own box onto the
interpolated box and joined, so the shape covers wherever either says the target is. The result
is grown by a margin and the effect applied only inside it.

**It fails closed.** ``place_shape`` returns ``None``, and the caller hides the padded box as
before, whenever: the box isn't straight from keyframes (held, lost, a long gap, next to an
``uncertain`` keyframe; see ``PlacedBox.anchors``), a keyframe it needs has no mask, or a mask
looks wrong for its box (too little of the box covered, or its outline far from the box). A
mask that's slightly off leaks; a box doesn't (D-018).
"""

import math
from typing import Protocol

import numpy as np

from vibe.render.geometry import Box, PlacedBox, Rect

MIN_FILL = 0.15
"""Least share of its box a mask must cover. Less means the segmenter likely missed the target."""

MIN_OUTLINE_IOU = 0.5
"""Least overlap (IoU) between a mask's bounding box and its keyframe's box. Less means it
likely segmented something else in the box (another person, the background)."""

MIN_MARGIN_PX = 2
"""The mask always grows by at least this many pixels."""


class MaskLookup(Protocol):
    def mask(self, track_id: str, t: float, box: Box) -> np.ndarray | None:
        """The full-frame boolean mask made for this track's keyframe at ``t`` with ``box``, or
        ``None`` if there is none."""
        ...


Shape = tuple[Rect, np.ndarray]
"""A window of the frame in pixels and the boolean mask inside it (shaped like the window)."""


def place_shape(
    masks: MaskLookup,
    track_id: str,
    placed: PlacedBox,
    padding: float,
    width: int,
    height: int,
) -> Shape | None:
    """The target's shape at this frame, grown by ``padding / 2`` × the box's shorter side (the
    same margin the box gets on each side), or ``None`` to use the padded box."""
    if not placed.anchors:
        return None
    parts = []
    for keyframe in placed.anchors:
        mask = masks.mask(track_id, keyframe.t, keyframe.box)
        if mask is None or mask.ndim != 2:
            return None
        mask = _resized(mask, width, height)
        if not _plausible(mask, keyframe.box):
            return None
        part = _moved(mask, keyframe.box, placed.box)
        if part is None:
            return None
        parts.append(part)
    rect, joined = _join(parts)
    bw = (placed.box[2] - placed.box[0]) * width
    bh = (placed.box[3] - placed.box[1]) * height
    margin = max(MIN_MARGIN_PX, math.ceil(padding / 2 * min(bw, bh)))
    return _grow(rect, joined, margin, width, height)


def _resized(mask: np.ndarray, width: int, height: int) -> np.ndarray:
    """The mask at the frame's size (nearest pixel), e.g. made on the source, shown on a preview."""
    mh, mw = mask.shape
    if (mh, mw) == (height, width):
        return mask
    rows = np.minimum((np.arange(height) + 0.5) * mh / height, mh - 1).astype(int)
    cols = np.minimum((np.arange(width) + 0.5) * mw / width, mw - 1).astype(int)
    return mask[np.ix_(rows, cols)]


def _bounds(mask: np.ndarray) -> Rect | None:
    rows = np.flatnonzero(mask.any(axis=1))
    cols = np.flatnonzero(mask.any(axis=0))
    if not len(rows):
        return None
    return (int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1)


def _plausible(mask: np.ndarray, box: Box) -> bool:
    height, width = mask.shape
    outline = _bounds(mask)
    if outline is None:
        return False
    x1, y1 = math.floor(box[0] * width), math.floor(box[1] * height)
    x2, y2 = math.ceil(box[2] * width), math.ceil(box[3] * height)
    box_area = max(1, (x2 - x1) * (y2 - y1))
    if mask[y1:y2, x1:x2].sum() < MIN_FILL * box_area:
        return False
    return _iou(outline, (x1, y1, x2, y2)) >= MIN_OUTLINE_IOU


def _iou(a: Rect, b: Rect) -> float:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _moved(mask: np.ndarray, source: Box, target: Box) -> Shape | None:
    """``mask``, made for ``source``, moved and scaled so ``source`` lands on ``target``.

    Each output pixel takes the nearest source pixel. The window is rounded outward."""
    height, width = mask.shape
    outline = _bounds(mask)
    if outline is None:
        return None
    sx = (target[2] - target[0]) / (source[2] - source[0])
    sy = (target[3] - target[1]) / (source[3] - source[1])

    def to_x(px: float) -> float:  # source pixel x -> target pixel x
        return (target[0] + (px / width - source[0]) * sx) * width

    def to_y(py: float) -> float:
        return (target[1] + (py / height - source[1]) * sy) * height

    x1 = max(0, math.floor(to_x(outline[0])))
    y1 = max(0, math.floor(to_y(outline[1])))
    x2 = min(width, math.ceil(to_x(outline[2])))
    y2 = min(height, math.ceil(to_y(outline[3])))
    if x2 <= x1 or y2 <= y1:
        return None
    # Back from each window pixel's centre to the source pixel it comes from.
    cols = ((np.arange(x1, x2) + 0.5) / width - target[0]) / sx + source[0]
    rows = ((np.arange(y1, y2) + 0.5) / height - target[1]) / sy + source[1]
    cols = np.floor(cols * width).astype(int)
    rows = np.floor(rows * height).astype(int)
    inside_c = (cols >= 0) & (cols < width)
    inside_r = (rows >= 0) & (rows < height)
    window = mask[np.ix_(np.clip(rows, 0, height - 1), np.clip(cols, 0, width - 1))]
    window &= inside_r[:, None] & inside_c[None, :]
    return (x1, y1, x2, y2), window


def _join(parts: list[Shape]) -> Shape:
    if len(parts) == 1:
        return parts[0]
    x1 = min(r[0] for r, _ in parts)
    y1 = min(r[1] for r, _ in parts)
    x2 = max(r[2] for r, _ in parts)
    y2 = max(r[3] for r, _ in parts)
    out = np.zeros((y2 - y1, x2 - x1), dtype=bool)
    for (a, b, c, d), m in parts:
        out[b - y1 : d - y1, a - x1 : c - x1] |= m
    return (x1, y1, x2, y2), out


def _grow(rect: Rect, mask: np.ndarray, margin: int, width: int, height: int) -> Shape:
    """Grow the mask by ``margin`` pixels in every direction (a square), clamped to the frame."""
    x1, y1, x2, y2 = rect
    nx1, ny1 = max(0, x1 - margin), max(0, y1 - margin)
    nx2, ny2 = min(width, x2 + margin), min(height, y2 + margin)
    canvas = np.zeros((ny2 - ny1, nx2 - nx1), dtype=bool)
    canvas[y1 - ny1 : y2 - ny1, x1 - nx1 : x2 - nx1] = mask
    grown = _spread(_spread(canvas, margin, 0), margin, 1)
    return (nx1, ny1, nx2, ny2), grown


def _spread(a: np.ndarray, r: int, axis: int) -> np.ndarray:
    """Set every pixel within ``r`` of a set pixel along ``axis`` (a running sum)."""
    a = np.moveaxis(a, axis, 0)
    pad = [(r + 1, r)] + [(0, 0)] * (a.ndim - 1)
    sums = np.cumsum(np.pad(a.astype(np.int32), pad), axis=0)
    out = (sums[2 * r + 1 :] - sums[: -(2 * r + 1)]) > 0
    return np.moveaxis(out, 0, axis)
