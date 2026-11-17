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
    # One described person is followed; "everyone except" hides bystanders for the whole range.
    assert result[1]["args"].get("while_tracked", False) is not args.get("exclude", False)


@pytest.mark.parametrize(
    "description",
    [
        "in a grey t-shirt with a bag",
        "in a grey t-shirt and carrying a backpack",
        "wearing a red jacket, with a dog",
    ],
)
def test_several_criteria_stay_one_description(description: str) -> None:
    result = calls(f"blur the person {description}")

    assert result[0]["args"] == {"label": "person", "description": description}
    assert [c["tool"] for c in result] == ["find_by_description", "add_mask"]


@pytest.mark.parametrize(
    "ending",
    [
        ", only if sure",
        " only if you're sure",
        ", but only the sure ones",
        " sure matches only",
        ", skip the unsure ones",
    ],
)
def test_asking_for_sure_matches_only_is_explicit(ending: str) -> None:
    result = calls(f"blur the person in a grey t-shirt with a bag{ending}")

    assert result[0]["args"] == {
        "label": "person",
        "description": "in a grey t-shirt with a bag",
        "unsure": "leave",
    }


@pytest.mark.parametrize(
    "text",
    [
        "blur the person in red from 0:10 to 0:20, only if sure",
        # Before the time (review of PR 33): "only if sure" used to stay in the description,
        # so the judge was unsure about everyone and everyone in the range was blurred.
        "blur the person in red only if sure from 0:10 to 0:20",
        "blur the person in red, only if you're sure, from 0:10 to 0:20",
        "blur the person in red sure matches only between 0:10 and 0:20",
    ],
)
def test_sure_matches_only_can_come_before_or_after_a_time(text: str) -> None:
    result = calls(text)

    assert result[0]["args"] == {"label": "person", "description": "in red", "unsure": "leave"}
    assert result[1]["args"]["time_range"] == {"start": 10.0, "end": 20.0}


def test_sure_matches_only_with_a_duration_relative_time() -> None:
    result = calls("blur the person in red only if sure for the first half")

    assert result[0]["args"] == {"label": "person", "description": "in red", "unsure": "leave"}
    assert [c["tool"] for c in result] == ["find_by_description", "resolve_time", "add_mask"]


def test_only_alone_is_not_giving_up_on_unsure() -> None:
    assert parse("blur only the person in red") is None


@pytest.mark.parametrize(
    "text",
    [
        # a negated time would mask the opposite of what was asked
        "blur the person in red except from 0:10 to 0:20",
        "blur the person in red but not from 0:10 to 0:20",
        # two times: the first one used to be swallowed by the description
        "blur the person in red from 0:10 to 0:20 and from 0:30 to 0:40",
        # two targets in one description
        "blur the person in red and the person in blue",
        "blur the person in red and faces",
        # identity, not a description (F-52)
        "blur everyone except the person with me",
        "blur the person with my bag",
        # a place, a time or the effect, not what someone looks like
        "blur the person in 0:10",
        "blur the people in the video",
        "blur the people in the background",
        "blur the person in red on the left",
        "blur everyone with a strong blur",
        "pixelate people with a heavy pixelation",
        "box everyone in black",
        "black box everyone in black",
        # sure of what?
        "blur everyone except the person in red, only if sure",
        "blur everyone except the person in red only if sure from 0:10 to 0:20",
        # a "sure" phrase in a place the parser doesn't read: never part of a description
        "blur the person if sure in red",
        "blur the person in red if you are certain",
        "blur the person in red and unsure ones too",
    ],
)
def test_what_is_not_a_plain_description_is_left_to_the_planner(text: str) -> None:
    assert parse(text) is None


def test_people_in_black_can_still_be_blurred() -> None:
    assert calls("blur everyone in black")[0]["args"]["description"] == "in black"


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
