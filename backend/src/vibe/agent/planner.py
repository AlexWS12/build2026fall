"""Entry point: chat request -> plan of tool calls, or a question for the user.

Plain requests go through the template parser (instant, offline). Anything else goes to the
LLM slot, which R-1's chosen model fills; until then the planner asks the user. An invalid LLM
plan is retried once with the error, then the user is asked. It never guesses silently.
"""

import logging
from collections.abc import Mapping
from typing import Any, Protocol

from pydantic import ValidationError

from vibe.agent.plan import Clarify, Plan, PlannerResult
from vibe.agent.templates import normalize, parse
from vibe.agent.timeparse import TimeParseError
from vibe.agent.tools_draft import DRAFT_REGISTRY, ToolSpec
from vibe.agent.validate import validate_plan

logger = logging.getLogger(__name__)

LLM_ATTEMPTS = 2
"""The first plan plus one retry with the validation error."""

EXAMPLES = "'blur faces', 'mute 0:10 to 0:20' or 'cut the first 5 seconds'"


class PlanError(RuntimeError):
    """The template parser built a plan the registry rejects: a bug in the parser."""


class LlmPlanner(Protocol):
    """Writes a whole plan as JSON in one call. ``error`` is set on the retry."""

    def plan(
        self, request: str, tools: Mapping[str, ToolSpec], error: str | None
    ) -> Mapping[str, Any]: ...


def plan_request(
    text: str,
    registry: Mapping[str, ToolSpec] = DRAFT_REGISTRY,
    llm: LlmPlanner | None = None,
) -> PlannerResult:
    try:
        plan = parse(text)
    except TimeParseError as exc:
        return Clarify(question=str(exc), reason="time range can't be used")
    if plan is not None:
        errors = validate_plan(plan, registry)
        if errors:
            raise PlanError(f"template plan for {text!r} is invalid: {errors}")
        return plan
    if llm is None:
        return Clarify(question=_question_for(text), reason="no template matched")
    return _plan_with_llm(text, registry, llm)


def _plan_with_llm(text: str, registry: Mapping[str, ToolSpec], llm: LlmPlanner) -> PlannerResult:
    failures: list[str] = []
    for _ in range(LLM_ATTEMPTS):
        try:
            raw = llm.plan(text, registry, failures[-1] if failures else None)
        except Exception:  # a provider or network error: tell the user, keep the details
            logger.exception("LLM planner failed on %r", text)
            return Clarify(
                question="I couldn't plan that right now. Try again, or try something like "
                f"{EXAMPLES}.",
                reason="LLM call failed",
            )
        try:
            plan = Plan.model_validate(raw)
        except ValidationError as exc:
            failures.append(f"not a valid plan: {exc}")
            continue
        errors = validate_plan(plan, registry)
        if not errors:
            return plan
        failures.append("; ".join(errors))
    logger.warning("LLM plan for %r invalid twice: %s", text, failures)
    return Clarify(
        question=_question_for(text),
        reason="LLM plan invalid twice: " + " | ".join(failures),
    )


def _question_for(text: str) -> str:
    """A question that tells the user what the planner needs to go ahead."""
    words = normalize(text).split()
    verb = words[0] if words else ""
    if verb == "cut":
        return "Which part should I cut? Give a time range like 0:10 to 0:20."
    if verb in ("mute", "silence"):
        return "What should I mute? Give a time range like 0:10 to 0:20, or say 'mute the audio'."
    if verb in ("blur", "pixelate", "hide"):
        return (
            "Who or what should I hide? Right now I can do faces, people or license plates, "
            "optionally with a time range like 0:10 to 0:20."
        )
    return f"I couldn't turn that into an edit yet. Try something like {EXAMPLES}."
