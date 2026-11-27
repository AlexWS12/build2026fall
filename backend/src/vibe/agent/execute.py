"""Plan executor: runs a plan's tool calls and writes what they produce to the timeline.

All or nothing. The whole plan first runs against a copy of the op log in memory. Only if every
step succeeds are the new ops written to the real timeline, in one batch, so a request's ops
are contiguous and a failing step never leaves half a request behind. A step the toolbox
refuses (``ToolError``) is a normal outcome, reported with the step and the reason; anything
else is a bug or a broken file and propagates.

No HTTP here: the API calls ``execute_request``, tests call ``execute_plan`` with an ``OpLog``.
"""

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import Field, ValidationError

from vibe.agent.plan import Plan, as_ref
from vibe.agent.planner import PlanError
from vibe.agent.validate import validate_plan
from vibe.contract import Op, OpLog, TimelineState, Track
from vibe.contract.common import Model, Source
from vibe.storage import Project, Storage
from vibe.toolbox import TOOLS, Describer, Returns, ToolContext, ToolError, ToolSpec, call_tool

if TYPE_CHECKING:
    # Type checkers only: planning and running a plan need no media or model code (a toolbox
    # test checks that importing vibe.agent loads none). ``execute_request`` imports what it
    # needs to read a stored video when it's called.
    from vibe.modules.transcribe import Transcript

logger = logging.getLogger(__name__)

UNREADABLE_ANALYSIS = (
    "this video's analysis can't be used: it's unreadable, or several are cached and none is "
    "marked as the default. I can't tell where anything is, so nothing was hidden"
)
"""Why a step that needs tracks failed when the cache has something but it can't be trusted.
Fixed text: the cache's own errors name files on the server."""


class StepReport(Model):
    """One tool call as it ran."""

    step_id: str
    tool: str
    args: dict[str, Any] = Field(description="The arguments, with every $ref filled in.")
    result: dict[str, Any] | None = Field(
        default=None, description="What the tool returned, as compact JSON. Null if it failed."
    )
    ok: bool


class Execution(Model):
    """What running a plan did. ``ok`` means every step ran and ``ops`` are in the timeline;
    otherwise nothing was written, and ``failed_step`` and ``error`` say why."""

    request_id: str
    ok: bool
    steps: tuple[StepReport, ...] = Field(
        description="The steps that ran, in order, up to and including one that failed."
    )
    ops: tuple[Op, ...] = Field(default=(), description="The ops written, in log order.")
    failed_step: str | None = None
    error: str | None = Field(default=None, description="The toolbox's reason, for the user.")


class Timeline(Protocol):
    """A timeline the executor can copy and then write to. ``storage.Project`` fits."""

    def log(self) -> OpLog: ...

    def append_many(self, ops: Sequence[Op]) -> list[Op]: ...


def _scratch(timeline: Timeline | OpLog) -> OpLog:
    """A copy of the timeline's op log in memory, for the dry run."""
    return OpLog(timeline.ops) if isinstance(timeline, OpLog) else timeline.log()


def _ref_value(returns: Returns, result: Model) -> Any:
    """What a ``$ref`` to a step becomes: the part of its result the registry says it returns,
    as JSON, so resolved arguments can be reported and stored as they were passed."""
    if returns == "track_ids":
        return list(result.track_ids)  # type: ignore[attr-defined]
    if returns == "time_range":
        return result.time_range.model_dump(exclude_none=True)  # type: ignore[attr-defined]
    raise PlanError(f"a step that returns {returns!r} can't be the target of a $ref")


SCREEN_TIME_TOOLS = frozenset({"resolve_time"})
"""Tools whose time ranges are in on-screen time when a chat request runs ("the first 5
seconds" of what the user sees, cuts already taken out). ``find_speech`` isn't one: its words
come from the transcript, in source time."""

RANGE_ARGS = frozenset({"time_range"})
"""Arguments that hold a time range."""


@dataclass(frozen=True)
class ScreenTime:
    """On-screen (output) time for a chat request: the timeline as it was before the request.

    The contract keeps every op in source time, so a cut never shifts other edits. What a user
    types is what they see, with earlier cuts gone (docs/ARCHITECTURE.md, "The contract"): "mute
    the first 5 seconds" after trimming to 0:03-0:09 means source 0:03-0:08."""

    state: TimelineState
    source_end: float | None
    """Seconds of source video, or None when it isn't known."""

    @property
    def output_end(self) -> float | None:
        return None if self.source_end is None else self.state.source_to_output(self.source_end)

    def to_source(self, time_range: Mapping[str, Any]) -> dict[str, Any]:
        """``time_range`` from on-screen to source time. An end at or past the end of the output
        means "until the end". A start past it stays past the end of the source by as much, so
        the tool refuses it as it would any time past the end."""
        start, end = time_range["start"], time_range.get("end")
        return {"start": self._start(start), "end": None if end is None else self._end(end)}

    def _past_end(self, t: float) -> float | None:
        """How far ``t`` is past the end of the output, or None if it's before the end."""
        out = self.output_end
        return None if out is None or t < out - 1e-6 else t - out

    def _start(self, t: float) -> float:
        past = self._past_end(t)
        if past is not None and self.source_end is not None:
            return self.source_end + past
        return self.state.output_to_source(max(0.0, t))

    def _end(self, t: float) -> float | None:
        return None if self._past_end(t) is not None else self.state.output_to_source(max(0.0, t))


