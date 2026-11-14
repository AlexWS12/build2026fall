"""F-13: the template parser plans people picked by a description ("the person in red")."""

import pytest

from vibe.agent.templates import parse
from vibe.agent.timeparse import TimeParseError


def calls(text: str) -> list[dict]:
    plan = parse(text)
    assert plan is not None, f"no template matched {text!r}"
    return [c.model_dump() for c in plan.calls]


@pytest.mark.parametrize(
    ("text", "args"),
    [
        ("blur the person in red", {"label": "person", "description": "in red"}),
        ("blur people wearing a hat", {"label": "person", "description": "wearing a hat"}),
        (
            "pixelate everyone in a blue shirt",
            {"label": "person", "description": "in a blue shirt"},
        ),
        (
            "blur everyone except the person in red",
            {"label": "person", "description": "in red", "exclude": True},
        ),
        (
            "blur everybody but the one wearing a hat",
            {"label": "person", "description": "wearing a hat", "exclude": True},
        ),
    ],
)
def test_a_described_person_goes_through_find_by_description(text: str, args: dict) -> None:
    result = calls(text)

    assert result[0] == {"step_id": "s1", "tool": "find_by_description", "args": args}
    assert result[1]["tool"] == "add_mask"
    assert result[1]["args"]["targets"] == {"$ref": "s1"}
    assert "time_range" not in result[1]["args"]


def test_a_description_can_be_followed_by_a_time() -> None:
    result = calls("blur the person in red from 0:10 to 0:20")

    assert result[0]["args"] == {"label": "person", "description": "in red"}
    assert result[1]["args"]["time_range"] == {"start": 10.0, "end": 20.0}


def test_a_description_with_a_duration_relative_time_adds_resolve_time() -> None:
    result = calls("blur everyone except the person in red for the first half")

    assert [c["tool"] for c in result] == ["find_by_description", "resolve_time", "add_mask"]
    assert result[0]["args"]["description"] == "in red"
    assert result[2]["args"]["time_range"] == {"$ref": "s2"}


def test_a_time_alone_is_still_not_a_description() -> None:
    result = calls("blur the person in the first half")

    assert result[0] == {"step_id": "s1", "tool": "find_objects", "args": {"label": "person"}}


def test_a_backwards_time_after_a_description_is_asked_about() -> None:
    with pytest.raises(TimeParseError):
        parse("blur the person in red from 0:20 to 0:10")


def test_a_described_clause_joins_others() -> None:
    result = calls("blur the person in red and mute the audio")

    assert [c["tool"] for c in result] == ["find_by_description", "add_mask", "mute"]
