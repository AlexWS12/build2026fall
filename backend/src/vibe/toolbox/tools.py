"""The tools: argument and result models, and the code that runs each one.

Query tools return compact JSON (ids, ranges, counts), never images. Edit tools build contract
ops and append them to the timeline; they never touch pixels or audio.
"""

import math
from collections.abc import Iterator, Sequence
from contextlib import contextmanager

from pydantic import Field

from vibe.contract import AddAudioFilter, AddMask, ContractError, Cut, Mute, Op, TimeRange, Unmute
from vibe.contract.common import Effect, Model
from vibe.contract.state import MuteSpan
from vibe.toolbox.context import ToolContext
from vibe.toolbox.spec import ToolError
from vibe.toolbox.time_range import TimeRangeError, check_range, resolve_range

# --- find_objects -------------------------------------------------------------------------


class FindObjectsArgs(Model):
    label: str = Field(min_length=1, description='Detector label, e.g. "face", "person".')


class FindObjectsResult(Model):
    track_ids: list[str]
    count: int


def find_objects(args: FindObjectsArgs, ctx: ToolContext) -> FindObjectsResult:
    """**Stub:** filters the tracks in the context. Real detection comes from F-10 via F-08.

    No tracks at all (``None``) means the video hasn't been analysed, which is an error: "blur
    all faces" must not succeed with nothing masked (privacy fails closed). An empty list means
    it was analysed and nothing is there.
    """
    if ctx.tracks is None:
        raise ToolError(
            f"find_objects: no analysis for this video, so I can't tell where any {args.label!r} is"
        )
    ids = [t.track_id for t in ctx.tracks if t.label == args.label]
    return FindObjectsResult(track_ids=ids, count=len(ids))


# --- resolve_time -------------------------------------------------------------------------


class ResolveTimeArgs(Model):
    phrase: str = Field(
        min_length=1,
        description='A time phrase that needs the video\'s length: "first half", '
        '"second half", "the last third", "first 5 seconds", "last 0:10", "after 0:30", '
        '"before 1:00", "between 0:10 and 0:40".',
    )


class ResolveTimeResult(Model):
    time_range: TimeRange


def resolve_time(args: ResolveTimeArgs, ctx: ToolContext) -> ResolveTimeResult:
    """The range ``args.phrase`` means in this video (F-12's ``resolve_range``).

    A missing duration is a plain ``ToolError``, not a ``TimeRangeError``, on purpose: the
    video hasn't been probed, which the user can't fix by rephrasing the time.
    """
    if ctx.duration is None:
        raise ToolError(f"resolve_time: needs the video duration to resolve {args.phrase!r}")
    try:
        return ResolveTimeResult(time_range=resolve_range(args.phrase, ctx.duration))
    except TimeRangeError as exc:
        raise TimeRangeError(f"resolve_time: {exc}") from None


# --- Edit tools ---------------------------------------------------------------------------


class EditResult(Model):
    op_ids: list[str]


class AddMaskArgs(Model):
    targets: list[str] = Field(min_length=1, description="Track ids to hide.")
    effect: Effect
    time_range: TimeRange | None = Field(default=None, description="Null = the whole clip.")


class CutRangeArgs(Model):
    time_range: TimeRange


class KeepRangeArgs(Model):
    time_range: TimeRange = Field(description="The part to keep; everything else is cut.")


class MuteArgs(Model):
    time_range: TimeRange | None = Field(default=None, description="Null = the whole clip.")
    speaker: str | None = Field(default=None, description="Speaker id; null = all audio.")


class UnmuteArgs(Model):
    time_range: TimeRange | None = Field(default=None, description="Null = the whole clip.")
    speaker: str | None = Field(
        default=None, description="Speaker id; null = the all-audio mute, not every speaker."
    )


class UnmuteResult(EditResult):
    still_muted: list[str] = Field(
        description="Speakers still muted somewhere in the range; an all-audio unmute keeps "
        "them, so the agent says so instead of reporting that all the sound is back."
    )


class DenoiseArgs(Model):
    time_range: TimeRange | None = Field(default=None, description="Null = the whole clip.")
    strength: float | None = Field(
        default=None, ge=0, le=1, description="0 = off, 1 = strongest; null = strongest."
    )


