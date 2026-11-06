"""F-21: "mute / cut where he says X" plans a ``find_speech`` step and edits its time range."""

import pytest

from vibe.agent import Plan, plan_request
from vibe.agent.templates import parse

FIND = {"$ref": "s1"}


@pytest.mark.parametrize(
    ("text", "said", "edit"),
    [
        ("mute where he says hola", "hola", "mute"),
        ("silence when they say 'hello world'", "hello world", "mute"),
        ("mute the word hola", "hola", "mute"),
        ("cut the part where she said hello world", "hello world", "cut_range"),
        ("please cut where I say um.", "um", "cut_range"),
    ],
)
def test_spoken_words_become_find_speech_then_the_edit(text: str, said: str, edit: str) -> None:
    plan = plan_request(text)  # also checks the plan against the real toolbox registry

    assert isinstance(plan, Plan)
    assert [c.model_dump() for c in plan.calls] == [
        {"step_id": "s1", "tool": "find_speech", "args": {"text": said}},
        {"step_id": "s2", "tool": edit, "args": {"time_range": FIND}},
    ]


def test_it_joins_other_timed_clauses() -> None:
    plan = parse("mute where he says hola and cut 0:10 to 0:20")

    assert plan is not None
    assert [c.tool for c in plan.calls] == ["find_speech", "mute", "cut_range"]


@pytest.mark.parametrize(
    "text", ["cut where she talks about the weather", "mute where he says", 'mute the word ""']
)
def test_topics_and_missing_words_are_not_templated(text: str) -> None:
    assert parse(text) is None
