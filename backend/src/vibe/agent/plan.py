"""What the planner returns: a plan of tool calls, or a question for the user.

A plan is JSON-friendly on purpose: the LLM planner (after R-1) writes the same shape. An
argument whose value is ``{"$ref": "s1"}`` means "the result of step s1", e.g. the track ids
``find_objects`` found. Refs are only allowed as whole argument values.
"""

from collections.abc import Iterable
from typing import Any

from pydantic import Field

from vibe.contract.common import Model

REF_KEY = "$ref"


class ToolCall(Model):
    step_id: str = Field(min_length=1, description='Unique in the plan, e.g. "s1".')
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)


class Plan(Model):
    calls: tuple[ToolCall, ...]


class Clarify(Model):
    """Ask the user instead of guessing (docs/ARCHITECTURE.md, "Agent / planner")."""

    question: str = Field(description="Shown to the user.")
    reason: str = Field(description="Why the planner asked, for logs and the benchmark.")


PlannerResult = Plan | Clarify


def ref(step_id: str) -> dict[str, str]:
    return {REF_KEY: step_id}


def as_ref(value: Any) -> str | None:
    """The step id if ``value`` is a ``{"$ref": ...}``, else None."""
    if isinstance(value, dict) and value.keys() == {REF_KEY} and isinstance(value[REF_KEY], str):
        return value[REF_KEY]
    return None


TOO_MANY_KEEPS = (
    "more than one keep_range call: each keeps only its own range, so only their overlap "
    "would be left"
)


def too_many_keeps(calls: Iterable[ToolCall]) -> bool:
    """A plan may keep one range at most: each keep cuts what the other keeps.

    Shared by the template parser (which declines such a request) and ``validate_plan`` (which
    refuses such a plan), so the two can't drift apart.
    """
    return sum(call.tool == "keep_range" for call in calls) > 1
