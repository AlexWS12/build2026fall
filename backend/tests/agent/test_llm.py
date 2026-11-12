"""The LLM wire format (R-1): reply schema, prompt, reply parsing and ``SchemaPlanner``.

No network: providers are fakes. The schema rules are the subset every provider's structured
output accepts (docs/ARCHITECTURE.md D-016; Claude rejects numeric and length limits).
"""

import json
from collections.abc import Iterator, Mapping
from typing import Any

import pytest

from vibe.agent import Clarify, LlmError, Plan, plan_request, validate_plan
from vibe.agent import llm as llm_module
from vibe.agent.bench.candidates import CANDIDATES, Candidate
from vibe.agent.llm import (
    _EXAMPLES,
    _UNSUPPORTED,
    KNOWN_LABELS,
    PlannerConfigError,
    SchemaPlanner,
    parse_reply,
    planner_from_env,
    ref_schema,
    reply_schema,
    system_prompt,
)
from vibe.agent.providers import Provider, Reply, Usage
from vibe.toolbox import TOOLS


def schema_nodes(node: Any) -> Iterator[dict[str, Any]]:
    """Every schema object in ``node`` (property maps are walked by value, not as schemas)."""
    if isinstance(node, list):
        for item in node:
            yield from schema_nodes(item)
    elif isinstance(node, dict):
        yield node
        for key, value in node.items():
            if key == "properties":
                for prop in value.values():
                    yield from schema_nodes(prop)
            elif key in ("items", "anyOf"):
                yield from schema_nodes(value)


def call_variant(tool: str, schema: Mapping[str, Any]) -> dict[str, Any]:
    variants = schema["properties"]["calls"]["items"]["anyOf"]
    return next(v for v in variants if v["properties"]["tool"]["enum"] == [tool])


# --- Schema ---------------------------------------------------------------------------------


def test_schema_has_one_call_variant_per_tool() -> None:
    schema = reply_schema(TOOLS)

    variants = schema["properties"]["calls"]["items"]["anyOf"]
    assert [v["properties"]["tool"]["enum"] for v in variants] == [[name] for name in TOOLS]


def test_schema_uses_only_keywords_every_provider_accepts() -> None:
    for node in schema_nodes(reply_schema(TOOLS)):
        assert not _UNSUPPORTED & node.keys(), node
        assert "$defs" not in node
        assert "$ref" not in node  # inlined; "$ref" only appears as a property name
        assert "const" not in node


def test_every_object_is_closed_and_requires_all_its_properties() -> None:
    objects = [n for n in schema_nodes(reply_schema(TOOLS)) if n.get("type") == "object"]

    assert objects
    for node in objects:
        assert node["additionalProperties"] is False
        assert node["required"] == list(node["properties"])


def test_mask_targets_must_be_a_ref_and_times_may_be_one() -> None:
    args = call_variant("add_mask", reply_schema(TOOLS))["properties"]["args"]["properties"]

    assert args["targets"] == ref_schema()
    assert ref_schema() in args["time_range"]["anyOf"]
    assert args["time_range"]["anyOf"][-1] == {"type": "null"}  # optional: nullable


def test_labels_are_an_enum_and_renderer_settings_are_left_out() -> None:
    schema = reply_schema(TOOLS)
    label = call_variant("find_objects", schema)["properties"]["args"]["properties"]["label"]
    effect = call_variant("add_mask", schema)["properties"]["args"]["properties"]["effect"]

    assert label["enum"] == list(KNOWN_LABELS)
    assert effect["properties"].keys() == {"type"}
    assert effect["required"] == ["type"]


def test_denoise_keeps_its_own_strength() -> None:
    """Only the effect's renderer settings are hidden: denoise's strength is the user's."""
    args = call_variant("denoise", reply_schema(TOOLS))["properties"]["args"]["properties"]

    assert args.keys() == {"time_range", "strength"}


def test_ref_key_can_be_changed_on_the_wire() -> None:
    args = call_variant("add_mask", reply_schema(TOOLS, ref_key="ref"))["properties"]["args"]

    assert args["properties"]["targets"]["properties"].keys() == {"ref"}


# --- Reply parsing --------------------------------------------------------------------------