def _end(r: TimeRange) -> str:
    return "the end" if r.end is None else f"{r.end:g}s"


@contextmanager
def _timeline_errors(tool: str) -> Iterator[None]:
    """Turn what a timeline raises on reading or writing into one clear ``ToolError``.

    Anything else (a ``TypeError``, say) is a bug and propagates as it is.
    """
    try:
        yield
    except OSError as exc:  # a project's timeline.jsonl is missing or unwritable
        raise ToolError(f"{tool}: can't read or write the timeline: {exc}") from None
    except (ContractError, ValueError) as exc:
        # Either the edit breaks a contract rule or the stored log is unreadable (bad UTF-8,
        # bad header); the message says which, so don't blame the edit.
        raise ToolError(f"{tool}: couldn't apply the edit to the timeline: {exc}") from None


def _write(tool: str, ops: Sequence[Op], ctx: ToolContext) -> EditResult:
    """Append one request's ops as one unit: all of them land, or none do."""
    if ctx.log is None:
        raise ToolError(f"{tool}: needs a timeline to write to")
    if ctx.duration is not None:
        for op in ops:
            r = getattr(op, "time_range", None)
            if r is None:
                continue
            # Every edit, whatever wrote its range (the planner's "after 2:00", the LLM, a
            # hand edit), must fit in the video; else "blur faces after 2:00" masks nothing,
            # silently. Checked before writing, so a batch with one bad range writes nothing.
            try:
                check_range(r, ctx.duration)
            except TimeRangeError as exc:
                raise TimeRangeError(f"{tool}: {exc}") from None
    with _timeline_errors(tool):
        ctx.log.append_many(ops)
    return EditResult(op_ids=[op.op_id for op in ops])


def add_mask(args: AddMaskArgs, ctx: ToolContext) -> EditResult:
    op = AddMask(
        request_id=ctx.request_id,
        source=ctx.source,
        targets=list(dict.fromkeys(args.targets)),  # drop repeats, keep order
        effect=args.effect,
        time_range=args.time_range,
    )
    return _write("add_mask", [op], ctx)


def cut_range(args: CutRangeArgs, ctx: ToolContext) -> EditResult:
    r = args.time_range
    if ctx.duration is not None and r.start <= 0 and (r.end is None or r.end >= ctx.duration):
        # Never silently cut everything ("cut before 2:00" on a 1-minute clip): ask instead.
        raise ToolError(f"cut_range: {r.start:g}s to {_end(r)} would cut the whole video")
    op = Cut(request_id=ctx.request_id, source=ctx.source, time_range=args.time_range)
    return _write("cut_range", [op], ctx)


END_TOLERANCE_S = 0.1
"""How far past the video's end a keep's end may be and still mean "to the end": about one
frame at 10 fps or more. A 59.97 s clip shows as 1:00, so "keep 0:50 to 1:00" means the tail.
Only an end past the video counts: an end before it is always kept exactly, however close, so
no real tail is dropped. Further past the end is refused, not clamped."""


def keep_range(args: KeepRangeArgs, ctx: ToolContext) -> EditResult:
    """Cut everything before and after ``time_range``: ``[0, start)`` then ``[end, end of video)``.

    Needs the video's duration. Refuses a range past the end (beyond ``END_TOLERANCE_S``) rather
    than clamping like ``cut_range``, so it never keeps or cuts more than the user said. Refuses
    a keep that earlier cuts would leave empty. Both cuts are written as one unit.
    """
    if ctx.log is None:
        raise ToolError("keep_range: needs a timeline to write to")
    start, end = _keep_bounds(args.time_range, ctx.duration)
    with _timeline_errors("keep_range"):
        earlier_cuts = ctx.log.state().cuts
    if _uncut_seconds(start, end, earlier_cuts) <= 0:
        raise ToolError(
            f"keep_range: {start:g}s to {end:g}s is already cut, so keeping it would leave "
            "nothing; times are in the original video"
        )
    ranges = []
    if start > 0:
        ranges.append(TimeRange(start=0, end=start))
    if end < ctx.duration:  # type: ignore[operator]  # checked by _keep_bounds
        ranges.append(TimeRange(start=end))
    ops = [Cut(request_id=ctx.request_id, source=ctx.source, time_range=t) for t in ranges]
    return _write("keep_range", ops, ctx)


