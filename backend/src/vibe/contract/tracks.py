"""Tracks: where a visual target is over time, from detectors, the tracker or a drawn box.

Tracks live in the analysis cache, not in the op log. Ops only name track ids, so running a
better detector later doesn't invalidate the edits, and in live mode tracks arrive frame by
frame from the camera. The renderer combines both: ``render(video, state, tracks)``.
"""

from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from vibe.contract.common import Model

Box = Annotated[
    tuple[float, float, float, float],
    Field(description="[x1, y1, x2, y2], normalized to 0..1 of the frame width and height."),
]
"""Normalized so the same track works on a 720p preview, a 1080p export and a live stream."""

KeyframeState = Literal["detected", "tracked", "uncertain", "user"]
"""``detected``: a model saw it. ``tracked``: the tracker carried it between detections.
``uncertain``: low confidence, so masked with extra padding and flagged for review.
``user``: drawn or corrected by hand."""


class Keyframe(Model):
    t: float = Field(ge=0, description="Source time in seconds.")
    box: Box
    conf: float = Field(ge=0, le=1)
    state: KeyframeState

    @field_validator("box")
    @classmethod
    def _valid_box(cls, box: tuple[float, float, float, float]) -> tuple[float, ...]:
        x1, y1, x2, y2 = box
        if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
            raise ValueError(f"box {box} must be normalized [x1, y1, x2, y2] with x1<x2, y1<y2")
        return box


class Track(Model):
    track_id: str
    label: str = Field(description='What it is, e.g. "person", "face", "license_plate".')
    source: Literal["detector", "user"]
    identity: str | None = Field(
        default=None, description="Matched enrolled identity, if any (for rules)."
    )
    keyframes: list[Keyframe] = Field(min_length=1)

    @model_validator(mode="after")
    def _sorted_by_time(self) -> "Track":
        times = [k.t for k in self.keyframes]
        if any(b <= a for a, b in zip(times, times[1:], strict=False)):
            raise ValueError("keyframes must be sorted by t with no duplicate times")
        return self
