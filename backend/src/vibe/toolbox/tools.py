"""The tools: argument and result models, and the code that runs each one.

Query tools return compact JSON (ids, ranges, counts), never images. Edit tools build contract
ops and append them to the timeline; they never touch pixels or audio.
"""

import math
import re

from pydantic import Field

from vibe.contract import AddMask, ContractError, Cut, Mute, Op, TimeRange
from vibe.contract.common import Effect, Model
from vibe.toolbox.context import ToolContext
from vibe.toolbox.spec import ToolError
from vibe.toolbox.timecodes import TimeParseError, is_bare_number, parse_timestamp

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
        '"second half", "first 5 seconds", "last 0:10".',
    )


class ResolveTimeResult(Model):
    time_range: TimeRange


_EDGE = re.compile(r"(first|last)\s+(.+)")


def resolve_time(args: ResolveTimeArgs, ctx: ToolContext) -> ResolveTimeResult:
    if ctx.duration is None:
        raise ToolError(f"resolve_time: needs the video duration to resolve {args.phrase!r}")
    if not math.isfinite(ctx.duration) or ctx.duration <= 0:
        raise ToolError(f"resolve_time: the video duration must be positive, not {ctx.duration}")
    d, phrase = ctx.duration, args.phrase.strip().lower()
    if phrase == "first half":
        return ResolveTimeResult(time_range=TimeRange(start=0, end=d / 2))
    if phrase == "second half":
        return ResolveTimeResult(time_range=TimeRange(start=d / 2))
    match = _EDGE.fullmatch(phrase)
    if match is None or is_bare_number(match.group(2)):
        raise ToolError(f"resolve_time: can't resolve {args.phrase!r}")
    try:
        seconds = parse_timestamp(match.group(2))
    except TimeParseError as exc:
        raise ToolError(f"resolve_time: {exc}") from None
    if seconds <= 0:
        raise ToolError(f"resolve_time: {args.phrase!r} is an empty range")
    if seconds >= d:
        # Don't stretch it to the whole video: "cut the last 2 minutes" of a 1-minute clip
        # would silently cut everything. Fail so the user is asked.
        raise ToolError(
            f"resolve_time: {args.phrase!r} is as long as the whole video ({d:g} seconds)"
        )
    if match.group(1) == "first":
        return ResolveTimeResult(time_range=TimeRange(start=0, end=seconds))
    return ResolveTimeResult(time_range=TimeRange(start=d - seconds))


# --- Edit tools ---------------------------------------------------------------------------


class EditResult(Model):
    op_ids: list[str]


class AddMaskArgs(Model):
    targets: list[str] = Field(min_length=1, description="Track ids to hide.")
    effect: Effect
    time_range: TimeRange | None = Field(default=None, description="Null = the whole clip.")


class CutRangeArgs(Model):
    time_range: TimeRange


class MuteArgs(Model):
    time_range: TimeRange | None = Field(default=None, description="Null = the whole clip.")
    speaker: str | None = Field(default=None, description="Speaker id; null = all audio.")


def _end(r: TimeRange) -> str:
    return "the end" if r.end is None else f"{r.end:g}s"


def _write(tool: str, op: Op, ctx: ToolContext) -> EditResult:
    if ctx.log is None:
        raise ToolError(f"{tool}: needs a timeline to write to")
    try:
        ctx.log.append(op)
    except OSError as exc:  # a project's timeline.jsonl is missing or unwritable
        raise ToolError(f"{tool}: can't read or write the timeline: {exc}") from None
    except (ContractError, ValueError) as exc:
        # Either the edit breaks a contract rule or the stored log is unreadable (bad UTF-8,
        # bad header); the message says which, so don't blame the edit.
        raise ToolError(f"{tool}: couldn't apply the edit to the timeline: {exc}") from None
    return EditResult(op_ids=[op.op_id])


def add_mask(args: AddMaskArgs, ctx: ToolContext) -> EditResult:
    op = AddMask(
        request_id=ctx.request_id,
        source=ctx.source,
        targets=list(dict.fromkeys(args.targets)),  # drop repeats, keep order
        effect=args.effect,
        time_range=args.time_range,
    )
    return _write("add_mask", op, ctx)


def cut_range(args: CutRangeArgs, ctx: ToolContext) -> EditResult:
    r = args.time_range
    if ctx.duration is not None and r.start <= 0 and (r.end is None or r.end >= ctx.duration):
        # Never silently cut everything ("cut before 2:00" on a 1-minute clip): ask instead.
        raise ToolError(f"cut_range: {r.start:g}s to {_end(r)} would cut the whole video")
    op = Cut(request_id=ctx.request_id, source=ctx.source, time_range=args.time_range)
    return _write("cut_range", op, ctx)


def mute(args: MuteArgs, ctx: ToolContext) -> EditResult:
    op = Mute(
        request_id=ctx.request_id,
        source=ctx.source,
        time_range=args.time_range,
        speaker=args.speaker,
    )
    return _write("mute", op, ctx)
