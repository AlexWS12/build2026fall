"""Timestamps as people write them ("10", "1.5", "10s", "2 minutes", "1:30", "1:02:03").

Shared by ``resolve_time`` and the planner's time phrases (the agent imports it from here, so
the toolbox never imports the agent).
"""

import re

UNIT = r"(?:s|secs?|seconds?|m|mins?|minutes?)"

TIMESTAMP = re.compile(rf"(\d+(?::\d{{2}}){{0,2}}(?:\.\d+)?)\s*({UNIT})?")
"""Groups: the number (with any colons), then the unit if one was written."""

_MINUTE_UNITS = ("m", "min", "mins", "minute", "minutes")


class TimeParseError(ValueError):
    """A time the user wrote that can't be used as is. The message is a question for them."""


def parse_timestamp(text: str) -> float:
    """Seconds for one timestamp. Raises ``TimeParseError`` if it isn't one."""
    match = TIMESTAMP.fullmatch(text.strip())
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


def has_unit(text: str) -> bool:
    """True for "0:10" or "10 seconds"; False for a bare "10", which could be either unit."""
    match = TIMESTAMP.fullmatch(text.strip())
    return match is not None and (":" in match.group(1) or match.group(2) is not None)
