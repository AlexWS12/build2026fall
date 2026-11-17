"""The tools: argument and result models, and the code that runs each one.

Query tools return compact JSON (ids, ranges, counts), never images. Edit tools build contract
ops and append them to the timeline; they never touch pixels or audio.
"""

import math
import re
import unicodedata
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Literal

from pydantic import Field

from vibe.contract import (
    AddAudioFilter,
    AddMask,
    ConfirmMask,
    ContractError,
    Cut,
    Mute,
    Op,
    Redo,
    TimeRange,
    Undo,
    Unmute,
)
from vibe.contract.common import Effect, Model
from vibe.contract.state import Span
from vibe.toolbox.context import DescriberError, ToolContext
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


# --- find_by_description ------------------------------------------------------------------


class FindByDescriptionArgs(Model):
    label: str = Field(min_length=1, description='Detector label to choose among, e.g. "person".')
    description: str = Field(
        min_length=1,
        description='What tells the targets apart, as the user said it: "in red", '
        '"wearing a hat". Not a name or "me": identity is a different question.',
    )
    exclude: bool = Field(
        default=False,
        description='False: the tracks that match ("the person in red"). True: every track '
        'with the label except the ones that match ("everyone except the person in red").',
    )
    unsure: Literal["mask", "leave"] = Field(
        default="mask",
        description='What to do with tracks the answer is unsure for. "mask" (the default, '
        'privacy fails closed): they are returned too. "leave": only sure answers are returned; '
        "use it only when the user asks for sure matches only.",
    )


class FindByDescriptionResult(Model):
    track_ids: list[str]
    count: int
    unsure: list[str] = Field(
        description="The tracks the answer was unsure for, so the agent can say so and the "
        'review queue can show them. In track_ids unless the request said unsure="leave".'
    )
    left_out: int = Field(description="Tracks with the label that are not in track_ids.")


def find_by_description(args: FindByDescriptionArgs, ctx: ToolContext) -> FindByDescriptionResult:
    """The tracks with ``label`` that match a description, or with ``exclude`` all but those.

    Unsure tracks are returned either way (privacy fails closed): "blur the person in red"
    masks anyone who might be them, and "blur everyone except the person in red" leaves out
    only the tracks that surely are. A track the describer says nothing about is unsure.

    ``unsure="leave"`` is the user saying "only the ones you're sure about": unsure tracks are
    reported but not returned. Nobody sure is then an error, not an empty answer.
    """
    name = "find_by_description"
    if ctx.tracks is None:
        raise ToolError(
            f"{name}: no analysis for this video, so I can't tell where any {args.label!r} is"
        )
    if ctx.describer is None:
        raise ToolError(f"{name}: nothing here can look at the video to check {args.description!r}")
    candidates = [t for t in ctx.tracks if t.label == args.label]
    if not candidates:
        return FindByDescriptionResult(track_ids=[], count=0, unsure=[], left_out=0)
    try:
        verdicts = ctx.describer.ask(candidates, args.description, ctx.tracks)
    except DescriberError as exc:
        raise ToolError(f"{name}: couldn't check {args.description!r}: {exc}") from None
    leave_out = "yes" if args.exclude else "no"
    ids, unsure = [], []
    for track in candidates:
        verdict = verdicts.get(track.track_id, "unsure")
        if verdict == leave_out:
            continue
        if verdict not in ("yes", "no"):
            unsure.append(track.track_id)
            if args.unsure == "leave":
                continue
        ids.append(track.track_id)
    if not ids and unsure:
        raise ToolError(
            f"{name}: no {args.label} surely matches {args.description!r}, and {len(unsure)} "
            "might; ask again without 'only if sure' to hide those too"
        )
    return FindByDescriptionResult(
        track_ids=ids, count=len(ids), unsure=unsure, left_out=len(candidates) - len(ids)
    )


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


# --- find_speech --------------------------------------------------------------------------

SPEECH_PAD = 0.15
"""Seconds added on each side of a spoken phrase, because Whisper's word times are approximate
and a clipped word stays audible. shortcut: not measured, tune with F-09 speech labels."""


class FindSpeechArgs(Model):
    text: str = Field(min_length=1, description='Words as spoken, e.g. "hola" or "hello world".')


class FindSpeechResult(Model):
    time_range: TimeRange


