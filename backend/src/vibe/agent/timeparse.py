"""Time phrases in plain requests ("0:10 to 0:20", "the first 5 seconds", "after 1:30").

Pure functions, no video needed. Phrases that need the video's duration ("first half",
"last 10 seconds") come back as text for a ``resolve_time`` step, not as numbers.
"""

import re

from pydantic import ValidationError

from vibe.contract import TimeRange


class TimeParseError(ValueError):
    """A time the user wrote that can't be used as is. The message is a question for them."""


_UNIT = r"(?:s|secs?|seconds?|m|mins?|minutes?)"
_TS = rf"\d+(?::\d{{2}}){{0,2}}(?:\.\d+)?(?:\s*{_UNIT}\b)?"
"""One timestamp: "10", "1.5", "10s", "2 minutes", "1:30", "1:02:03"."""

_TS_EXPLICIT = rf"(?:\d+(?::\d{{2}}){{1,2}}(?:\.\d+)?|\d+(?:\.\d+)?\s*{_UNIT}\b)"
"""A timestamp that says its unit ("5 seconds", "0:05"). "the first 5" alone is ambiguous."""

_TIMESTAMP = re.compile(rf"(\d+(?::\d{{2}}){{0,2}}(?:\.\d+)?)\s*({_UNIT})?")

_LEADING_WORDS = re.compile(r"^(?:(?:for|during|in|over|on|everything|all)\s+)+")

_RANGE = re.compile(
    rf"(?:from\s+|between\s+)?({_TS})\s*(?:to|until|till|through|and|-|–)\s*({_TS})"
)
_HALF = re.compile(r"(?:the\s+)?((?:first|second) half)")
_FIRST = re.compile(rf"(?:the\s+)?first\s+({_TS_EXPLICIT})")
_LAST = re.compile(rf"(?:the\s+)?(last\s+{_TS_EXPLICIT})")
_AFTER = re.compile(rf"(?:after|from|starting at|since)\s+({_TS})(?:\s+on(?:wards?)?)?")
_BEFORE = re.compile(rf"(?:before|until|till|up to)\s+({_TS})")

_MINUTE_UNITS = ("m", "min", "mins", "minute", "minutes")


def parse_timestamp(text: str) -> float:
    """Seconds for one timestamp. Raises ``TimeParseError`` if it isn't one."""
    match = _TIMESTAMP.fullmatch(text.strip())
    if match is None:
        raise TimeParseError(f"I couldn't read {text!r} as a time. Try 0:10 or 10 seconds.")
    number, unit = match.groups()
    parts = number.split(":")
    if len(parts) == 1:
        seconds = float(number)
        return seconds * 60 if unit in _MINUTE_UNITS else seconds
    if unit is not None:
        raise TimeParseError(f"{text!r} mixes a clock time and a unit. Try 1:30 or 90 seconds.")
    if any(float(p) >= 60 for p in parts[1:]):
        raise TimeParseError(f"{text!r} isn't a valid time: minutes and seconds go up to 59.")
    total = 0.0
    for part in parts:
        total = total * 60 + float(part)
    return total


def parse_time_phrase(text: str) -> TimeRange | str | None:
    """A time range, a duration-relative phrase (for ``resolve_time``), or None.

    The whole text must be the time phrase: anything left over means it isn't one, so
    "faces except mine" gives None rather than a wrong guess. Raises ``TimeParseError`` for a
    range that is empty or backwards.
    """
    text = _LEADING_WORDS.sub("", text.strip())
    if match := _RANGE.fullmatch(text):
        return _written_range(match.group(1), match.group(2))
    if match := _HALF.fullmatch(text):
        return match.group(1)
    if match := _FIRST.fullmatch(text):
        return _range("0", match.group(1))
    if match := _LAST.fullmatch(text):
        return match.group(1)
    if match := _AFTER.fullmatch(text):
        return _range(match.group(1), None)
    if match := _BEFORE.fullmatch(text):
        return _range("0", match.group(1))
    return None


def _is_bare_number(text: str) -> bool:
    match = _TIMESTAMP.fullmatch(text.strip())
    return match is not None and ":" not in match.group(1) and match.group(2) is None


def _written_range(start: str, end: str) -> TimeRange:
    """A range the user wrote out, e.g. "0:10 to 0:20"."""
    if _is_bare_number(start) != _is_bare_number(end):
        # "10 to 20 minutes": is the start 10 seconds or 10 minutes? Ask, don't guess.
        raise TimeParseError(
            f"Is {start.strip()!r} in the same unit as {end.strip()!r}? "
            "Give both times, like 10 to 20 minutes as 10:00 to 20:00."
        )
    return _range(start, end)


def _range(start: str, end: str | None) -> TimeRange:
    start_s = parse_timestamp(start)
    end_s = None if end is None else parse_timestamp(end)
    try:
        return TimeRange(start=start_s, end=end_s)
    except ValidationError:
        raise TimeParseError(
            f"The end ({end}) is before or at the start ({start}). Which range did you mean?"
        ) from None
