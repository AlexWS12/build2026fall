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


@pytest.mark.parametrize("text", ["mute the audio", "silence the sound", "mute the video"])
def test_mute_the_audio_mutes_the_whole_clip(text: str) -> None:
    assert calls(text) == [{"step_id": "s1", "tool": "mute", "args": {}}]


@pytest.mark.parametrize("text", ["mute it", "silence it", "mute it from 0:10 to 0:20"])
def test_mute_it_is_not_guessed_as_the_whole_clip(text: str) -> None:
    # "it" may be a person or a sound the user means, so it's asked about or goes to the LLM.
    assert parse(text) is None


@pytest.mark.parametrize(
    ("text", "args"),
    [
        ("unmute", {}),
        ("unmute the audio", {}),
        ("unmute 0:10 to 0:20", {"time_range": {"start": 10, "end": 20}}),
        ("restore the sound after 0:30", {"time_range": {"start": 30}}),
    ],
)
def test_unmute_requests(text: str, args: dict) -> None:
    assert calls(text) == [{"step_id": "s1", "tool": "unmute", "args": args}]


def test_mute_then_unmute_part_of_it() -> None:
    result = calls("mute 0:01 to 0:05 and unmute 0:02 to 0:03")
    assert [c["tool"] for c in result] == ["mute", "unmute"]


@pytest.mark.parametrize("text", ["unmute him", "restore the cut part", "restore it"])
def test_unclear_unmute_requests_are_declined(text: str) -> None:
    assert parse(text) is None


@pytest.mark.parametrize(
    "text",
    [
        "remove the background noise",
        "reduce the noise",
        "cut the background hiss",
        "remove the hum from the audio",
        "remove the background noises",
        "reduce the hums",
        "cut the hisses",
        "denoise",
        "denoise the audio",
    ],
)
def test_noise_requests_denoise_the_whole_clip_instead_of_cutting(text: str) -> None:
    assert calls(text) == [{"step_id": "s1", "tool": "denoise", "args": {}}]


@pytest.mark.parametrize(
    "text",
    ["cut the noise from 0:10 to 0:20", "delete the background hiss in the first half"],
)
def test_cutting_noise_over_a_range_is_asked_not_guessed(text: str) -> None:
    assert parse(text) is None  # cut that part out, or filter it?


def test_removing_noise_over_a_range_still_denoises() -> None:
    assert [c["tool"] for c in calls("remove the noise from 0:10 to 0:20")] == ["denoise"]


def test_denoise_with_a_time_filters_only_that_range() -> None:
    assert calls("remove the background noise from 0:10 to 0:20")[-1]["args"] == {
        "time_range": {"start": 10.0, "end": 20.0}
    }
    result = calls("reduce the noise in the first half")
    assert [c["tool"] for c in result] == ["resolve_time", "denoise"]


def test_denoise_is_its_own_clause() -> None:
    result = calls("mute 0:01 to 0:02 and reduce the noise after 0:30")

    assert [c["tool"] for c in result] == ["mute", "denoise"]


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


# --- keep (F-20) ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "keep 0:10 to 0:20",
        "keep only 0:10 to 0:20",
        "keep just 0:10 to 0:20",
        "only keep 0:10 to 0:20",
        "please keep only 0:10 to 0:20",
        "keep from 0:10 to 0:20",
    ],
)
def test_keep_a_time_range_plans_keep_range(text: str) -> None:
    assert calls(text) == [
        {
            "step_id": "s1",
            "tool": "keep_range",
            "args": {"time_range": {"start": 10.0, "end": 20.0}},
        }
    ]


@pytest.mark.parametrize(
    ("text", "phrase"),
    [
        ("keep the first half", "first half"),
        ("keep only the last 10 seconds", "last 10 seconds"),
        ("keep the first 30 seconds", "first 30 seconds"),
    ],
)
def test_keep_a_duration_relative_range_resolves_the_time_first(text: str, phrase: str) -> None:
    assert calls(text) == [
        {"step_id": "s1", "tool": "resolve_time", "args": {"phrase": phrase}},
        {"step_id": "s2", "tool": "keep_range", "args": {"time_range": {"$ref": "s1"}}},
    ]


@pytest.mark.parametrize(
    "text",
    [
        "keep",
        "keep only",
        "keep it",
        "keep the whole video",
        "keep faces blurred",
        "keep the audio muted",
        "keep the noise down",
        "keep everything except 0:10 to 0:20",
        "keepsake",
        "keep 0:10 to 0:20 and keep 0:30 to 0:40",
        "keep 0:10 to 0:20, then keep 0:30 to 0:40",
        "keep 0:10 to 0:20 and then keep 0:30 to 0:40",
        "keep 0:10 to 0:20 and only keep 0:30 to 0:40",
        "only keep 0:10 to 0:20 and keep 0:30 to 0:40",
    ],
)
def test_keep_requests_that_are_not_plain_give_none(text: str) -> None:
    assert parse(text) is None  # more than one keep would silently keep only their overlap


def test_keep_with_a_bare_number_asks_seconds_or_minutes_like_cut() -> None:
    with pytest.raises(TimeParseError, match="seconds or minutes"):
        parse("keep 10 to 20")


def test_keep_after_a_cut_is_two_steps_in_order() -> None:
    result = calls("cut 0:05 to 0:08 and keep 0:10 to 0:20")

    assert [c["tool"] for c in result] == ["cut_range", "keep_range"]
    assert [c["step_id"] for c in result] == ["s1", "s2"]


def test_keep_doesnt_trigger_the_mixed_time_question() -> None:
    # Keep is a whole-timeline edit, so a mask or mute with no time next to it isn't ambiguous
    # (unlike "blur faces and cut 0:10 to 0:20"): the blur covers the video, keep cuts the rest.
    assert [c["tool"] for c in calls("blur faces and keep 0:10 to 0:20")] == [
        "find_objects",
        "add_mask",
        "keep_range",
    ]
    assert [c["tool"] for c in calls("keep 0:10 to 0:20 and mute")] == ["keep_range", "mute"]


def test_a_timed_edit_outside_the_kept_range_still_plans() -> None:
    # The mute lands in a part keep cuts away, so it changes nothing: harmless, since it can't
    # leave anything unhidden. Pinned so a change to this is deliberate.
    assert [c["tool"] for c in calls("mute 0:30 to 0:40 and keep 0:10 to 0:20")] == [
        "mute",
        "keep_range",
    ]


def test_cut_next_to_an_untimed_edit_still_asks() -> None:
    with pytest.raises(TimeParseError):
        parse("blur faces and cut 0:10 to 0:20")


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


def test_first_n_goes_through_resolve_time_like_last_n() -> None:
    result = calls("cut the first 2 minutes")

    assert result == [
        {"step_id": "s1", "tool": "resolve_time", "args": {"phrase": "first 2 minutes"}},
        {"step_id": "s2", "tool": "cut_range", "args": {"time_range": {"$ref": "s1"}}},
    ]
