"""Template parser: plain requests become a plan instantly, with no LLM and no network.

It only takes what it fully understands. A request with any part it can't read ("blur him",
"faces except mine", "cut it") gives None, so the planner asks or hands it to the LLM instead
of guessing. Clauses joined by "and" / "then" are planned in order.
"""

import re
from collections.abc import Callable
from typing import Any, Literal

from vibe.agent.plan import Plan, ToolCall, mixes_history, ref, too_many_keeps
from vibe.agent.timeparse import TimeParseError, parse_time_phrase
from vibe.contract import WHOLE_CLIP, TimeRange

_EDIT_VERBS = (
    r"blur|pixelate|black ?box|box|hide|mute|silence|unmute|restore|cut|remove|delete"
    r"|denoise|reduce|get rid of|take out|filter out|isolate|clean up"
)
_KEEP_VERBS = r"keep|(?:only|just) keep"
_VERBS = f"{_EDIT_VERBS}|{_KEEP_VERBS}"
"""No "trim": it means "cut this" or "keep only this" depending on who says it, so it's asked."""

_CLAUSE_SPLIT = re.compile(
    r"\s*(?:,\s*and then|,\s*then|,\s*and|,|\s+and then|\s+then|\s+and)\s+"
    rf"(?=(?:{_VERBS})\b)"
    rf"|\s*,?\s+but\s+(?=(?:{_EDIT_VERBS})\b)"
)
"""Splits before a verb only: "keep 0:10 to 0:20 but blur faces" is two edits, while "keep
everything but 0:10 to 0:20" stays one clause (and is asked about). "but keep" never splits:
"cut 0:10 to 0:20 but keep 0:15" or "blur faces but keep 0:10 to 0:20" may be an exception to
the first edit, not a second one, so those are asked about."""

_MASK = re.compile(
    r"(blur|pixelate|black ?box|box|hide)(?:\s+out)?\s+"
    r"(?:all\s+(?:the\s+)?|the\s+|every\s+|each\s+)?"
    r"(faces?|people|persons?|everyone|everybody|license plates?|plates?)\b(.*)"
)
_MUTE = re.compile(r"(?:mute|silence)(?:\s+(?:the\s+)?(?:audio|sound|video|clip))?\b(.*)")
"""No "it": "mute it" may mean a person or a sound the user is pointing at, not the whole clip,
so it goes to the LLM or gets asked about, like other pronouns ("mute him")."""
_UNMUTE = re.compile(
    r"(?:unmute(?:\s+(?:the\s+)?(?:audio|sound|video|clip))?"
    r"|restore\s+(?:the\s+)?(?:audio|sound))\b(.*)"
)
_DENOISE = re.compile(
    r"(?:denoise(?:\s+(?:the\s+)?(?:audio|sound|video|clip|it))?"
    r"|(remove|reduce|cut|delete)\s+(?:the\s+)?(?:background\s+)?(?:noises?|hiss(?:es)?|hums?)"
    r"(?:\s+(?:in|from)\s+(?:the\s+)?(?:audio|sound|video|clip))?)\b(.*)"
)
"""Before ``_CUT``: "remove the noise" filters the audio, it doesn't cut the video.

"cut the noise from 0:10 to 0:20" may mean cutting that noisy part out, so with a range, "cut"
and "delete" are asked about instead (see ``_denoise``)."""
STEADY_NOISE_WORDS = frozenset(
    "noise noises hiss hisses hissing hum hums humming buzz buzzes buzzing fan fans".split()
)
"""Steady sounds: ``denoise`` (F-33) is enough for them."""
CLICK_WORDS = frozenset(
    "tap taps tapping click clicks clicking typing knock knocks knocking thump thumps "
    "bump bumps sounds".split()
)
"""Short sounds (and "background sounds", which may be anything): ``denoise`` doesn't touch
them, so they plan ``isolate_voice`` (F-34), which keeps the voices and turns the rest down."""
NOISE_WORDS = STEADY_NOISE_WORDS | CLICK_WORDS
VOICE_WORDS = frozenset("voice voices speech talking speaker speakers dialogue".split())

