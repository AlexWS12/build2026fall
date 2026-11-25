"""The per-frame path: one frame and its source time in, that frame with its masks applied out.

Preview, export and live mode all go through here, so they look the same. It's pure: no files,
no cache and no network (live mode can't wait on any of them), and the same inputs always give
the same frame.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass, replace

import numpy as np

from vibe.contract import Effect, TimelineState, Track
from vibe.render.effects import apply_effect
from vibe.render.errors import UnsupportedEdit
from vibe.render.geometry import PlacedBox, Rect, TrackPath, to_pixels
from vibe.render.shape import MaskLookup, place_shape

log = logging.getLogger(__name__)


@dataclass
class ShapeUse:
    """How often a track was hidden by its shape (F-15) or by its padded box, in frames."""

    shape: int = 0
    box: int = 0


@dataclass(frozen=True)
class RenderIssue:
    """Something the renderer had to work around, always by masking more (fail closed).

    One issue covers one unbroken stretch of frames, so a track lost twice gives two.
    """

    track_id: str
    message: str
    first_seen: float
    """Source time of the first frame it happened on."""
    last_seen: float
    """Source time of the last frame it happened on."""


def check_video(state: TimelineState) -> None:
    """Raise ``UnsupportedEdit`` for visual edits this renderer can't apply yet."""
    if state.rules:
        ids = ", ".join(r.rule_id for r in state.rules)
        raise UnsupportedEdit(
            f"the timeline has standing rules ({ids}); rendering them needs per-frame "
            "detections and comes with F-51"
        )


class FrameRenderer:
    """Renders frames for one timeline state and one set of tracks. Build it once, then call
    ``render`` for every frame.

    Tracks that share an id (say, a detector's track and its correction by hand) are all
    masked: masking too much beats leaving a target visible.

    ``masks`` (F-15) gives segmentation masks per keyframe. Where one fits, only the target's
    shape, grown by a margin, is hidden; everywhere else, or on any doubt, the padded box is, as
    without masks (``render.shape``).
    """

    def __init__(
        self, state: TimelineState, tracks: Iterable[Track], masks: MaskLookup | None = None
    ) -> None:
        check_video(state)
        self.state = state
        self._shapes = masks
        self._shape_use: dict[str, ShapeUse] = {}
        self._paths: dict[str, list[TrackPath]] = {}
        for track in tracks:
            self._paths.setdefault(track.track_id, []).append(TrackPath(track))
        self._issues: list[RenderIssue] = []
        self._ongoing: dict[str, int] = {}  # track id -> its issue, if the last frame had one

    @property
    def shape_use(self) -> dict[str, ShapeUse]:
        """Per masked track, frames hidden by shape vs by box. Empty without ``masks``."""
        return dict(self._shape_use)

    @property
    def issues(self) -> list[RenderIssue]:
        """Each stretch of a problem once, in the order first seen."""
        return list(self._issues)

    def render(self, image: np.ndarray, t: float, until: float | None = None) -> np.ndarray:
        """``image`` (height, width, channels), shown from source time ``t``, with its masks
        applied. Returns a new array; ``image`` is left as it was.

        ``until`` is when the next frame replaces this one, if known. A mask starting while this
        frame is on screen then covers it too, so no mask starts late, and none is too short to
        reach a frame. Without it (live doesn't know yet), only masks in effect at ``t`` apply.

        Cuts aren't applied here: whether a cut frame is shown at all is the caller's call.
        """
        if image.ndim != 3:
            raise ValueError(f"image must be (height, width, channels), got {image.shape}")
        out = image.copy()
        height, width = out.shape[:2]
        previous, self._ongoing = self._ongoing, {}
        for track_id, effect in self._masks(t, until):
            paths = self._paths.get(track_id)
            if not paths:
                # A target with no geometry can be anywhere: hide the whole frame.
                message = "no track with this id, so the whole frame is masked"
                self._report(track_id, message, t, previous)
                apply_effect(out, (0, 0, width, height), effect)
                continue
            placed = [path.at(t) for path in paths]
            if all(box.lost for box in placed):
                self._report(track_id, "lost, so its nearest known box stays masked", t, previous)
            for box in placed:
                padding = effect.padding + box.extra_padding
                rect = to_pixels(box.box, padding, width, height)
                if self._shapes is None or not self._hide_shape(out, track_id, box, rect, effect):
                    apply_effect(out, rect, effect)
        return out

    def _hide_shape(
        self, out: np.ndarray, track_id: str, box: PlacedBox, rect: Rect, effect: Effect
    ) -> bool:
        """Hide the target's shape instead of its box. False (and nothing changed) when there's
        no usable mask, so the caller hides the box: privacy fails closed."""
        assert self._shapes is not None
        height, width = out.shape[:2]
        use = self._shape_use.setdefault(track_id, ShapeUse())
        try:
            shape = place_shape(self._shapes, track_id, box, effect.padding, width, height)
        except Exception:  # a broken mask must never leave the target visible
            log.exception("render: %s at a mask: using its box", track_id)
            shape = None
        if shape is None:
            use.box += 1
            return False
        (sx1, sy1, sx2, sy2), mask = shape
        # The effect is worked out over the box and the shape together, so a blur looks the same
        # size as without masks, then applied only inside the shape.
        x1, y1 = min(rect[0], sx1), min(rect[1], sy1)
        x2, y2 = max(rect[2], sx2), max(rect[3], sy2)
        where = np.zeros((y2 - y1, x2 - x1), dtype=bool)
        where[sy1 - y1 : sy2 - y1, sx1 - x1 : sx2 - x1] = mask
        apply_effect(out, (x1, y1, x2, y2), effect, where)
        use.shape += 1
        return True

    def _masks(self, t: float, until: float | None) -> list[tuple[str, Effect]]:
        """Each track to mask, with its effect. Two spans touching one frame give two entries."""
        found = []
        for track_id in sorted(self.state.masks):
            for span in self.state.masks[track_id]:
                if span.contains(t) or (until is not None and t < span.start < until):
                    found.append((track_id, span.effect))
        return found

    def _report(self, track_id: str, message: str, t: float, previous: dict[str, int]) -> None:
        """Extend the track's issue if it was ongoing on the last frame, or start a new one."""
        position = self._ongoing.get(track_id, previous.get(track_id))
        if position is None:
            position = len(self._issues)
            self._issues.append(RenderIssue(track_id, message, t, t))
            log.warning("render: %s at %.3f s: %s", track_id, t, message)
        else:
            self._issues[position] = replace(self._issues[position], last_seen=t)
        self._ongoing[track_id] = position


def render_frame(
    image: np.ndarray,
    t: float,
    state: TimelineState,
    tracks: Iterable[Track],
    until: float | None = None,
    masks: MaskLookup | None = None,
) -> np.ndarray:
    """One frame, without keeping a renderer around: see ``FrameRenderer.render``."""
    return FrameRenderer(state, tracks, masks).render(image, t, until)
