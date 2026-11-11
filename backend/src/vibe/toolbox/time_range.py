"""Time phrases resolved against the video's length (F-12): "first half", "the last third",
"first 2 minutes", "after 0:30", "before 1:00", "between 0:10 and 0:40".

``resolve_range`` is a pure function, so the planner, the API and the UI can all use it. The
``resolve_time`` tool runs on it. It returns the contract's ``TimeRange`` (source seconds, end
excluded, ``end=None`` = until the end of the video) and checks it fits inside the video.
"""

import math
import re

from vibe.contract import TimeRange
from vibe.toolbox.spec import ToolError
from vibe.toolbox.timecodes import (
    AFTER,
    BEFORE,
    BETWEEN,
    FRACTION,
    LEADING_WORDS,
    SPAN,
    TS,
    TimeParseError,
    is_bare_number,
    parse_timestamp,
)

__all__ = ["TimeRangeError", "check_range", "resolve_range"]


class TimeRangeError(ToolError):
    """A time phrase that can't become a range inside this video: unreadable, empty, backwards,
    past the end, or as long as the whole video when only part was asked for. Nothing is
    clamped or guessed; the message says what's wrong so the user can be asked.

    Only for what the user can fix by rephrasing the time. Missing context (no video duration)
    is a plain ``ToolError``."""


_EDGE = re.compile(rf"(first|last)\s+({TS})")
"""The rest of the grammar is in ``timecodes``, shared with the planner so the two can't drift."""

_PARTS = {
    "first half": (0, 1, 2),
    "second half": (1, 2, 2),
    "first third": (0, 1, 3),
    "middle third": (1, 2, 3),
    "second third": (1, 2, 3),
    "last third": (2, 3, 3),
}
"""(from, to, parts) for each ``FRACTION``: "the middle third" is 1/3 to 2/3."""


def resolve_range(phrase: str, duration: float) -> TimeRange:
    """The range ``phrase`` means in a video ``duration`` seconds long.

    Ranges that reach the end of the video have ``end=None``. Raises ``TimeRangeError`` if the
    duration isn't a positive number, the phrase isn't one of the forms above, a number has
    no unit ("the first 5": seconds or minutes?), or the range is empty, backwards or goes
    past the end. "first N" / "last N" as long as the video are refused too, so "cut the last
    2 minutes" of a 1-minute clip asks instead of cutting everything.
    """
    if not math.isfinite(duration) or duration <= 0:
        raise TimeRangeError(f"the video duration must be positive, not {duration}")
    # As a user (or the LLM planner, passing their words on) writes it: "for the first half".
    text = LEADING_WORDS.sub("", re.sub(r"\s+", " ", phrase.strip().lower()))
    text = re.sub(r"^the ", "", text)

    if match := FRACTION.fullmatch(text):
        lo, hi, n = _PARTS[match.group(1)]
        return _range(duration * lo / n, None if hi == n else duration * hi / n, duration)
    if match := _EDGE.fullmatch(text):
        side, ts = match.groups()
        seconds = _seconds(ts)
        if seconds <= 0:
            raise TimeRangeError(f"{phrase!r} is an empty range")
        if seconds >= duration:
            raise TimeRangeError(f"{phrase!r} is as long as the whole video ({duration:g} seconds)")
        if side == "first":
            return _range(0.0, seconds, duration)
        return _range(duration - seconds, None, duration)
    if match := AFTER.fullmatch(text):
        return _range(_seconds(match.group(1)), None, duration)
    if match := BEFORE.fullmatch(text):
        return _range(0.0, _seconds(match.group(1)), duration)
    if match := BETWEEN.fullmatch(text) or SPAN.fullmatch(text):
        return _range(_seconds(match.group(1)), _seconds(match.group(2)), duration)
    raise TimeRangeError(f"can't resolve {phrase!r}")


def check_range(r: TimeRange, duration: float) -> TimeRange:
    """``r`` if it starts before the end of the video and doesn't end after it.

    ``TimeRange`` already guarantees ``0 <= start`` and ``start < end``; this adds the video's
    length, which the contract doesn't know. An end exactly at ``duration`` is fine.
    """
    if r.start >= duration:
        raise TimeRangeError(
            f"{r.start:g}s is at or after the end of the video ({duration:g} seconds)"
        )
    if r.end is not None and r.end > duration:
        raise TimeRangeError(f"{r.end:g}s is after the end of the video ({duration:g} seconds)")
    return r


def _seconds(timestamp: str) -> float:
    timestamp = timestamp.strip()
    if is_bare_number(timestamp):
        raise TimeRangeError(
            f"Is {timestamp!r} in seconds or minutes? Add the unit, like '{timestamp} seconds', "
            "or write a clock time like 1:30."
        )
    try:
        return parse_timestamp(timestamp)
    except TimeParseError as exc:
        raise TimeRangeError(str(exc)) from None


def _range(start: float, end: float | None, duration: float) -> TimeRange:
    if end is not None and end <= start:
        raise TimeRangeError(
            f"the end ({end:g}s) is before or at the start ({start:g}s): the range is empty"
        )
    return check_range(TimeRange(start=start, end=end), duration)
