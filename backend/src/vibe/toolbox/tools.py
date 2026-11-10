"""The tools: argument and result models, and the code that runs each one.

Query tools return compact JSON (ids, ranges, counts), never images. Edit tools build contract
ops and append them to the timeline; they never touch pixels or audio.
"""

import math
import re
import unicodedata

from pydantic import Field

from vibe.contract import AddAudioFilter, AddMask, ContractError, Cut, Mute, Op, TimeRange
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


class CutRangeArgs(Model):
    time_range: TimeRange


class MuteArgs(Model):
    time_range: TimeRange | None = Field(default=None, description="Null = the whole clip.")
    speaker: str | None = Field(default=None, description="Speaker id; null = all audio.")


class DenoiseArgs(Model):
    time_range: TimeRange | None = Field(default=None, description="Null = the whole clip.")
    strength: float | None = Field(
        default=None, ge=0, le=1, description="0 = off, 1 = strongest; null = strongest."
    )


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


def denoise(args: DenoiseArgs, ctx: ToolContext) -> EditResult:
    op = AddAudioFilter(
        request_id=ctx.request_id,
        source=ctx.source,
        filter="denoise",
        strength=args.strength,
        time_range=args.time_range,
    )
    return _write("denoise", op, ctx)
