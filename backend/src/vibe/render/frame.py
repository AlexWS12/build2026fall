"""The per-frame path: one frame and its source time in, that frame with its masks applied out.

Preview, export and live mode all go through here, so they look the same. It's pure: no files,
no cache and no network (live mode can't wait on any of them), and the same inputs always give
the same frame.
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np

from vibe.contract import Effect, TimelineState, Track
from vibe.render.effects import apply_effect
from vibe.render.errors import UnsupportedEdit
from vibe.render.geometry import TrackPath, to_pixels

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RenderIssue:
    """Something the renderer had to work around, always by masking more (fail closed)."""

    track_id: str
    message: str
    first_seen: float
    """Source time of the first frame it happened on."""


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
    """

    def __init__(self, state: TimelineState, tracks: Iterable[Track]) -> None:
        check_video(state)
        self.state = state
        self._paths: dict[str, list[TrackPath]] = {}
        for track in tracks:
            self._paths.setdefault(track.track_id, []).append(TrackPath(track))
        self._issues: dict[str, RenderIssue] = {}

    @property
    def issues(self) -> list[RenderIssue]:
        """Each problem once, in the order first seen."""
        return list(self._issues.values())

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
        for track_id, effect in self._masks(t, until):
            paths = self._paths.get(track_id)
            if not paths:
                # A target with no geometry can be anywhere: hide the whole frame.
                self._report(track_id, "no track with this id, so the whole frame is masked", t)
                apply_effect(out, (0, 0, width, height), effect)
                continue
            placed = [path.at(t) for path in paths]
            if all(box.lost for box in placed):
                self._report(track_id, "lost, so its nearest known box stays masked", t)
            for box in placed:
                padding = effect.padding + box.extra_padding
                apply_effect(out, to_pixels(box.box, padding, width, height), effect)
        return out

    def _masks(self, t: float, until: float | None) -> list[tuple[str, Effect]]:
        """Each track to mask, with its effect. Two spans touching one frame give two entries."""
        found = []
        for track_id in sorted(self.state.masks):
            for span in self.state.masks[track_id]:
                if span.contains(t) or (until is not None and t < span.start < until):
                    found.append((track_id, span.effect))
        return found

    def _report(self, track_id: str, message: str, t: float) -> None:
        if track_id not in self._issues:
            self._issues[track_id] = RenderIssue(track_id, message, t)
            log.warning("render: %s at %.3f s: %s", track_id, t, message)


def render_frame(
    image: np.ndarray,
    t: float,
    state: TimelineState,
    tracks: Iterable[Track],
    until: float | None = None,
) -> np.ndarray:
    """One frame, without keeping a renderer around: see ``FrameRenderer.render``."""
    return FrameRenderer(state, tracks).render(image, t, until)