def _plain_word(text: str) -> str:
    """One word lowercased without accents or punctuation, so "que" finds "¿Qué" and "dont"
    finds "don't" (but "don" doesn't)."""
    text = unicodedata.normalize("NFKD", text.casefold())
    return "".join(re.findall(r"\w+", "".join(c for c in text if not unicodedata.combining(c))))


def _plain_words(text: str) -> list[str]:
    """``_plain_word`` for each whitespace-separated word, dropping ones with no letters."""
    return [w for w in map(_plain_word, text.split()) if w]


def find_speech(args: FindSpeechArgs, ctx: ToolContext) -> FindSpeechResult:
    """When ``text`` is said, from the video's transcript (F-31), padded by ``SPEECH_PAD``.

    Said more than once is an error that lists the times, so the user picks one instead of
    the tool guessing.
    """
    if ctx.transcript is None:
        raise ToolError("find_speech: this video has no transcript yet; transcribe it first")
    wanted = _plain_words(args.text)
    if not wanted:
        raise ToolError(f"find_speech: {args.text!r} has no words to look for")
    # Whole Whisper words, so a query never matches inside one ("it" in "it's").
    said = [
        (plain, word)
        for segment in ctx.transcript.segments
        for word in segment.words
        if (plain := _plain_word(word.text))
    ]
    n = len(wanted)
    hits = [said[i : i + n] for i in range(len(said) - n + 1)]
    hits = [h for h in hits if [plain for plain, _ in h] == wanted]
    if not hits:
        raise ToolError(f"find_speech: {args.text!r} isn't said in the transcript")
    if len(hits) > 1:
        times = ", ".join(f"{h[0][1].start:.2f}s" for h in hits)
        raise ToolError(
            f"find_speech: {args.text!r} is said {len(hits)} times (at {times}); "
            "edit the one you mean by its time instead, e.g. mute 0:12 to 0:13"
        )
    start = max(0.0, hits[0][0][1].start - SPEECH_PAD)
    end = hits[0][-1][1].end + SPEECH_PAD
    if ctx.duration is not None:
        end = min(end, ctx.duration)
    return FindSpeechResult(time_range=TimeRange(start=start, end=end))


# --- Edit tools ---------------------------------------------------------------------------


class EditResult(Model):
    op_ids: list[str]


class AddMaskArgs(Model):
    targets: list[str] = Field(min_length=1, description="Track ids to hide.")
    effect: Effect
    time_range: TimeRange | None = Field(default=None, description="Null = the whole clip.")
    while_tracked: bool = Field(
        default=False,
        description="False: every target stays hidden for the whole range; where the tracker "
        "lost it, its last box is held. True: each target is hidden only while its track "
        "exists (plus at least half a second each side), so the mask follows it and doesn't stay "
        "behind. For one described target, not for hiding everyone.",
    )


class ConfirmMaskArgs(Model):
    targets: list[str] = Field(min_length=1, description="Track ids to mark as checked.")
    time_range: TimeRange = Field(
        description="The stretch that was checked; each target must be masked somewhere in it."
    )


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


TRACKED_PAD_S = 0.5
"""With ``while_tracked``, the least time before a track's first keyframe and after its last
one that the mask stays: at least the renderer's own hold (``render.geometry.HOLD_SECONDS``),
so the mask never stops while the renderer still holds the target's box. A shorter pad left a
gap of up to a quarter second at each end, and at every handover between two fragments of one
person. Two fragments up to twice this apart are covered without a gap. Not imported from the
renderer, which would load av and numpy into the planner; a test keeps the two in step."""

TRACKED_GRID_S = 0.25
"""``while_tracked`` ranges are rounded outward to this, so targets that come and go together
share one op instead of each writing its own. Rounding only ever adds time: past the hold the
renderer keeps the held box (marked lost), which errs on the masked side."""