def test_a_question_comes_back_as_clarify_even_with_calls() -> None:
    text = json.dumps({"clarify": " Which person? ", "calls": [{"step_id": "s1"}]})

    assert parse_reply(text) == {"clarify": "Which person?"}


def test_nulls_are_dropped_so_tool_defaults_apply() -> None:
    text = json.dumps(
        {
            "clarify": None,
            "calls": [
                {
                    "step_id": "s1",
                    "tool": "mute",
                    "args": {"time_range": {"start": 90, "end": None}, "speaker": None},
                }
            ],
        }
    )

    reply = parse_reply(text)

    assert reply == {
        "calls": [{"step_id": "s1", "tool": "mute", "args": {"time_range": {"start": 90}}}]
    }
    assert validate_plan(Plan.model_validate(reply), TOOLS) == []


def test_an_empty_question_means_a_plan() -> None:
    assert parse_reply(json.dumps({"clarify": "", "calls": []})) == {"calls": []}


def test_the_wire_ref_key_is_rewritten_to_ref() -> None:
    text = json.dumps({"clarify": None, "calls": [{"args": {"targets": {"ref": "s1"}}}]})

    assert parse_reply(text, ref_key="ref") == {"calls": [{"args": {"targets": {"$ref": "s1"}}}]}


@pytest.mark.parametrize("text", ["not json", "[1, 2]", '{"calls": '])
def test_a_reply_that_isnt_a_json_object_fails_plan_validation(text: str) -> None:
    reply = parse_reply(text)

    with pytest.raises(ValueError):
        Plan.model_validate(reply)


# --- Prompt ---------------------------------------------------------------------------------


def test_prompt_is_the_same_bytes_every_time_so_it_caches() -> None:
    assert system_prompt(TOOLS) == system_prompt(dict(TOOLS))


def test_prompt_lists_every_tool_and_the_labels() -> None:
    prompt = system_prompt(TOOLS)

    for name in TOOLS:
        assert f"- {name}(" in prompt
    assert "face, person, license_plate" in prompt


def test_prompt_uses_the_wire_ref_key() -> None:
    prompt = system_prompt(TOOLS, ref_key="ref")

    assert '{"ref": "s1"}' in prompt
    assert '"$ref"' not in prompt


@pytest.mark.parametrize(("request_text", "reply"), _EXAMPLES, ids=[r for r, _ in _EXAMPLES])
def test_few_shot_examples_follow_the_rules_they_teach(
    request_text: str, reply: Mapping[str, Any]
) -> None:
    parsed = parse_reply(json.dumps(reply))

    if reply["clarify"]:
        assert parsed == {"clarify": reply["clarify"]}
        return
    assert validate_plan(Plan.model_validate(parsed), TOOLS) == []


# --- SchemaPlanner ----------------------------------------------------------------------------


class FakeProvider:
    name = "fake:model"
    model = "model"
    settings: Mapping[str, Any] = {}

    def __init__(self, *texts: str, stop_reason: str = "end_turn") -> None:
        self.texts = list(texts)
        self.stop_reason = stop_reason
        self.users: list[str] = []
        self.schemas: list[Mapping[str, Any]] = []

    def complete(self, system: str, user: str, schema: Mapping[str, Any]) -> Reply:
        self.users.append(user)
        self.schemas.append(schema)
        return Reply(self.texts.pop(0), Usage(input=10, output=5), 0.5, self.stop_reason)

    def describe(self) -> dict[str, Any]:
        return {}


MUTE_ALL = json.dumps(
    {"clarify": None, "calls": [{"step_id": "s1", "tool": "mute", "args": {"time_range": None}}]}
)
BAD = json.dumps({"clarify": None, "calls": [{"step_id": "s1", "tool": "explode", "args": {}}]})


def test_schema_planner_plans_what_the_templates_cant() -> None:
    planner = SchemaPlanner(FakeProvider(MUTE_ALL))

    result = plan_request("kill the audio", llm=planner)

    assert isinstance(result, Plan)
    assert [c.tool for c in result.calls] == ["mute"]
    assert len(planner.attempts) == 1