_SOUND_KIND = (
    r"(?:(?:background|computer|laptop|pc|finger|desk|table|keyboard|mouse|pen|annoying|loud"
    r"|constant|electrical|fan)\s+)*"
)
_SOUND = (
    r"(?:the\s+|that\s+|those\s+|any\s+|all\s+(?:the\s+|that\s+)?)?"
    rf"(?:{_SOUND_KIND}(?:noises?|hiss(?:es|ing)?|hums?|humming|buzz(?:es|ing)?|tapping|taps?"
    r"|clicks?|clicking|typing|knocks?|knocking|thumps?|bumps?)"
    r"|(?:computer|laptop|pc|ceiling|desk)\s+fans?|background\s+sounds?)"
)
"""One unwanted sound. A bare "fan" or "sound" isn't one: "remove the fan" may mean the object
on screen, and "remove the sound" may mean muting."""
_SOUNDS = re.compile(
    r"(remove|reduce|cut|delete|get\s+rid\s+of|take\s+out|filter\s+out)\s+(?:out\s+)?"
    rf"({_SOUND}(?:\s*,\s*(?:and\s+)?{_SOUND}|\s+and\s+{_SOUND})*)"
    r"(?:\s+(?:in|from)\s+(?:the\s+)?(?:audio|sound|video|clip|background))?\b(.*)"
)
"""A list of unwanted sounds ("the finger tapping and the computer fans"). Steady ones only plan
``denoise``; any short one plans ``isolate_voice``. Before ``_DENOISE`` and ``_CUT``."""
_ISOLATE = re.compile(
    r"(?:(?:isolate|(?:(?:only|just)\s+)?keep(?:\s+(?:only|just))?)\s+(?:the\s+)?"
    r"(?:voices?|speech|talking|speakers?|dialogue|people\s+talking)(?:\s+only)?"
    r"|clean\s+up\s+(?:the\s+)?(?:audio|sound))\b(.*)"
)
"""Before ``_KEEP``: "keep only the voices" filters the sound, it doesn't keep a time range."""
_CUT = re.compile(r"(?:cut|remove|delete)(?:\s+out)?\b(.*)")
_KEEP = re.compile(r"(?:(?:only|just)\s+)?keep(?:\s+(?:only|just))?\b(.*)")
"""Keep only a range: the toolbox cuts everything around it."""
_HISTORY = re.compile(
    r"(undo|redo)(?:\s+(?:that|it|this|(?:the|my)\s+last\s+(?:edit|change|one|thing)))?"
)
"""Undo is linear: it only ever acts on the latest request, so "undo the blur" (which may not
be the latest) isn't matched and gets asked about."""
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
        self._made = 0

    def add(self, tool: str, args: dict[str, Any]) -> str:
        self._made += 1
        step_id = f"s{self._made}"
        self.calls.append(ToolCall(step_id=step_id, tool=tool, args=args))
        return step_id

    def add_filter(self, tool: str, args: dict[str, Any]) -> None:
        """An audio filter step, once. ``isolate_voice`` already turns down steady noise, so
        "remove the noise and keep the voices" is one ``isolate_voice``, not a ``denoise`` too.
        Only exact repeats merge: same literal range, or both the whole clip."""
        same = [c for c in self.calls if c.tool in ("denoise", "isolate_voice") and c.args == args]
        if any(c.tool in (tool, "isolate_voice") for c in same):
            return
        if tool == "isolate_voice":
            self.calls = [c for c in self.calls if c not in same]
        self.add(tool, args)

    def time_arg(self, phrase: TimeRange | str) -> dict[str, Any]:
        """A literal range, or a ``resolve_time`` step and a ref to it."""
        if isinstance(phrase, str):
            return ref(self.add("resolve_time", {"phrase": phrase}))
        return phrase.model_dump(exclude_none=True)


class MixedTimeError(TimeParseError):
    """Only part of a request has a time, so the time may have been meant for every part."""


_NO_TIME = object()
"""The clause has no time words: the edit applies to the whole clip."""

Matched = Literal["timed", "untimed", "keep", "history"] | None
"""Whether a clause said when (a range, or "for the whole video") or didn't; None: no match.

A request mixing both ("blur faces and mute 0:10 to 0:20") is asked about, because the time
may have been meant for every part. "keep" edits the whole timeline, so it never takes part;
neither does "history" (undo, redo), which has to be the whole request."""


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


