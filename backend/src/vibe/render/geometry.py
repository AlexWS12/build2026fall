"""Where a track's box is at a given time, and that box in pixels.

Tracks give normalized ``[x1, y1, x2, y2]`` boxes at keyframes (``contract/tracks.py``). Between
two keyframes the box moves linearly. Past either end of a track, the nearest box is held and
grows, up to a limit: a detector losing a target doesn't mean the target left, so a mask in
effect never just stops (docs/ARCHITECTURE.md, "Renderer"). A box held for more than
``HOLD_SECONDS`` is marked ``lost``, because the target may have moved out of it.

Across a gap longer than ``MAX_GAP_SECONDS`` nothing saw the target, so a straight line between
the two keyframes would be a guess. The box covers both keyframes and all the space between
them instead.

The constants are starting points; F-11 tunes them on real detections.
"""

import math
from bisect import bisect_right
from dataclasses import dataclass

from vibe.contract import Track

HOLD_SECONDS = 0.5
"""How long a box can be held from a keyframe before the target counts as lost."""

HOLD_GROWTH = 0.5
"""How fast a held box grows, as a fraction of its size per second held (0.5 s → +25%)."""

MAX_HOLD_GROWTH = 0.5
"""The most a held box grows, so a target lost for a long time doesn't hide the whole frame."""

MAX_GAP_SECONDS = 1.0
"""The longest gap between two keyframes that a box still moves across in a straight line."""

UNCERTAIN_PADDING = 0.25
"""Padding added on top of the effect's own, next to an ``uncertain`` keyframe."""

Box = tuple[float, float, float, float]
Rect = tuple[int, int, int, int]
"""Pixels ``(x1, y1, x2, y2)``, with ``x2`` and ``y2`` excluded, like a numpy slice."""


@dataclass(frozen=True)
class PlacedBox:
    """A track's box at one moment, plus the padding the renderer adds on top of the effect's
    (for a held or uncertain box)."""

    box: Box
    extra_padding: float
    lost: bool = False
    """No detection near this moment, so the target may not be inside the box."""


class TrackPath:
    """One track's box over time, ready to look up frame after frame."""

    def __init__(self, track: Track) -> None:
        self.track = track
        self._times = [k.t for k in track.keyframes]

    def at(self, t: float) -> PlacedBox:
        """The box at source time ``t``. There's always one: a mask in effect never stops."""
        keyframes = self.track.keyframes
        first, last = keyframes[0], keyframes[-1]
        if t < first.t:
            return _held(first.box, first.t - t, first.state == "uncertain")
        if t >= last.t:
            return _held(last.box, t - last.t, last.state == "uncertain")
        i = bisect_right(self._times, t) - 1  # keyframes[i].t <= t < keyframes[i + 1].t
        a, b = keyframes[i], keyframes[i + 1]
        uncertain = "uncertain" in (a.state, b.state)
        if b.t - a.t > MAX_GAP_SECONDS:
            return _held(_hull(a.box, b.box), min(t - a.t, b.t - t), uncertain)
        w = (t - a.t) / (b.t - a.t)
        ax1, ay1, ax2, ay2 = a.box
        bx1, by1, bx2, by2 = b.box
        box = (
            ax1 + (bx1 - ax1) * w,
            ay1 + (by1 - ay1) * w,
            ax2 + (bx2 - ax2) * w,
            ay2 + (by2 - ay2) * w,
        )
        return PlacedBox(box, UNCERTAIN_PADDING if uncertain else 0.0)


def _held(box: Box, held: float, uncertain: bool) -> PlacedBox:
    growth = min(HOLD_GROWTH * held, MAX_HOLD_GROWTH)
    padding = growth + (UNCERTAIN_PADDING if uncertain else 0.0)
    return PlacedBox(box, padding, lost=held > HOLD_SECONDS)


def _hull(a: Box, b: Box) -> Box:
    """The smallest box that contains both."""
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def to_pixels(box: Box, padding: float, width: int, height: int) -> Rect:
    """``box`` grown by ``padding`` × its size (half on each side), in pixels of a
    ``width`` × ``height`` frame, clamped to the frame.

    Rounded outward, so the pixels always cover the whole box: a mask never shrinks.
    """
    x1, y1, x2, y2 = box
    grow_x = (x2 - x1) * padding / 2
    grow_y = (y2 - y1) * padding / 2
    return (
        max(0, math.floor((x1 - grow_x) * width)),
        max(0, math.floor((y1 - grow_y) * height)),
        min(width, math.ceil((x2 + grow_x) * width)),
        min(height, math.ceil((y2 + grow_y) * height)),
    )
