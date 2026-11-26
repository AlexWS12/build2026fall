"""Target something on screen by hand (F-14): click a target, or draw a box around one.

    POST /api/projects/{id}/targets     mask what was clicked or boxed; the reply says how

Three ways a request ends up masking a track, said in the reply's ``how``:

- ``picked``: a click (``point``) on a target the analysis or an earlier box already follows.
  The smallest box under the click wins, so a face inside a person box can be clicked.
- ``matched``: a drawn ``box`` that an already followed target fills (IoU ``MATCH_IOU`` or
  more). That track is masked, so drawing over a detected person doesn't add a copy of it.
- ``followed``: anything else drawn. The box is followed through the clip on CPU
  (``vibe.modules.vision.follow``) and saved as a hand-drawn track in ``tracks.user.json``.

The mask is a hand edit like the review queue's: one ``mask_targets`` (``add_mask`` with
literal track ids, in ``HAND_TOOLS``) through the toolbox with source ``user`` and its own
request id, so undo takes it back. A track that was ``picked`` or ``matched`` is masked while
it's on screen (``while_tracked``, as for one described target, F-13). A ``followed`` box is
masked over the whole clip: where the follower lost it, the renderer holds its last box,
marked lost, and the review queue lists that stretch, so a weak tracker never quietly unmasks
something (privacy fails closed).

Undo removes the mask, not the drawn track: geometry isn't in the op log (ARCHITECTURE "The
contract"), and an unmasked track changes nothing in the render.
"""

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import Field, ValidationError, field_validator, model_validator

from vibe.agent import Plan, ToolCall, execute_plan
from vibe.agent.describe import clock
from vibe.api.projects import (
    StorageDep,
    _frame_index,
    _one_request_at_a_time,
    _open,
    _tracks,
)
from vibe.contract import Track, new_id
from vibe.contract.common import EffectType, Model, TimeRange
from vibe.modules.vision.detection import iou
from vibe.modules.vision.follow import follow_box
from vibe.render.geometry import TrackPath
from vibe.storage import Project, Storage
from vibe.toolbox import UI_TOOLS
from vibe.video import FrameIndex, VideoError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects", tags=["projects"])

MATCH_IOU = 0.5
"""A drawn box over an already followed target counts as that target from this much overlap."""

MIN_BOX = 0.005
"""The smallest box side, as a fraction of the frame: under it, a drag is taken for a slip."""

Box = tuple[float, float, float, float]

_EFFECT_WORDS = {"blur": "Blurred", "pixelate": "Pixelated", "box": "Covered"}


class TargetRequest(Model):
    t: float = Field(ge=0, description="Source time of the frame the user clicked or drew on.")
    point: tuple[float, float] | None = Field(
        default=None, description="A click, normalized [x, y]: mask the target under it."
    )
    box: Box | None = Field(
        default=None, description="A drawn box, normalized [x1, y1, x2, y2]: mask what's in it."
    )
    effect: EffectType = "blur"
    label: str = Field(
        default="object",
        min_length=1,
        max_length=40,
        pattern=r"^[a-z][a-z0-9 _-]*$",
        description='What a newly followed box is called, e.g. "person". Not used otherwise.',
    )

    @field_validator("point")
    @classmethod
    def _point_in_frame(cls, point: tuple[float, float] | None) -> tuple[float, float] | None:
        if point is not None and not all(0 <= v <= 1 for v in point):
            raise ValueError("point must be normalized: x and y between 0 and 1")
        return point

    @field_validator("box")
    @classmethod
    def _box_in_frame(cls, box: Box | None) -> Box | None:
        if box is None:
            return box
        x1, y1, x2, y2 = box
        if not (0 <= x1 < x2 <= 1 and 0 <= y1 < y2 <= 1):
            raise ValueError("box must be normalized [x1, y1, x2, y2] with x1 < x2 and y1 < y2")
        if x2 - x1 < MIN_BOX or y2 - y1 < MIN_BOX:
            raise ValueError("box is too small to follow; drag a bigger box around the target")
        return box

    @model_validator(mode="after")
    def _point_or_box(self) -> "TargetRequest":
        if (self.point is None) == (self.box is None):
            raise ValueError("give either a point (a click) or a box (a drawn box), not both")
        return self


How = Literal["picked", "matched", "followed"]


class TargetReply(Model):
    request_id: str
    how: How = Field(
        description="picked: the clicked target; matched: an already followed target the box "
        "fills; followed: a new hand-drawn track."
    )
    track_id: str
    label: str
    followed: TimeRange | None = Field(
        default=None, description="For a followed box: from its first keyframe to its last."
    )
    lost_before: float | None = Field(
        default=None, description="Followed box: lost going back, at this time; null = never."
    )
    lost_after: float | None = Field(
        default=None, description="Followed box: lost going forward, at this time; null = never."
    )
    reply: str = Field(description="What happened, in plain words.")


@router.post("/{project_id}/targets")
def add_target(project_id: str, body: TargetRequest, storage: StorageDep) -> TargetReply:
    """Mask the target clicked (``point``) or boxed (``box``) on the frame at ``t``, as a hand
    edit (F-14). 400 if nothing is under a click, ``t`` is past the end, or the toolbox refuses.
    """
    project = _open(storage, project_id)
    with _one_request_at_a_time(project.id):
        _, tracks = _tracks(storage, project)
        index = _frame_index(storage, project)
        if body.t > index.end:
            raise HTTPException(
                status_code=400, detail=f"{clock(body.t)} is past the end of the video"
            )
        if body.point is not None:
            track = _under(tracks, body.t, body.point)
            if track is None:
                raise HTTPException(
                    status_code=400,
                    detail="nothing is followed there; drag a box around the target instead",
                )
            return _mask(project, tracks, index.end, track, "picked", body)
        assert body.box is not None
        track = _matching(tracks, body.t, body.box)
        if track is not None:
            return _mask(project, tracks, index.end, track, "matched", body)
        return _follow_and_mask(storage, project, tracks, index, body)


