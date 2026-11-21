"""The shapes and arithmetic of a preview before apply (F-44). The routes are in ``projects``.

A preview is a dry run of a plan on the project's timeline: the ops it would write are folded
onto the current ones to give the state the player shows, and the saved video's length with
and without the cuts says what the edit does.
"""

from collections.abc import Sequence

from pydantic import Field

from vibe.agent import StepReport
from vibe.contract import Op, OpLog, TimelineState, fold
from vibe.contract.common import Model, TimeRange


class PreviewSummary(Model):
    """What a proposed edit does to the video's length."""

    duration_before: float = Field(description="Seconds the export lasts now.")
    duration_after: float = Field(description="Seconds it would last if the edit is accepted.")
    removed: list[TimeRange] = Field(description="The cuts the edit would add, in source time.")


class PreviewReply(Model):
    """A proposed edit waiting for accept or reject (``GET /api/projects/{id}/preview``)."""

    request_id: str
    text: str = Field(description="What the user asked.")
    reply: str = Field(description="What the edit would do, in plain words.")
    steps: list[StepReport]
    state: TimelineState = Field(description="The timeline as it would be if accepted.")
    preview: PreviewSummary
    problem: str | None = Field(
        default=None,
        description=(
            "Set when the edit no longer applies to the timeline as it is now: why. The state "
            "is then the current one, and the edit can only be discarded."
        ),
    )


def exported_seconds(state: TimelineState, duration: float) -> float:
    """How long the export is: the source without its cuts (cuts in a state never overlap)."""
    removed = sum(max(0.0, min(cut.end or duration, duration) - cut.start) for cut in state.cuts)
    return max(0.0, duration - removed)


def candidate_state(log: OpLog, ops: Sequence[Op]) -> TimelineState:
    """The state after ``ops`` land on ``log``, without writing them."""
    return fold([*log.ops, *ops])


def _ends(cut: TimeRange, duration: float) -> float:
    return duration if cut.end is None else min(cut.end, duration)


def new_footage(before: TimelineState, after: TimelineState, duration: float) -> list[TimeRange]:
    """The stretches ``after`` cuts that ``before`` didn't: a cut that grows an existing one
    reports only the added part, not the whole merged range."""
    covered = sorted((cut.start, _ends(cut, duration)) for cut in before.cuts)
    added: list[TimeRange] = []
    for cut in after.cuts:
        start, end = cut.start, _ends(cut, duration)
        for cut_start, cut_end in covered:
            if cut_end <= start or cut_start >= end:
                continue
            if cut_start > start:
                added.append(TimeRange(start=start, end=cut_start))
            start = max(start, cut_end)
        if start < end:
            added.append(TimeRange(start=start, end=end))
    return added


def summarise(before: TimelineState, after: TimelineState, duration: float) -> PreviewSummary:
    return PreviewSummary(
        duration_before=exported_seconds(before, duration),
        duration_after=exported_seconds(after, duration),
        removed=new_footage(before, after, duration),
    )
