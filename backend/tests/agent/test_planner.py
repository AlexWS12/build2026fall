"""F-05 exit check: plain requests become valid tool calls or a clarify reply, offline, no GPU."""

import json
import socket
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from vibe.agent import Clarify, LlmError, Plan, PlanError, plan_request, validate_plan
from vibe.agent.tools_draft import DRAFT_REGISTRY, ToolSpec

REQUEST_SET = json.loads((Path(__file__).parent / "requests_draft.json").read_text())["requests"]


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("the planner tried to use the network")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.mark.usefixtures("no_network")
@pytest.mark.parametrize("case", REQUEST_SET, ids=[c["request"] for c in REQUEST_SET])
def test_request_set_gives_valid_plans_or_clarify(case: dict[str, Any]) -> None:
    result = plan_request(case["request"])

    if case["expect"] == "clarify":
        assert isinstance(result, Clarify), f"expected clarify, got {result!r}"
        assert result.question
        return
    assert isinstance(result, Plan), f"expected a plan, got {result!r}"
    assert validate_plan(result, DRAFT_REGISTRY) == []
    assert [c.model_dump() for c in result.calls] == case["calls"]


def test_request_set_covers_both_outcomes() -> None:
    outcomes = {c["expect"] for c in REQUEST_SET}

    assert outcomes == {"plan", "clarify"}
    assert len(REQUEST_SET) >= 20


@pytest.mark.parametrize(
    ("text", "hint"),
    [("cut it", "0:10 to 0:20"), ("mute 0:20 to 0:10", "before")],
)
def test_clarify_question_tells_the_user_what_to_say(text: str, hint: str) -> None:
    result = plan_request(text)

    assert isinstance(result, Clarify)
    assert hint in result.question


# --- LLM slot (R-1 plugs a real model in here) --------------------------------------------

GOOD_PLAN = {"calls": [{"step_id": "s1", "tool": "mute", "args": {}}]}
BAD_PLAN = {"calls": [{"step_id": "s1", "tool": "explode", "args": {}}]}


class FakeLlm:
    """Returns canned plans in order and records the error it was shown each time."""

    def __init__(self, *replies: Mapping[str, Any]) -> None:
        self.replies = list(replies)
        self.errors_seen: list[str | None] = []

    def plan(
        self, request: str, tools: Mapping[str, ToolSpec], error: str | None
    ) -> Mapping[str, Any]:
        self.errors_seen.append(error)
        return self.replies.pop(0)


def test_plain_requests_never_reach_the_llm() -> None:
    llm = FakeLlm()

    result = plan_request("blur faces", llm=llm)

    assert isinstance(result, Plan)
    assert llm.errors_seen == []


def test_llm_plan_is_used_when_no_template_matches() -> None:
    llm = FakeLlm(GOOD_PLAN)

    result = plan_request("make him quieter", llm=llm)

    assert isinstance(result, Plan)
    assert llm.errors_seen == [None]


def test_invalid_llm_plan_is_retried_once_with_the_error() -> None:
    llm = FakeLlm(BAD_PLAN, GOOD_PLAN)

    result = plan_request("make him quieter", llm=llm)

    assert isinstance(result, Plan)
    assert llm.errors_seen[0] is None
    assert "explode" in (llm.errors_seen[1] or "")


@pytest.mark.parametrize("second", [BAD_PLAN, {"not": "a plan"}])
def test_two_invalid_llm_plans_give_clarify(second: Mapping[str, Any]) -> None:
    llm = FakeLlm(BAD_PLAN, second)

    result = plan_request("make him quieter", llm=llm)

    assert isinstance(result, Clarify)
    assert len(llm.errors_seen) == 2


class FailingLlm:
    def plan(
        self, request: str, tools: Mapping[str, ToolSpec], error: str | None
    ) -> Mapping[str, Any]:
        raise LlmError("provider is down")


def test_llm_error_gives_clarify_and_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    result = plan_request("make him quieter", llm=FailingLlm())

    assert isinstance(result, Clarify)
    assert result.reason == "LLM call failed"
    assert "provider is down" in caplog.text


def test_clarify_reason_keeps_both_llm_failures() -> None:
    result = plan_request("make him quieter", llm=FakeLlm(BAD_PLAN, {"not": "a plan"}))

    assert isinstance(result, Clarify)
    assert "explode" in result.reason
    assert "not a valid plan" in result.reason


class BuggyLlm:
    def plan(
        self, request: str, tools: Mapping[str, ToolSpec], error: str | None
    ) -> Mapping[str, Any]:
        raise KeyError("bug in the adapter")


def test_a_bug_in_the_llm_adapter_is_not_hidden_as_try_again() -> None:
    with pytest.raises(KeyError):
        plan_request("make him quieter", llm=BuggyLlm())


def test_only_part_timed_has_its_own_clarify_reason() -> None:
    result = plan_request("blur faces and mute 0:10 to 0:20")

    assert isinstance(result, Clarify)
    assert result.reason == "only part of the request has a time"


def test_unusable_time_keeps_the_time_reason() -> None:
    result = plan_request("cut 10 to 20")

    assert isinstance(result, Clarify)
    assert result.reason == "time range can't be used"
    assert "seconds or minutes" in result.question


NO_RESOLVE_TIME = {name: spec for name, spec in DRAFT_REGISTRY.items() if name != "resolve_time"}
"""Like a real registry (F-06) that dropped or renamed a tool the templates use."""


def test_template_plan_rejected_by_another_registry_asks_instead_of_crashing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    result = plan_request("blur faces for the first half", registry=NO_RESOLVE_TIME)

    assert isinstance(result, Clarify)
    assert "resolve_time" in caplog.text


def test_template_plan_rejected_by_another_registry_goes_to_the_llm() -> None:
    llm = FakeLlm(GOOD_PLAN)

    result = plan_request("blur faces for the first half", registry=NO_RESOLVE_TIME, llm=llm)

    assert isinstance(result, Plan)
    assert llm.errors_seen == [None]


def test_template_plan_rejected_by_the_draft_registry_is_a_parser_bug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("vibe.agent.planner.validate_plan", lambda plan, registry: ["broken"])

    with pytest.raises(PlanError):
        plan_request("blur faces")
