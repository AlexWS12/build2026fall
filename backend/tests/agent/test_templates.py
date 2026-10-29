"""F-05: the template parser turns plain requests into plans, and declines everything else."""

import pytest

from vibe.agent.templates import parse
from vibe.agent.timeparse import TimeParseError


def calls(text: str) -> list[dict]:
    plan = parse(text)
    assert plan is not None, f"no template matched {text!r}"
    return [c.model_dump() for c in plan.calls]


@pytest.mark.parametrize(
    ("verb", "effect"),
    [("blur", "blur"), ("pixelate", "pixelate"), ("black box", "box"), ("hide", "blur")],
)
def test_mask_verbs_map_to_effects(verb: str, effect: str) -> None:
    result = calls(f"{verb} faces")

    assert result[-1]["args"]["effect"] == {"type": effect}


@pytest.mark.parametrize(
    ("noun", "label"),
    [
        ("faces", "face"),
        ("people", "person"),
        ("everyone", "person"),
        ("license plates", "license_plate"),
        ("plates", "license_plate"),
        ("face", "face"),
        ("person", "person"),
        ("license plate", "license_plate"),
    ],
)
def test_mask_nouns_map_to_detector_labels(noun: str, label: str) -> None:
    result = calls(f"blur all the {noun}" if noun != "everyone" else "blur everyone")

    assert result[0] == {"step_id": "s1", "tool": "find_objects", "args": {"label": label}}


def test_duration_relative_time_adds_a_resolve_time_step() -> None:
    result = calls("cut the last 10 seconds")

    assert [c["tool"] for c in result] == ["resolve_time", "cut_range"]
    assert result[1]["args"] == {"time_range": {"$ref": "s1"}}


def test_mute_without_a_time_mutes_the_whole_clip() -> None:
    assert calls("mute") == [{"step_id": "s1", "tool": "mute", "args": {}}]


def test_step_ids_are_numbered_across_clauses() -> None:
    result = calls("mute 0:01 to 0:02 and then blur faces for the whole video")

    assert [c["step_id"] for c in result] == ["s1", "s2", "s3"]
    assert result[2]["args"]["targets"] == {"$ref": "s2"}


def test_and_inside_a_between_range_is_not_a_clause_split() -> None:
    assert calls("mute between 0:10 and 0:20")[0]["args"] == {
        "time_range": {"start": 10.0, "end": 20.0}
    }


@pytest.mark.parametrize(
    "text",
    [
        "cut it",
        "cut",
        "blur him",
        "blur the guy in red",
        "blur faces except mine",
        "make it nicer",
        "remove the people in the background",
        "blur faces and make it nicer",
        "trim 0:10 to 0:20",
        "mute 0:10 and 0:20",
        "blur the person in red",
        "",
    ],
)
def test_requests_that_are_not_plain_give_none(text: str) -> None:
    assert parse(text) is None


def test_backwards_range_raises_so_the_planner_can_ask() -> None:
    with pytest.raises(TimeParseError):
        parse("mute 0:20 to 0:10")


@pytest.mark.parametrize(
    "text",
    [
        "mute and cut 0:10 to 0:20",
        "cut 0:10 to 0:20 and mute",
        "blur faces and mute 0:10 to 0:20",
        "cut the first 3 seconds, then blur faces",
    ],
)
def test_a_clause_without_a_time_next_to_one_with_a_time_raises_so_the_planner_can_ask(
    text: str,
) -> None:
    with pytest.raises(TimeParseError):
        parse(text)


@pytest.mark.parametrize(
    "text",
    [
        "blur faces and mute the audio",
        "cut the first 3 seconds, then blur faces for the whole video",
    ],
)
def test_clauses_that_cant_be_misread_are_planned(text: str) -> None:
    assert parse(text) is not None


def test_whole_video_means_no_time_range_argument() -> None:
    assert calls("mute for the whole video") == [{"step_id": "s1", "tool": "mute", "args": {}}]


def test_cutting_the_whole_video_is_not_a_plain_request() -> None:
    assert parse("cut the whole video") is None


@pytest.mark.parametrize(
    "text",
    [
        "can you please blur faces",
        "please blur faces",
        "blur faces, please",
        "Could you blur faces please?",
        "blur faces. Thanks!",
    ],
)
def test_polite_words_around_a_request_are_ignored(text: str) -> None:
    assert calls(text) == calls("blur faces")