def _tracked_ranges(
    args: AddMaskArgs, targets: Sequence[str], ctx: ToolContext
) -> dict[tuple[float, float], list[str]]:
    """Each target's own time on screen, cut to the requested range: ``{(start, end): ids}``."""
    if ctx.tracks is None:
        raise ToolError(
            "add_mask: no analysis for this video, so I can't tell when a target is seen"
        )
    by_id = {t.track_id: t for t in ctx.tracks}
    low = args.time_range.start if args.time_range is not None else 0.0
    high = args.time_range.end if args.time_range is not None else None
    if ctx.duration is not None:
        high = ctx.duration if high is None else min(high, ctx.duration)
    ranges: dict[tuple[float, float], list[str]] = {}
    for track_id in targets:
        track = by_id.get(track_id)
        if track is None:
            raise ToolError(f"add_mask: no track {track_id!r} in this video's analysis")
        first, last = track.keyframes[0].t, track.keyframes[-1].t
        start = max(low, math.floor((first - TRACKED_PAD_S) / TRACKED_GRID_S) * TRACKED_GRID_S)
        end = math.ceil((last + TRACKED_PAD_S) / TRACKED_GRID_S) * TRACKED_GRID_S
        if high is not None:
            end = min(end, high)
        if end > start:  # else: not on screen in the requested range
            ranges.setdefault((start, end), []).append(track_id)
    if not ranges:
        raise ToolError("add_mask: none of the targets is on screen in that time range")
    return ranges


def add_mask(args: AddMaskArgs, ctx: ToolContext) -> EditResult:
    targets = list(dict.fromkeys(args.targets))  # drop repeats, keep order
    if not args.while_tracked:
        ranges: dict[tuple[float, float] | None, list[str]] = {None: targets}
    else:
        ranges = dict(sorted(_tracked_ranges(args, targets, ctx).items()))
    ops = [
        AddMask(
            request_id=ctx.request_id,
            source=ctx.source,
            targets=ids,
            effect=args.effect,
            time_range=args.time_range if span is None else TimeRange(start=span[0], end=span[1]),
        )
        for span, ids in ranges.items()
    ]
    return _write("add_mask", ops, ctx)


def confirm_mask(args: ConfirmMaskArgs, ctx: ToolContext) -> EditResult:
    """Mark masked stretches as checked by a person (F-41), for hand edits from the review queue.

    Refuses a target with no mask over any part of the range, so a confirmation never covers a
    track that isn't hidden there. Overlap, not containment: a review item may start a little
    before its mask span when it comes from frame times.
    """
    if ctx.log is None:
        raise ToolError("confirm_mask: needs a timeline to write to")
    with _timeline_errors("confirm_mask"):
        masks = ctx.log.state().masks
    r = args.time_range
    targets = list(dict.fromkeys(args.targets))  # drop repeats, keep order
    for target in targets:
        if not any(_overlaps(m, r) for m in masks.get(target, [])):
            raise ToolError(f"confirm_mask: {target!r} isn't masked from {_range(r)}")
    op = ConfirmMask(request_id=ctx.request_id, source=ctx.source, targets=targets, time_range=r)
    return _write("confirm_mask", [op], ctx)


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


def _overlaps(span: Span, r: TimeRange | None) -> bool:
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


# --- undo / redo --------------------------------------------------------------------------


class UndoArgs(Model):
    """No arguments: undo is linear, so it always acts on the latest request in effect."""


class RedoArgs(Model):
    """No arguments: redo always brings back the most recently undone request."""


class UndoResult(EditResult):
    undone_request_id: str = Field(description="The request whose ops are no longer in effect.")


class RedoResult(EditResult):
    redone_request_id: str = Field(description="The request whose ops are in effect again.")


def _next_in_history(tool: str, ctx: ToolContext) -> str:
    """The request ``tool`` ("undo" or "redo") would act on. Refuses when there is none."""
    if ctx.log is None:
        raise ToolError(f"{tool}: needs a timeline to write to")
    with _timeline_errors(tool):
        history = ctx.log.history()
    target = history.can_undo if tool == "undo" else history.can_redo
    if target is None:
        raise ToolError(f"{tool}: nothing to {tool}")
    return target


def undo(args: UndoArgs, ctx: ToolContext) -> UndoResult:
    """Write the contract's ``undo`` op for the latest request still in effect. Nothing is
    deleted: the request's ops stay in the log, so ``redo`` can bring them back."""
    target = _next_in_history("undo", ctx)
    op = Undo(request_id=ctx.request_id, source=ctx.source, undo_request_id=target)
    return UndoResult(op_ids=_write("undo", [op], ctx).op_ids, undone_request_id=target)


def redo(args: RedoArgs, ctx: ToolContext) -> RedoResult:
    """Write the contract's ``redo`` op for the most recently undone request."""
    target = _next_in_history("redo", ctx)
    op = Redo(request_id=ctx.request_id, source=ctx.source, redo_request_id=target)
    return RedoResult(op_ids=_write("redo", [op], ctx).op_ids, redone_request_id=target)
