"""F-05: time phrases in plain requests. Pure functions: no video, no network."""

import pytest

from vibe.agent.timeparse import TimeParseError, parse_time_phrase, parse_timestamp
from vibe.contract import TimeRange


@pytest.mark.parametrize(
    ("text", "seconds"),
    [
        ("10", 10),
        ("1.5", 1.5),
        ("10s", 10),
        ("10 seconds", 10),
        ("2 minutes", 120),
        ("0:10", 10),
        ("1:30", 90),
        ("1:02:03", 3723),
        ("0:10.5", 10.5),
    ],
)
def test_parse_timestamp_accepts_common_formats(text: str, seconds: float) -> None:
    assert parse_timestamp(text) == seconds


@pytest.mark.parametrize("text", ["", "abc", "1:75", "1::2", "-5", "1:5", "1:30 minutes"])
def test_parse_timestamp_rejects_bad_input(text: str) -> None:
    with pytest.raises(TimeParseError):
        parse_timestamp(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("0:10 to 0:20", TimeRange(start=10, end=20)),
        ("from 0:10 to 0:20", TimeRange(start=10, end=20)),
        ("0:05-0:08", TimeRange(start=5, end=8)),
        ("between 0:10 and 0:20", TimeRange(start=10, end=20)),
        ("for the first 5 seconds", TimeRange(start=0, end=5)),
        ("the first 5 seconds", TimeRange(start=0, end=5)),
        ("after 1:30", TimeRange(start=90)),
        ("from 0:30 on", TimeRange(start=30)),
        ("before 0:30", TimeRange(start=0, end=30)),
        ("until 1:00", TimeRange(start=0, end=60)),
        ("for the whole video", TimeRange(start=0)),
        ("the entire clip", TimeRange(start=0)),
    ],
)
def test_parse_time_phrase_gives_ranges(text: str, expected: TimeRange) -> None:
    assert parse_time_phrase(text) == expected


@pytest.mark.parametrize(
    ("text", "phrase"),
    [
        ("for the first half", "first half"),
        ("the second half", "second half"),
        ("the last 10 seconds", "last 10 seconds"),
    ],
)
def test_duration_relative_phrases_are_left_for_resolve_time(text: str, phrase: str) -> None:
    assert parse_time_phrase(text) == phrase


@pytest.mark.parametrize(
    "text", ["him", "the guy in red", "except mine", "it", "nicely", "the first 5", "the last 10"]
)
def test_text_that_is_not_a_time_phrase_gives_none(text: str) -> None:
    assert parse_time_phrase(text) is None


@pytest.mark.parametrize(
    "text", ["0:20 to 0:10", "before 0:00", "0:10 to 0:10", "10 to 20 minutes", "1-2 minutes"]
)
def test_empty_or_backwards_ranges_raise(text: str) -> None:
    with pytest.raises(TimeParseError):
        parse_time_phrase(text)
