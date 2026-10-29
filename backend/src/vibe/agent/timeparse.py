"""Time phrases in plain requests ("0:10 to 0:20", "the first 5 seconds", "after 1:30").

Pure functions, no video needed. Phrases that need the video's duration ("first half",
"last 10 seconds") come back as text for a ``resolve_time`` step, not as numbers.
"""

import re

from pydantic import ValidationError

from vibe.contract import WHOLE_CLIP, TimeRange
from vibe.toolbox.timecodes import (
    TIMESTAMP,
    UNIT,
    TimeParseError,
    has_unit,
    parse_timestamp,
)

__all__ = ["TimeParseError", "parse_time_phrase", "parse_timestamp"]


_TS = rf"\d+(?::\d{{2}}){{0,2}}(?:\.\d+)?(?:\s*{UNIT}\b)?"
"""One timestamp: "10", "1.5", "10s", "2 minutes", "1:30", "1:02:03"."""

_LEADING_WORDS = re.compile(r"^(?:(?:for|during|in|over|on|everything|all)\s+)+")

_RANGE = re.compile(rf"(?:from\s+)?({_TS})\s*(?:to|until|till|through|-|–)\s*({_TS})")
_BETWEEN = re.compile(rf"between\s+({_TS})\s*and\s*({_TS})")
"""Only "between" makes "X and Y" a range: "mute 0:10 and 0:20" may mean two moments."""
_WHOLE = re.compile(r"(?:the\s+)?(?:whole|entire|full)\s+(?:video|clip|thing)")
_HALF = re.compile(r"(?:the\s+)?((?:first|second) half)")
_FIRST = re.compile(rf"(?:the\s+)?first\s+({_TS})")
_LAST = re.compile(rf"(?:the\s+)?last\s+({_TS})")
_AFTER = re.compile(rf"(?:after|from|starting at|since)\s+({_TS})(?:\s+on(?:wards?)?)?")
_BEFORE = re.compile(rf"(?:before|until|till|up to)\s+({_TS})")


def parse_time_phrase(text: str) -> TimeRange | str | None:
    """A time range, a duration-relative phrase (for ``resolve_time``), or None.

    The whole text must be the time phrase: anything left over means it isn't one, so
    "faces except mine" gives None rather than a wrong guess. Raises ``TimeParseError`` for a
    range that is empty or backwards, and for a bare number ("10 to 20", "the first 5"),
    which could be seconds or minutes.
    """
    text = _LEADING_WORDS.sub("", text.strip())
    if match := _RANGE.fullmatch(text) or _BETWEEN.fullmatch(text):
        return _range(match.group(1), match.group(2))
    if _WHOLE.fullmatch(text):
        return WHOLE_CLIP
    if match := _HALF.fullmatch(text):
        return match.group(1)
    if match := _FIRST.fullmatch(text):
        return _range(None, match.group(1))
    if match := _LAST.fullmatch(text):
        return f"last {_explicit(match.group(1))}"
    if match := _AFTER.fullmatch(text):
        return _range(match.group(1), None)
    if match := _BEFORE.fullmatch(text):
        return _range(None, match.group(1))
    return None


def _explicit(timestamp: str) -> str:
    """``timestamp`` if it says its unit ("0:10", "10 seconds"); a bare "10" is asked about."""
    timestamp = timestamp.strip()
    if not has_unit(timestamp) and TIMESTAMP.fullmatch(timestamp):
        raise TimeParseError(
            f"Is {timestamp!r} in seconds or minutes? Add the unit, like '{timestamp} seconds', "
            "or write a clock time like 1:30."
        )
    return timestamp


def _range(start: str | None, end: str | None) -> TimeRange:
    """A range from written times. ``start=None`` is the start of the video."""
    start_s = 0.0 if start is None else parse_timestamp(_explicit(start))
    end_s = None if end is None else parse_timestamp(_explicit(end))
    try:
        return TimeRange(start=start_s, end=end_s)
    except ValidationError:
        if start is None:
            raise TimeParseError(
                f"There's nothing before {end.strip()}. Give a time after the start."
            ) from None
        raise TimeParseError(
            f"The end ({end.strip()}) is before or at the start ({start.strip()}). "
            "Which range did you mean?"
        ) from None
