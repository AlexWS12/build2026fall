"""The F-15 masks as small images for the editor's preview.

The export applies masks exactly (``render.shape``). The preview only needs to look right: for
each masked track, every cached keyframe mask is cropped to the same padded box the preview
draws (``frontend/src/components/preview.ts``, ``padded``), shrunk, grown a little and sent as a
PNG whose alpha is the shape. The browser then clips its blur to it (CSS ``mask-image``).

Only masks made for the track's current keyframes count (same time and box): masks cached for
an older analysis of the video are ignored, so the preview never shows a stale shape.
"""

import base64
import struct
import zlib
from collections.abc import Iterable, Sequence

import numpy as np
from pydantic import BaseModel, ConfigDict, ValidationError

from vibe.contract import TimelineState, Track
from vibe.modules.vision import rle
from vibe.modules.vision.masks import KIND, MasksFile
from vibe.storage import Storage

MAX_SIDE = 96
"""Longer side of each preview image, in pixels: enough for a smooth outline under a blur."""
GROW = 2
"""Pixels the shape grows by after shrinking, so the outline doesn't cut into the person."""


class ShapeFrame(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    t: float
    image: str
    """``data:image/png;base64,...``: white, with the shape as alpha, over the padded box."""


def cached_files(storage: Storage, sha256: str) -> list[MasksFile]:
    """Every masks file cached for a video (any segmenter); unreadable ones are skipped."""
    folder = storage.cache_dir / sha256 / KIND
    files = []
    for path in sorted(folder.glob("*.json")) if folder.is_dir() else []:
        try:
            files.append(MasksFile.model_validate_json(path.read_bytes()))
        except (OSError, ValidationError, ValueError):
            continue
    return files


def preview_shapes(
    state: TimelineState, tracks: Sequence[Track], files: Iterable[MasksFile]
) -> dict[str, list[ShapeFrame]]:
    """The shapes of the masked tracks, by track id, in time order."""
    by_id = {t.track_id: t for t in tracks}
    shapes: dict[str, dict[float, ShapeFrame]] = {}
    for f in files:
        track, spans = by_id.get(f.track_id), state.masks.get(f.track_id)
        if track is None or not spans:
            continue
        current = {(k.t, tuple(k.box)) for k in track.keyframes}
        padding = spans[0].effect.padding
        frames = shapes.setdefault(f.track_id, {})
        for entry in f.entries:
            if entry.mask is None or (entry.t, tuple(entry.box)) not in current:
                continue
            mask = rle.decode({"size": list(entry.mask.size), "counts": entry.mask.counts})
            image = _shape_image(mask, entry.box, padding)
            if image is not None:
                frames[entry.t] = ShapeFrame(t=entry.t, image=image)
    return {tid: [frames[t] for t in sorted(frames)] for tid, frames in shapes.items() if frames}


def _padded(box: Sequence[float], padding: float) -> tuple[float, float, float, float]:
    """The preview's padded box (``preview.ts``): each side moves out by ``padding`` times the
    box's size along that axis."""
    x1, y1, x2, y2 = box
    px, py = (x2 - x1) * padding, (y2 - y1) * padding
    return (max(0.0, x1 - px), max(0.0, y1 - py), min(1.0, x2 + px), min(1.0, y2 + py))


def _shape_image(mask: np.ndarray, box: Sequence[float], padding: float) -> str | None:
    height, width = mask.shape[:2]
    x1, y1, x2, y2 = _padded(box, padding)
    left, top = int(x1 * width), int(y1 * height)
    right, bottom = max(left + 1, round(x2 * width)), max(top + 1, round(y2 * height))
    crop = mask[top:bottom, left:right]
    if crop.size == 0 or not crop.any():
        return None
    small = _shrink(crop.astype(np.uint8), MAX_SIDE)
    for _ in range(GROW):
        small = _grow(small)
    return "data:image/png;base64," + base64.b64encode(_png_alpha(small * 255)).decode()


def _shrink(mask: np.ndarray, max_side: int) -> np.ndarray:
    """At most ``max_side`` on the longer side. A cell is in the shape if any pixel in it is
    (shrinking never drops a thin part)."""
    h, w = mask.shape
    k = max(1, int(np.ceil(max(h, w) / max_side)))
    if k == 1:
        return mask
    ph, pw = -h % k, -w % k
    padded = np.pad(mask, ((0, ph), (0, pw)))
    return padded.reshape((h + ph) // k, k, (w + pw) // k, k).max(axis=(1, 3))


def _grow(mask: np.ndarray) -> np.ndarray:
    """One pixel of dilation (3x3)."""
    out = mask.copy()
    out[1:, :] |= mask[:-1, :]
    out[:-1, :] |= mask[1:, :]
    out[:, 1:] |= mask[:, :-1]
    out[:, :-1] |= mask[:, 1:]
    return out


def _png_alpha(alpha: np.ndarray) -> bytes:
    """A PNG of white pixels whose alpha is ``alpha`` (h, w uint8), standard library only."""
    h, w = alpha.shape
    rgba = np.full((h, w, 4), 255, dtype=np.uint8)
    rgba[..., 3] = alpha
    rows = np.zeros((h, 1 + w * 4), dtype=np.uint8)
    rows[:, 1:] = rgba.reshape(h, w * 4)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    header = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)  # 8-bit RGBA
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows.tobytes(), 9))
        + chunk(b"IEND", b"")
    )
