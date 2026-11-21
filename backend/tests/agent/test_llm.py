"""The Claude planner (R-1b, D-023): a fake client stands in for the API, so nothing calls out."""

import json
from types import SimpleNamespace
from typing import Any

import pytest

from vibe import config
from vibe.agent import Clarify, Plan, plan_request
from vibe.agent.llm import ClaudePlanner, output_schema, to_plan, tool_catalogue
from vibe.toolbox import TOOLS


def call(step: str, tool: str, args: dict[str, Any]) -> dict[str, str]:
    return {"step_id": step, "tool": tool, "args_json": json.dumps(args)}


FIRST_HALF_FACES = {
    "calls": [
        call("s1", "find_objects", {"label": "face"}),
        call("s2", "resolve_time", {"phrase": "first half"}),
        call(
            "s3",
            "add_mask",
            {
                "targets": {"$ref": "s1"},
                "effect": {"type": "blur"},
                "time_range": {"$ref": "s2"},
                "while_tracked": True,
            },
        ),
    ],
    "question": "",
}


class FakeClient:
    """Answers each request with the next of ``answers`` (dicts as JSON, or a raw response)."""

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.requests: list[dict[str, Any]] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, SimpleNamespace):
            return answer
        text = SimpleNamespace(type="text", text=json.dumps(answer))
        return SimpleNamespace(stop_reason="end_turn", content=[text])


def planner(*answers: Any) -> tuple[ClaudePlanner, FakeClient]:
    client = FakeClient(*answers)
    return ClaudePlanner(client=client), client


def test_a_request_no_template_reads_is_planned_by_claude() -> None:
    llm, client = planner(FIRST_HALF_FACES)

    result = plan_request("hide everyone's face in the first part", llm=llm)

    assert isinstance(result, Plan)
    assert [c.tool for c in result.calls] == ["find_objects", "resolve_time", "add_mask"]
    assert result.calls[2].args["targets"] == {"$ref": "s1"}
    (sent,) = client.requests
    assert sent["messages"] == [
        {"role": "user", "content": "Request: hide everyone's face in the first part"}
    ]
    assert sent["model"] == "claude-opus-5-5"
    assert sent["output_config"]["effort"] == "low"
    assert sent["output_config"]["format"]["schema"] == output_schema(TOOLS)


def test_known_commands_never_reach_claude() -> None:
    llm, client = planner()
    assert isinstance(plan_request("blur faces", llm=llm), Plan)
    assert client.requests == []


def test_claude_can_ask_instead_of_planning() -> None:
    question = "I can't pick someone by position. I can blur faces, people or plates."
    llm, _ = planner({"calls": [], "question": question})

    result = plan_request("blur the person in the middle", llm=llm)

    assert isinstance(result, Clarify)
    assert result.question == question


def test_an_invalid_plan_is_retried_with_the_error() -> None:
    made_up = {"calls": [call("s1", "add_mask", {"targets": ["trk_1"]})], "question": ""}
    llm, client = planner(made_up, FIRST_HALF_FACES)

    result = plan_request("hide everyone's face in the first part", llm=llm)

    assert isinstance(result, Plan)
    assert len(client.requests) == 2
    assert "previous plan was rejected" in client.requests[1]["messages"][0]["content"]


def test_arguments_that_arent_json_are_retried_not_guessed() -> None:
    broken = {"calls": [{"step_id": "s1", "tool": "undo", "args_json": "{not json"}]}
    llm, client = planner(
        {**broken, "question": ""}, {"calls": [call("s1", "undo", {})], "question": ""}
    )

    result = plan_request("take that back please", llm=llm)

    assert isinstance(result, Plan) and result.calls[0].tool == "undo"
    assert len(client.requests) == 2


@pytest.mark.parametrize(
    "answer",
    [
        SimpleNamespace(stop_reason="refusal", content=[]),
        SimpleNamespace(stop_reason="max_tokens", content=[]),
        SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="no")]),
    ],
)
def test_a_declined_or_broken_answer_asks_the_user_to_retry(answer: Any) -> None:
    llm, _ = planner(answer)
    result = plan_request("hide everyone's face in the first part", llm=llm)
    assert isinstance(result, Clarify) and "couldn't plan that right now" in result.question


def test_an_api_outage_asks_the_user_to_retry() -> None:
    import anthropic

    outage = anthropic.APIConnectionError.__new__(anthropic.APIConnectionError)
    Exception.__init__(outage, "connection refused")
    llm, _ = planner(outage)

    result = plan_request("hide everyone's face in the first part", llm=llm)

    assert isinstance(result, Clarify) and "couldn't plan that right now" in result.question


def test_a_bug_in_the_client_is_not_hidden() -> None:
    llm, _ = planner(KeyError("bug"))
    with pytest.raises(KeyError):
        plan_request("hide everyone's face in the first part", llm=llm)


def test_the_prompt_lists_every_tool_from_the_registry() -> None:
    catalogue = tool_catalogue(TOOLS)
    for name in TOOLS:
        assert f'"tool": "{name}"' in catalogue
    assert "remove_mask" not in catalogue  # hand tools never reach the planner


def test_to_plan_keeps_the_question_only_when_there_are_no_calls() -> None:
    assert to_plan({"calls": [], "question": "Which part?"}) == {"question": "Which part?"}
    plan = to_plan({"calls": [call("s1", "undo", {})], "question": "ignored"})
    assert plan == {"calls": [{"step_id": "s1", "tool": "undo", "args": {}}]}


@pytest.mark.parametrize(
    ("value", "expected"), [("", None), ("off", None), ("Claude", "claude"), (" claude ", "claude")]
)
def test_the_planner_llm_setting(
    monkeypatch: pytest.MonkeyPatch, value: str, expected: str | None
) -> None:
    monkeypatch.setenv("VIBE_PLANNER_LLM", value)
    assert config.planner_llm() == expected


def test_an_unknown_planner_llm_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VIBE_PLANNER_LLM", "gpt")
    with pytest.raises(ValueError, match="VIBE_PLANNER_LLM"):
        config.planner_llm()