_DESCRIBED = re.compile(
    r"(?:(except(?:\s+for)?|but(?:\s+not)?|other\s+than|apart\s+from)\s+"
    r"(?:the\s+|any\s+)?(?:person|people|one|ones)\s+)?"
    r"((?:in|wearing|with|dressed\s+in)\s+.+)"
)
"""What may follow a person noun (F-13): "in red", "wearing a hat", "except the one in red".
Only people: "faces except the person in red" needs faces linked to people, which isn't built.

No "except me" / "except mine": that's identity (F-52), not something a description can check."""

_SURE_ONLY = re.compile(
    r",?\s*(?:(?:but|and)\s+)?(?:only\s+(?:if|when)\s+(?:you(?:'re|\s+are)\s+)?(?:sure|certain)"
    r"|only\s+the\s+(?:sure|certain)\s+(?:ones|matches)"
    r"|(?:the\s+)?(?:sure|certain)\s+(?:ones|matches)\s+only"
    r"|(?:skip|not|without)\s+the\s+unsure(?:\s+ones)?)$"
)
"""The user giving up "unsure = masked" for this request, in so many words. Nothing vaguer
("only the person in red") counts: that may just be emphasis."""


def _cut_sure_only(text: str) -> tuple[str, bool]:
    """``text`` without a "only if sure" ending, and whether it had one."""
    found = _SURE_ONLY.search(text)
    return (text[: found.start()], True) if found else (text, False)


_DANGLING = frozenset(
    {"in", "with", "wearing", "dressed", "a", "an", "the", "and", "or", "of"}
    | {"from", "to", "at", "for", "until"}
)
_NOT_A_DESCRIPTION = frozenset(
    # a negation: "in red except from 0:10" would mask the opposite of what was asked
    "except but not excluding unless outside other apart "
    # someone else: a second target ("in red and the person in blue") or identity (F-52)
    "person people persons one ones everyone everybody face faces plate plates "
    "me my mine myself i him her his hers them their us our you your we he she they it "
    # where, not what they look like: positions come from the boxes, which nothing reads yet
    "video clip frame scene shot background foreground picture footage image left right "
    "middle center centre front back bottom side corner here there "
    # the effect, not the target: "with a strong blur"
    "blur blurs blurred blurring blurry pixelate pixelated pixelation pixels box boxes "
    "effect effects mask masks strength intensity "
    # how sure, not how they look: a "sure" phrase _SURE_ONLY didn't take ("if sure, in red")
    "sure certain unsure uncertain".split()
)
"""A word that means the text after the person noun isn't (only) a description of how they
look. The template declines, so the planner asks or the LLM reads it, instead of masking by a
description nobody gave."""


def _described(rest: str, verb: str, steps: _Steps) -> tuple[str, object, bool] | None:
    """Split "in red from 0:10 to 0:20" into a ``find_by_description`` step, its time, and
    whether the mask should follow the target (``add_mask``'s ``while_tracked``).

    The time is the longest ending that reads as a time phrase; the description is what's
    before it, kept in the user's words for whatever checks it. One described target is
    followed; "everyone except ..." hides bystanders, so their masks hold for the whole range.
    """
    match = _DESCRIBED.fullmatch(rest.strip())
    if match is None:
        return None
    exclude, text = match.groups()
    # "Only if sure" may come after the time ("in red from 0:10 to 0:20, only if sure") or
    # before it ("in red only if sure from 0:10 to 0:20"), so look for it on both sides of the
    # time split. Left in the description, it would make every judge unsure and mask everyone.
    text, sure_only = _cut_sure_only(text)
    words = text.split()
    when: object = _NO_TIME
    for i in range(1, len(words)):
        found = parse_time_phrase(" ".join(words[i:]))
        if found is not None:
            words, when = words[:i], found
            break
    if when is not _NO_TIME:
        rest, before_time = _cut_sure_only(" ".join(words).rstrip(" ,"))
        words, sure_only = rest.split(), sure_only or before_time
    description = " ".join(words).strip(" ,")  # the user's words, commas inside included
    words = [w for w in (word.strip(",") for word in words) if w]
    if len(words) < 2 or words[-1] in _DANGLING:
        return None  # "with the" before a time, or "in" alone: nothing to look for
    if set(words) & _NOT_A_DESCRIPTION or _TIME_WORDS.search(description):
        return None  # a second time ("... from 0:10 to 0:20 and from 0:30"), a place, an effect
    if verb.endswith("box") and words == ["in", "black"]:
        return None  # "box everyone in black": a black box, or the people in black?
    if exclude and sure_only:
        return None  # sure of what: who to leave out, or who to hide? Ask.
    args: dict[str, Any] = {"label": "person", "description": description}
    if exclude:
        args["exclude"] = True
    if sure_only:
        args["unsure"] = "leave"
    return steps.add("find_by_description", args), when, not exclude