def _keep_bounds(r: TimeRange, d: float | None) -> tuple[float, float]:
    """The range to keep, checked against the video duration, with the end filled in."""
    if d is None:
        raise ToolError("keep_range: needs the video duration")
    if not math.isfinite(d) or d <= 0:
        raise ToolError(f"keep_range: the video duration must be finite and positive, not {d}")
    if not math.isfinite(r.start) or (r.end is not None and not math.isfinite(r.end)):
        raise ToolError(f"keep_range: times must be finite numbers, not {r.start} to {_end(r)}")
    if not r.start < d:
        raise ToolError(f"keep_range: the range starts at or after the end of the video ({d:g}s)")
    if r.end is not None and r.end > d + END_TOLERANCE_S:
        raise ToolError(
            f"keep_range: the range ends past the end of the video ({d:g}s); to keep everything "
            f"after {r.start:g}s, say 'keep from {r.start:g}s to the end'"
        )
    end = d if r.end is None or r.end >= d else r.end
    if r.start <= 0 and end >= d:
        raise ToolError("keep_range: that keeps the whole video, so there is nothing to cut")
    return r.start, end


def _uncut_seconds(start: float, end: float, cuts: Sequence[TimeRange]) -> float:
    """How much of ``[start, end)`` the (merged, non-overlapping) ``cuts`` leave."""
    left = end - start
    for c in cuts:
        c_end = end if c.end is None else c.end
        left -= max(0.0, min(end, c_end) - max(start, c.start))
    return left


def mute(args: MuteArgs, ctx: ToolContext) -> EditResult:
    op = Mute(
        request_id=ctx.request_id,
        source=ctx.source,
        time_range=args.time_range,
        speaker=args.speaker,
    )
    return _write("mute", [op], ctx)


def unmute(args: UnmuteArgs, ctx: ToolContext) -> UnmuteResult:
    """Bring the sound back over a range, for the same ``speaker`` a mute was for. Unlike undo,
    it can lift part of a mute ("unmute 0:12 to 0:15") or one made by an earlier request.

    Refuses when no mute for ``speaker`` overlaps the range, so "unmute" never reports success
    for an edit that changes nothing.
    """
    if ctx.log is None:
        raise ToolError("unmute: needs a timeline to write to")
    try:
        mutes = [m for m in ctx.log.state().mutes if _overlaps(m, args.time_range)]
    except OSError as exc:
        raise ToolError(f"unmute: can't read the timeline: {exc}") from None
    except (ContractError, ValueError) as exc:
        raise ToolError(f"unmute: the stored timeline is unreadable: {exc}") from None
    others = sorted(
        {m.speaker for m in mutes if m.speaker is not None and m.speaker != args.speaker}
    )
    if not any(m.speaker == args.speaker for m in mutes):
        where = "anywhere" if args.time_range is None else f"from {_range(args.time_range)}"
        who = "all audio" if args.speaker is None else f"speaker {args.speaker!r}"
        hint = f"; only {', '.join(others)} muted there, say which to unmute" if others else ""
        raise ToolError(f"unmute: {who} isn't muted {where}{hint}")
    op = Unmute(
        request_id=ctx.request_id,
        source=ctx.source,
        time_range=args.time_range,
        speaker=args.speaker,
    )
    result = _write("unmute", [op], ctx)
    return UnmuteResult(op_ids=result.op_ids, still_muted=others)


def _overlaps(span: MuteSpan, r: TimeRange | None) -> bool:
    if r is None:
        return True
    return (r.end is None or span.start < r.end) and (span.end is None or span.end > r.start)


def _range(r: TimeRange) -> str:
    return f"{r.start:g}s to {_end(r)}"


def denoise(args: DenoiseArgs, ctx: ToolContext) -> EditResult:
    op = AddAudioFilter(
        request_id=ctx.request_id,
        source=ctx.source,
        filter="denoise",
        strength=args.strength,
        time_range=args.time_range,
    )
    return _write("denoise", [op], ctx)
