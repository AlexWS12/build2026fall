"""Template parser: plain requests become a plan instantly, with no LLM and no network.

It only takes what it fully understands. A request with any part it can't read ("blur him",
"faces except mine", "cut it") gives None, so the planner asks or hands it to the LLM instead
of guessing. Clauses joined by "and" / "then" are planned in order.
"""

import re
from collections.abc import Callable
from typing import Any, Literal

from vibe.agent.plan import Plan, ToolCall, ref
from vibe.agent.timeparse import TimeParseError, parse_time_phrase
from vibe.contract import WHOLE_CLIP, TimeRange

_VERBS = r"blur|pixelate|black ?box|box|hide|mute|silence|cut|remove|delete"
"""No "trim": it means "cut this" or "keep only this" depending on who says it, so it's asked."""

_CLAUSE_SPLIT = re.compile(
    rf"\s*(?:,\s*and then|,\s*then|,\s*and|,|\s+and then|\s+then|\s+and)\s+(?=(?:{_VERBS})\b)"
)

_MASK = re.compile(
    r"(blur|pixelate|black ?box|box|hide)(?:\s+out)?\s+"
    r"(?:all\s+(?:the\s+)?|the\s+)?(faces|people|persons|everyone|license plates|plates)\b(.*)"
)
_MUTE = re.compile(r"(?:mute|silence)(?:\s+(?:the\s+)?(?:audio|sound|video|clip|it))?\b(.*)")
_CUT = re.compile(r"(?:cut|remove|delete)(?:\s+out)?\b(.*)")

_EFFECTS = {"blur": "blur", "hide": "blur", "pixelate": "pixelate", "box": "box"}
_LABELS = {
    "faces": "face",
    "people": "person",
    "persons": "person",
    "everyone": "person",
    "license plates": "license_plate",
    "plates": "license_plate",
}


class _Steps:
    """Numbers steps s1, s2, ... across the whole request."""

    def __init__(self) -> None:
        self.calls: list[ToolCall] = []

    def add(self, tool: str, args: dict[str, Any]) -> str:
        step_id = f"s{len(self.calls) + 1}"
        self.calls.append(ToolCall(step_id=step_id, tool=tool, args=args))
        return step_id

    def time_arg(self, phrase: TimeRange | str) -> dict[str, Any]:
        """A literal range, or a ``resolve_time`` step and a ref to it."""
        if isinstance(phrase, str):
            return ref(self.add("resolve_time", {"phrase": phrase}))
        return phrase.model_dump(exclude_none=True)


_NO_TIME = object()
"""The clause has no time words: the edit applies to the whole clip."""

Matched = Literal["timed", "untimed"] | None
"""Whether a clause said when (a range, or "for the whole video") or didn't; None: no match.

A request mixing both ("blur faces and mute 0:10 to 0:20") is asked about, because the time
may have been meant for every part."""


def _time(rest: str) -> TimeRange | str | object | None:
    rest = rest.strip()
    return _NO_TIME if not rest else parse_time_phrase(rest)


def _time_args(when: object, steps: _Steps) -> dict[str, Any]:
    """The ``time_range`` argument, left out for the whole clip."""
    if when is _NO_TIME or when == WHOLE_CLIP:
        return {}
    return {"time_range": steps.time_arg(when)}


def _scope(when: object) -> Matched:
    return "untimed" if when is _NO_TIME else "timed"


def _mask(match: re.Match[str], steps: _Steps) -> Matched:
    verb, noun, rest = match.groups()
    when = _time(rest)
    if when is None:
        return None
    targets = steps.add("find_objects", {"label": _LABELS[noun]})
    effect = {"type": _EFFECTS[verb.replace("black", "").strip()]}
    steps.add("add_mask", {"targets": ref(targets), "effect": effect, **_time_args(when, steps)})
    return _scope(when)


def _mute(match: re.Match[str], steps: _Steps) -> Matched:
    when = _time(match.group(1))
    if when is None:
        return None
    steps.add("mute", _time_args(when, steps))
    return _scope(when)


def _cut(match: re.Match[str], steps: _Steps) -> Matched:
    when = _time(match.group(1))
    if when is None or when is _NO_TIME or when == WHOLE_CLIP:  # never guess what to cut
        return None
    steps.add("cut_range", {"time_range": steps.time_arg(when)})
    return "timed"


_TEMPLATES: tuple[tuple[re.Pattern[str], Callable[[re.Match[str], _Steps], Matched]], ...] = (
    (_MASK, _mask),
    (_MUTE, _mute),
    (_CUT, _cut),
)


def normalize(text: str) -> str:
    text = re.sub(r"\s+", " ", text.strip().lower())
    text = re.sub(r"[.!?]+$", "", text)
    return re.sub(r"^(?:please|can you|could you)\s+", "", text)


def parse(text: str) -> Plan | None:
    """A plan if every clause of ``text`` matches a template, else None.

    Raises ``TimeParseError`` when a template matches but its time can't be used (e.g. a
    backwards range), so the planner can ask about that specifically.
    """
    text = normalize(text)
    if not text:
        return None
    steps = _Steps()
    scopes: set[Matched] = set()
    for clause in _CLAUSE_SPLIT.split(text):
        scope = _parse_clause(clause, steps)
        if scope is None:
            return None
        scopes.add(scope)
    if {"untimed", "timed"} <= scopes:
        raise TimeParseError(
            "Only part of that request has a time. Should the rest cover the whole video, or "
            "the same time? Say it for each part, like 'blur faces for the whole video and "
            "mute 0:10 to 0:20'."
        )
    return Plan(calls=tuple(steps.calls))


def _parse_clause(clause: str, steps: _Steps) -> Matched:
    for pattern, build in _TEMPLATES:
        if match := pattern.fullmatch(clause):
            return build(match, steps)
    return None