def _mask(match: re.Match[str], steps: _Steps) -> Matched:
    verb, noun, rest = match.groups()
    when = _time(rest)
    follow = False
    if when is None:
        described = _described(rest, verb, steps) if _LABELS[noun] == "person" else None
        if described is None:
            return None
        targets, when, follow = described
    else:
        targets = steps.add("find_objects", {"label": _LABELS[noun]})
    args: dict[str, Any] = {
        "targets": ref(targets),
        "effect": {"type": _EFFECTS[verb.replace("black", "").strip()]},
        **_time_args(when, steps),
    }
    if follow:
        args["while_tracked"] = True
    steps.add("add_mask", args)
    return _scope(when)


_TIME_WORDS = re.compile(
    r"\b(?:first|last|middle|half|thirds?|whole|entire|end|minutes?|seconds?)\b|\d"
)
"""Words that may be a time ("goodbye in the last minute", "hello in the middle third", "until
the end") rather than speech. ponytail: a word list, not a parse (``parse_time_phrase`` doesn't
read "the last minute"), so "he says 3" or "says the end" is asked too; quoting the words skips
it. Add a ``within`` range to ``find_speech`` if it's too strict."""


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


def _unmute(match: re.Match[str], steps: _Steps) -> Matched:
    when = _time(match.group(1))
    if when is None:
        return None
    steps.add("unmute", _time_args(when, steps))
    return _scope(when)


def _denoise(match: re.Match[str], steps: _Steps) -> Matched:
    verb, rest = match.groups()
    when = _time(rest)
    if when is None:
        return None
    if verb in ("cut", "delete") and when is not _NO_TIME and when != WHOLE_CLIP:
        return None  # cut that part out, or filter it? Ask.
    steps.add_filter("denoise", _time_args(when, steps))
    return _scope(when)


def _sounds(match: re.Match[str], steps: _Steps) -> Matched:
    verb, sounds, rest = match.groups()
    when = _time(rest)
    if when is None:
        return None
    if verb in ("cut", "delete") and when is not _NO_TIME and when != WHOLE_CLIP:
        return None  # cut that part out, or filter it? Ask.
    clicks = not CLICK_WORDS.isdisjoint(re.findall(r"[a-z]+", sounds))
    steps.add_filter("isolate_voice" if clicks else "denoise", _time_args(when, steps))
    return _scope(when)


def _isolate(match: re.Match[str], steps: _Steps) -> Matched:
    when = _time(match.group(1))
    if when is None:
        return None
    steps.add_filter("isolate_voice", _time_args(when, steps))
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


def _keep(match: re.Match[str], steps: _Steps) -> Matched:
    when = _time(match.group(1))
    if when is None or when is _NO_TIME or when == WHOLE_CLIP:  # never guess what to keep
        return None
    steps.add("keep_range", {"time_range": steps.time_arg(when)})
    return "keep"


def _history(match: re.Match[str], steps: _Steps) -> Matched:
    steps.add(match.group(1), {})
    return "history"


_TEMPLATES: tuple[tuple[re.Pattern[str], Callable[[re.Match[str], _Steps], Matched]], ...] = (
    (_MASK, _mask),
    (_MUTE, _mute),
    (_UNMUTE, _unmute),
    (_SOUNDS, _sounds),
    (_ISOLATE, _isolate),
    (_DENOISE, _denoise),
    (_CUT, _cut),
    (_KEEP, _keep),
    (_HISTORY, _history),
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
    if too_many_keeps(steps.calls):
        return None  # asked about; validate_plan would refuse it too
    if mixes_history(steps.calls):
        return None  # "undo and blur faces": two requests, so asked about
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