def _is_range(value: object) -> bool:
    return isinstance(value, Mapping) and "start" in value


def execute_plan(
    plan: Plan,
    timeline: Timeline | OpLog,
    *,
    request_id: str,
    source: Source = "agent",
    duration: float | None = None,
    tracks: Sequence[Track] | None = None,
    transcript: "Transcript | None" = None,
    describer: Describer | None = None,
    registry: Mapping[str, ToolSpec] = TOOLS,
    screen_time: bool | None = None,
) -> Execution:
    """Run ``plan`` and write its ops to ``timeline``, all of them or none.

    Every op carries ``request_id`` and ``source``. ``duration`` and ``tracks`` are what the
    tools know about the video (``ToolContext``): ``tracks=None`` means it was never analysed,
    so a step that looks for targets fails and nothing gets masked by guesswork. ``describer``
    looks at the video for ``find_by_description`` (F-13); without one that step fails too.

    ``screen_time`` (default: for the agent's requests, not for hand edits): the plan's time
    ranges are what the user sees, earlier cuts taken out. They're turned into source time
    before a tool writes anything (``ScreenTime``); the step reports keep the user's times.

    Raises ``PlanError`` for a plan the registry rejects (the planner validates before this, so
    that's a bug), and whatever the timeline raises when it can't be read or written.
    """
    errors = validate_plan(plan, registry)
    if errors:
        raise PlanError(f"can't run an invalid plan: {'; '.join(errors)}")

    scratch = _scratch(timeline)
    before = len(scratch)
    ctx = ToolContext(
        request_id=request_id,
        source=source,
        log=scratch,
        duration=duration,
        tracks=tracks,
        transcript=transcript,
        describer=describer,
    )
    screen = (
        ScreenTime(scratch.state(), duration)
        if (source == "agent" if screen_time is None else screen_time)
        else None
    )
    on_screen_steps: set[str] = set()
    steps: list[StepReport] = []
    values: dict[str, Any] = {}
    for call in plan.calls:
        args = {
            name: values[target] if (target := as_ref(value)) is not None else value
            for name, value in call.args.items()
        }
        tool_args, tool_ctx = args, ctx
        if screen is not None:
            # Ranges the user (or a time phrase) gave go to the tool in source time.
            tool_args = {
                name: screen.to_source(args[name])
                if name in RANGE_ARGS
                and _is_range(args[name])
                and ((target := as_ref(raw)) is None or target in on_screen_steps)
                else args[name]
                for name, raw in call.args.items()
            }
            if call.tool in SCREEN_TIME_TOOLS:
                tool_ctx = replace(ctx, duration=screen.output_end)
                on_screen_steps.add(call.step_id)
        try:
            result = call_tool(call.tool, tool_args, tool_ctx, registry)
        except ToolError as exc:
            steps.append(StepReport(step_id=call.step_id, tool=call.tool, args=args, ok=False))
            return Execution(
                request_id=request_id,
                ok=False,
                steps=tuple(steps),
                failed_step=call.step_id,
                error=str(exc),
            )
        returns = registry[call.tool].returns
        if returns != "none":
            values[call.step_id] = _ref_value(returns, result)
        steps.append(
            StepReport(
                step_id=call.step_id,
                tool=call.tool,
                args=args,
                result=result.model_dump(mode="json", exclude_none=True),
                ok=True,
            )
        )

    new_ops = scratch.ops[before:]
    if new_ops:  # one batch: the request's ops land together, right after each other
        timeline.append_many(new_ops)
    return Execution(request_id=request_id, ok=True, steps=tuple(steps), ops=new_ops)


def execute_request(
    storage: Storage,
    project: Project,
    plan: Plan,
    request_id: str,
    source: Source = "agent",
) -> Execution:
    """Run a chat request's plan on a project, with what's known about its video.

    The duration comes from probing the stored video. The tracks are the video's cached
    analysis plus the project's hand-drawn ones, or ``None`` if it was never analysed, so
    masking fails closed. The transcript is the cached one (F-31), or ``None`` if the video was
    never transcribed, so ``find_speech`` says to transcribe first. An analysis that's there but
    can't be used counts as none, and a step that needed it says so.
    """
    from vibe.modules import ModuleError
    from vibe.modules.transcribe import cached_transcript
    from vibe.modules.vision import CacheError, project_describer, project_tracks
    from vibe.video import probe

    duration = probe(storage.video_path(project.info.video)).duration
    unusable = False
    try:
        tracks = project_tracks(storage, project)
    except (CacheError, ValidationError) as exc:
        logger.warning("tracks for project %s can't be used: %s", project.id, exc)
        tracks, unusable = None, True

    try:
        transcript = cached_transcript(storage, project.info.video)
    except ModuleError as exc:  # a corrupt cache file: the log names it, the tool asks to redo
        logger.warning("transcript for project %s can't be used: %s", project.id, exc)
        transcript = None

    done = execute_plan(
        plan,
        project,
        request_id=request_id,
        source=source,
        duration=duration,
        tracks=tracks,
        transcript=transcript,
        # Built lazily: on a backend with no description judge yet, only a request that
        # describes someone ("the person in red") fails, and says why.
        describer=project_describer(storage, project),
    )
    if not done.ok and unusable and TOOLS[done.steps[-1].tool].returns == "track_ids":
        return done.model_copy(update={"error": UNREADABLE_ANALYSIS})
    return done