# --- Finding the target ---------------------------------------------------------------------


def _seen_at(tracks: list[Track], t: float) -> list[tuple[Track, Box]]:
    """Every track with a box near ``t`` (not held long past its keyframes), and that box."""
    seen = []
    for track in tracks:
        placed = TrackPath(track).at(t)
        if not placed.lost:
            seen.append((track, placed.box))
    return seen


def _under(tracks: list[Track], t: float, point: tuple[float, float]) -> Track | None:
    x, y = point
    hits = [(tr, b) for tr, b in _seen_at(tracks, t) if b[0] <= x <= b[2] and b[1] <= y <= b[3]]
    if not hits:
        return None
    # The smallest box: in a crowd, a face sits inside a bigger person box.
    return min(hits, key=lambda hit: (hit[1][2] - hit[1][0]) * (hit[1][3] - hit[1][1]))[0]


def _matching(tracks: list[Track], t: float, box: Box) -> Track | None:
    best = max(((iou(box, b), tr) for tr, b in _seen_at(tracks, t)), default=None, key=_first)
    return best[1] if best is not None and best[0] >= MATCH_IOU else None


def _first(pair: tuple[float, Track]) -> float:
    return pair[0]


# --- Masking it -----------------------------------------------------------------------------


def _follow_and_mask(
    storage: Storage,
    project: Project,
    tracks: list[Track],
    index: FrameIndex,
    body: TargetRequest,
) -> TargetReply:
    assert body.box is not None
    try:
        followed = follow_box(
            storage.video_path(project.info.video),
            body.t,
            body.box,
            track_id=new_id("usr"),
            label=body.label,
            index=index,
        )
    except (VideoError, OSError, ValueError) as e:
        logger.exception("following a box in project %s failed", project.id)
        raise HTTPException(status_code=500, detail="couldn't read this project's video") from e
    try:
        drawn = project.user_tracks()
        project.save_user_tracks([*drawn, followed.track])
    except (OSError, ValidationError) as e:
        logger.exception("saving a drawn track in project %s failed", project.id)
        raise HTTPException(
            status_code=500, detail="couldn't save the box: the project's files can't be written"
        ) from e
    try:
        return _mask(
            project,
            [*tracks, followed.track],
            index.end,
            followed.track,
            "followed",
            body,
            lost=(followed.lost_before, followed.lost_after),
        )
    except Exception:
        # The mask wasn't written: don't leave a track nothing uses behind.
        try:
            project.save_user_tracks(drawn)
        except OSError:
            logger.exception("taking back a drawn track in project %s failed", project.id)
        raise


def _mask(
    project: Project,
    tracks: list[Track],
    duration: float,
    track: Track,
    how: How,
    body: TargetRequest,
    lost: tuple[float | None, float | None] = (None, None),
) -> TargetReply:
    args: dict[str, object] = {"targets": [track.track_id], "effect": {"type": body.effect}}
    if how != "followed":
        args["while_tracked"] = True
    call = ToolCall(step_id="s1", tool="mask_targets", args=args)
    request_id = new_id("r")
    try:
        done = execute_plan(
            Plan(calls=(call,)),
            project,
            request_id=request_id,
            source="user",
            duration=duration,
            tracks=tracks,
            registry=UI_TOOLS,
        )
    except (OSError, ValueError) as e:  # an unreadable timeline, or a write that failed
        logger.exception("masking a target in project %s failed", project.id)
        raise HTTPException(
            status_code=500,
            detail="couldn't apply this: the project's files can't be read or written.",
        ) from e
    if not done.ok:
        reason = (done.error or "the edit was refused").removeprefix("add_mask: ")
        reason = reason.removeprefix("mask_targets: ")
        raise HTTPException(status_code=400, detail=reason)
    first, last = track.keyframes[0].t, track.keyframes[-1].t
    return TargetReply(
        request_id=request_id,
        how=how,
        track_id=track.track_id,
        label=track.label,
        followed=TimeRange(start=first, end=last) if how == "followed" else None,
        lost_before=lost[0],
        lost_after=lost[1],
        reply=_say(how, track, body.effect, lost),
    )


def _say(how: How, track: Track, effect: str, lost: tuple[float | None, float | None]) -> str:
    done = _EFFECT_WORDS.get(effect, "Masked")
    article = "with a black box " if effect == "box" else ""
    if how == "picked":
        return f"{done} the {track.label} you clicked {article}while it's on screen."
    if how == "matched":
        return f"{done} the {track.label} in your box {article}while it's on screen."
    first, last = track.keyframes[0].t, track.keyframes[-1].t
    text = f"{done} your box {article}and followed it from {clock(first)} to {clock(last)}."
    gaps = []
    if lost[0] is not None:
        gaps.append(f"before {clock(lost[0])}")
    if lost[1] is not None:
        gaps.append(f"after {clock(lost[1])}")
    if gaps:
        text += (
            f" I lost it {' and '.join(gaps)}, so the mask stays where it was last seen there. "
            "Check those stretches in the review queue, or draw a box there too."
        )
    return text
