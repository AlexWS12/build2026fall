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

_VERBS = r"blur|pixelate|black ?box|box|hide|mute|silence|cut|remove|delete|denoise|reduce"
"""No "trim": it means "cut this" or "keep only this" depending on who says it, so it's asked."""

_CLAUSE_SPLIT = re.compile(
    rf"\s*(?:,\s*and then|,\s*then|,\s*and|,|\s+and then|\s+then|\s+and)\s+(?=(?:{_VERBS})\b)"
)

_MASK = re.compile(
    r"(blur|pixelate|black ?box|box|hide)(?:\s+out)?\s+"
    r"(?:all\s+(?:the\s+)?|the\s+|every\s+|each\s+)?"
    r"(faces?|people|persons?|everyone|everybody|license plates?|plates?)\b(.*)"
)
_MUTE = re.compile(r"(?:mute|silence)(?:\s+(?:the\s+)?(?:audio|sound|video|clip|it))?\b(.*)")
_DENOISE = re.compile(
    r"(?:denoise(?:\s+(?:the\s+)?(?:audio|sound|video|clip|it))?"
    r"|(remove|reduce|cut|delete)\s+(?:the\s+)?(?:background\s+)?(?:noises?|hiss(?:es)?|hums?)"
    r"(?:\s+(?:in|from)\s+(?:the\s+)?(?:audio|sound|video|clip))?)\b(.*)"
)
"""Before ``_CUT``: "remove the noise" filters the audio, it doesn't cut the video.

"cut the noise from 0:10 to 0:20" may mean cutting that noisy part out, so with a range, "cut"
and "delete" are asked about instead (see ``_denoise``)."""
NOISE_WORDS = frozenset({"noise", "noises", "hiss", "hisses", "hum", "hums"})
_CUT = re.compile(r"(?:cut|remove|delete)(?:\s+out)?\b(.*)")
_SPOKEN = re.compile(
    r"(?:the\s+(?:part|bit|section)\s+)?(?:where|when)\s+"
    r"(?:(?:he|she|they|i|you|we|someone|somebody)\s+)?(?:says?|said)\s+(.+)"
    r"|the\s+words?\s+(.+)"
)
"""Where words are said (F-21, words only: "where she talks about X" is a topic, for the LLM)."""

_EFFECTS = {"blur": "blur", "hide": "blur", "pixelate": "pixelate", "box": "box"}
_LABELS = {
    "face": "face",
    "faces": "face",
    "people": "person",
    "person": "person",
    "persons": "person",
    "everyone": "person",
    "everybody": "person",
    "license plate": "license_plate",
    "license plates": "license_plate",
    "plate": "license_plate",
    "plates": "license_plate",
}
"""Singular nouns mask every match too ("blur the face" = all faces): fail closed for privacy."""


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


class MixedTimeError(TimeParseError):
    """Only part of a request has a time, so the time may have been meant for every part."""


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


_TIME_WORDS = re.compile(r"\b(?:first|last|half|whole|entire|minutes?|seconds?)\b|\d")
"""Words that may be a time ("goodbye in the last minute") rather than speech. ponytail: a word
list, not a parse (``parse_time_phrase`` doesn't read "the last minute"), so "he says 3" is
asked too; quoting the words skips it. Add a ``within`` range to ``find_speech`` if it's too
strict."""


def _spoken(rest: str, steps: _Steps, bare_words: bool = False) -> dict[str, Any] | None:
    """``{"time_range": $ref}`` to a ``find_speech`` step for "where he says X", else None.

    ``bare_words`` also takes "the word(s) X", only for mute: "remove the words on the screen"
    isn't speech, and a cut is never guessed. Unquoted words with a time word in them give
    None (asked): ``find_speech`` has no time-range argument, so the time would be searched
    for as spoken words.
    """
    match = _SPOKEN.fullmatch(rest.strip())
    if match is None or (match.group(2) and not bare_words):
        return None
    said = (match.group(1) or match.group(2)).strip()
    quoted = len(said) > 1 and said[0] == said[-1] and said[0] in "\"'"
    words = said.strip("\"' ")
    if not words or (not quoted and _TIME_WORDS.search(words)):
        return None
    return {"time_range": ref(steps.add("find_speech", {"text": words}))}


def _mute(match: re.Match[str], steps: _Steps) -> Matched:
    if (spoken := _spoken(match.group(1), steps, bare_words=True)) is not None:
        steps.add("mute", spoken)
        return "timed"
    when = _time(match.group(1))
    if when is None:
        return None
    steps.add("mute", _time_args(when, steps))
    return _scope(when)


def _denoise(match: re.Match[str], steps: _Steps) -> Matched:
    verb, rest = match.groups()
    when = _time(rest)
    if when is None:
        return None
    if verb in ("cut", "delete") and when is not _NO_TIME and when != WHOLE_CLIP:
        return None  # cut that part out, or filter it? Ask.
    steps.add("denoise", _time_args(when, steps))
    return _scope(when)


def _cut(match: re.Match[str], steps: _Steps) -> Matched:
    if (spoken := _spoken(match.group(1), steps)) is not None:
        steps.add("cut_range", spoken)
        return "timed"
    when = _time(match.group(1))
    if when is None or when is _NO_TIME or when == WHOLE_CLIP:  # never guess what to cut
        return None
    steps.add("cut_range", {"time_range": steps.time_arg(when)})
    return "timed"


_TEMPLATES: tuple[tuple[re.Pattern[str], Callable[[re.Match[str], _Steps], Matched]], ...] = (
    (_MASK, _mask),
    (_MUTE, _mute),
    (_DENOISE, _denoise),
    (_CUT, _cut),
)


_POLITE_START = re.compile(r"^(?:(?:please|can you|could you|would you|pls)\s+)+")
_POLITE_END = re.compile(r"(?:,?\s+(?:please|pls|thanks|thank you))+$")


def normalize(text: str) -> str:
    """Lowercase, single spaces, and no polite words or end punctuation around the request."""
    text = re.sub(r"\s+", " ", text.strip().lower())
    text = _POLITE_END.sub("", re.sub(r"[.!?]+$", "", text))
    return re.sub(r"[,.!?]+$", "", _POLITE_START.sub("", text))


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
        raise MixedTimeError(
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