def test_schema_planner_passes_a_question_to_the_user() -> None:
    planner = SchemaPlanner(FakeProvider(json.dumps({"clarify": "Who?", "calls": []})))

    result = plan_request("blur her", llm=planner)

    assert result == Clarify(question="Who?", reason="LLM asked")


def test_retry_shows_the_llm_its_rejected_reply_and_the_error() -> None:
    provider = FakeProvider(BAD, MUTE_ALL)
    planner = SchemaPlanner(provider)

    result = plan_request("kill the audio", llm=planner)

    assert isinstance(result, Plan)
    assert provider.users[0] == "Request: kill the audio"
    assert BAD in provider.users[1]
    assert "explode" in provider.users[1]
    assert len(planner.attempts) == 2


def test_attempts_start_over_with_each_request() -> None:
    planner = SchemaPlanner(FakeProvider(BAD, MUTE_ALL, MUTE_ALL))

    plan_request("kill the audio", llm=planner)
    plan_request("kill the sound", llm=planner)

    assert len(planner.attempts) == 1


def test_a_refusal_is_a_provider_failure_not_a_plan() -> None:
    planner = SchemaPlanner(FakeProvider(MUTE_ALL, stop_reason="refusal"))

    with pytest.raises(LlmError):
        planner.plan("kill the audio", TOOLS, None)

    result = plan_request(
        "kill the audio", llm=SchemaPlanner(FakeProvider(MUTE_ALL, stop_reason="refusal"))
    )
    assert isinstance(result, Clarify)
    assert result.reason == "LLM call failed"


def test_schema_planner_sends_the_schema_for_the_registry_it_is_given() -> None:
    provider = FakeProvider(MUTE_ALL)
    only_mute = {"mute": TOOLS["mute"]}

    SchemaPlanner(provider).plan("kill the audio", only_mute, None)

    assert provider.schemas[0] == reply_schema(only_mute)


# --- The planner the app uses ---------------------------------------------------------------


@pytest.fixture
def fresh_providers() -> Iterator[None]:
    llm_module._provider.cache_clear()
    yield
    llm_module._provider.cache_clear()


def test_no_planner_llm_means_templates_only(
    monkeypatch: pytest.MonkeyPatch, fresh_providers: None
) -> None:
    monkeypatch.delenv("VIBE_PLANNER_LLM", raising=False)

    assert planner_from_env() is None


def test_an_unknown_planner_llm_is_refused(
    monkeypatch: pytest.MonkeyPatch, fresh_providers: None
) -> None:
    monkeypatch.setenv("VIBE_PLANNER_LLM", "gpt-17")

    with pytest.raises(PlannerConfigError, match="isn't a known model; try: groq-gpt-oss-120b"):
        planner_from_env()


@pytest.mark.parametrize(
    ("error", "says"),
    [
        (ImportError("No module named 'openai'"), "needs the llm dependency group"),
        (KeyError("GROQ_API_KEY"), "needs GROQ_API_KEY in the environment"),
        (ValueError("No API key was provided."), "can't be set up: No API key"),
    ],
)
def test_a_planner_llm_that_cant_be_built_says_why(
    monkeypatch: pytest.MonkeyPatch, fresh_providers: None, error: Exception, says: str
) -> None:
    def build() -> Provider:
        raise error

    monkeypatch.setitem(CANDIDATES, "broken", Candidate("broken", build, "free"))
    monkeypatch.setenv("VIBE_PLANNER_LLM", "broken")

    with pytest.raises(PlannerConfigError, match=says):
        planner_from_env()


def test_each_request_gets_its_own_planner_on_one_provider(
    monkeypatch: pytest.MonkeyPatch, fresh_providers: None
) -> None:
    built: list[object] = []

    def build() -> Provider:
        built.append(object())
        return built[-1]  # type: ignore[return-value]

    monkeypatch.setitem(CANDIDATES, "fake", Candidate("fake", build, "free"))
    monkeypatch.setenv("VIBE_PLANNER_LLM", "fake")

    first, second = planner_from_env(), planner_from_env()

    assert first is not second  # a SchemaPlanner keeps one request's attempts
    assert first is not None and second is not None
    assert first.provider is second.provider and len(built) == 1
