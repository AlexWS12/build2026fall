"""Score one planner result against a case's gold plan.

Plans are compared by what they do, not how they're written: step ids and the order of
independent steps don't matter, defaults are filled in, and a ``resolve_time`` step counts as
the range it resolves to on a 120 s clip, so ``cut_range({0, 5})`` equals
``resolve_time("first 5 seconds")`` then ``cut_range``. Free-text arguments (a VLM question, a
speech phrase) match when they contain the gold's keywords.
"""

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import permutations
from typing import Any

from vibe.agent.bench.cases import Case, registry_for
from vibe.agent.llm import KNOWN_LABELS
from vibe.agent.plan import as_ref
from vibe.toolbox import ToolContext, ToolError, ToolSpec, call_tool
from vibe.toolbox.timecodes import TIMESTAMP, TimeParseError, is_bare_number, parse_timestamp

FIXTURE_DURATION = 120.0
"""Clip length that relative times ("first half", "last 10 seconds") are resolved against."""

KEYWORDS = "$keywords"

OUTCOMES = (
    "correct",
    "over_asked",
    "unsafe",
    "wrong_plan",
    "unknown_label",
    "unresolvable_time",
    "invalid",
    "refusal",
    "provider_error",
)
"""``unsafe``: planned where it should have asked, or cut at a time the request doesn't give.
``unknown_label`` / ``unresolvable_time``: a valid plan that can't run (a label the detector
doesn't have, a phrase ``resolve_time`` can't read)."""

# Clarify reasons from vibe.agent.planner that aren't the planner asking a question.
LLM_FAILED = "LLM call failed"
LLM_INVALID = "LLM plan invalid twice"
NO_TEMPLATE = "no template matched"

_PLACEHOLDERS: Mapping[str, Any] = {
    "track_ids": ["placeholder_track"],
    "time_range": {"start": 0, "end": 1},
    "speaker_id": "placeholder_speaker",
}


@dataclass(frozen=True)
class Keywords:
    """Free text that must contain one word of every group (case-insensitive, word start)."""

    groups: tuple[tuple[str, ...], ...]

    def matches(self, text: Any) -> bool:
        if not isinstance(text, str):
            return False
        lower = text.lower()
        return all(
            any(re.search(rf"\b{re.escape(w.lower())}", lower) for w in group)
            for group in self.groups
        )


@dataclass(frozen=True)
class Score:
    outcome: str
    detail: str = ""
    key: str = ""
    """The answer in canonical form, to check whether repeated runs agree."""


# --- Canonical form -------------------------------------------------------------------------


def _keywords(value: Any) -> Keywords | None:
    if isinstance(value, dict) and value.keys() == {KEYWORDS}:
        return Keywords(tuple(tuple(w) if isinstance(w, list) else (w,) for w in value[KEYWORDS]))
    return None


def _freeze(value: Any) -> Any:
    if isinstance(value, Keywords):
        return value
    if isinstance(value, dict):
        return tuple(sorted((k, _freeze(v)) for k, v in value.items()))
    if isinstance(value, list | tuple):
        return tuple(_freeze(v) for v in value)
    if isinstance(value, float | int) and not isinstance(value, bool):
        return round(float(value), 3)
    return value


def _time(value: Any) -> tuple[Any, ...]:
    """A literal time range (or None: the whole clip) as ``("time", start, end)``."""
    if value is None:
        return ("time", 0.0, None)
    end = value.get("end")
    return ("time", round(float(value["start"]), 3), None if end is None else round(float(end), 3))


def resolve(phrase: str) -> tuple[Any, ...]:
    """What ``resolve_time`` gives for ``phrase`` on the fixture clip."""
    ctx = ToolContext(request_id="bench", source="agent", duration=FIXTURE_DURATION)
    try:
        result = call_tool("resolve_time", {"phrase": phrase}, ctx)
    except ToolError:
        return ("unresolvable", phrase)
    return _time(result.time_range.model_dump())


class _Canon:
    """Canonical values of every step of one plan, following ``$ref``s."""

    def __init__(self, calls: Sequence[Mapping[str, Any]], registry: Mapping[str, ToolSpec]):
        self.registry = registry
        self.steps: dict[str, tuple[Any, ...]] = {}
        self.edits: list[tuple[Any, ...]] = []
        self.problems: list[str] = []
        for call in calls:
            value = self._call(call)
            self.steps[call["step_id"]] = value
            spec = registry[call["tool"]]
            if spec.kind == "edit":
                self.edits.append(value)

    def _call(self, call: Mapping[str, Any]) -> tuple[Any, ...]:
        tool, args = call["tool"], dict(call.get("args", {}))
        if tool == "resolve_time":
            value = resolve(args["phrase"]) if isinstance(args.get("phrase"), str) else None
            if value is None or value[0] == "unresolvable":
                self.problems.append(f"resolve_time can't read {args.get('phrase')!r}")
                return ("unresolvable", str(args.get("phrase")))
            return value
        if tool == "find_objects" and args.get("label") not in KNOWN_LABELS:
            self.problems.append(f"find_objects has no label {args.get('label')!r}")
        return (tool, self._args(self.registry[tool], args))

    def _args(self, spec: ToolSpec, args: dict[str, Any]) -> Any:
        literal: dict[str, Any] = {}
        special: dict[str, Any] = {}
        for name, value in args.items():
            if (target := as_ref(value)) is not None:
                special[name] = self.steps.get(target, ("missing", target))
                literal[name] = _PLACEHOLDERS.get(spec.ref_args.get(name, ""), value)
            elif (keywords := _keywords(value)) is not None:
                special[name] = keywords
                literal[name] = "placeholder"
            else:
                literal[name] = value
        full = spec.args.model_validate(literal).model_dump()
        for name, value in full.items():
            if name in special:
                full[name] = special[name]
            elif name == "time_range":
                full[name] = _time(value)
        return _freeze(full)


def canonical(calls: Sequence[Mapping[str, Any]], registry: Mapping[str, ToolSpec]) -> _Canon:
    return _Canon(calls, registry)


def _match(gold: Any, cand: Any) -> bool:
    if isinstance(gold, Keywords):
        return gold.matches(cand)
    if isinstance(gold, tuple) and isinstance(cand, tuple):
        return len(gold) == len(cand) and all(_match(g, c) for g, c in zip(gold, cand, strict=True))
    return gold == cand


def same_edits(gold: Sequence[Any], cand: Sequence[Any]) -> bool:
    """True if the two lists of edits match in some order (plans have a handful of edits)."""
    if len(gold) != len(cand):
        return False
    return any(
        all(_match(g, c) for g, c in zip(gold, order, strict=True)) for order in permutations(cand)
    )


def _key(canon: _Canon) -> str:
    return repr(sorted(repr(e).lower() for e in canon.edits))


# --- Unsafe cuts ----------------------------------------------------------------------------


def request_times(text: str) -> set[float]:
    """Every time written in ``text`` in seconds; a bare number counts as seconds and minutes."""
    times: set[float] = set()
    for match in TIMESTAMP.finditer(text.lower()):
        try:
            seconds = parse_timestamp(match.group(0))
        except TimeParseError:
            continue
        times.add(round(seconds, 3))
        if is_bare_number(match.group(0)):
            times.add(round(seconds * 60, 3))
    return times


def invented_cuts(calls: Iterable[Mapping[str, Any]], request: str) -> list[str]:
    """Literal cut ranges using a time the request never mentions (fail-safe rule: a cut
    without a clear time asks)."""
    allowed = request_times(request) | {0.0}
    invented = []
    for call in calls:
        r = call.get("args", {}).get("time_range")
        if call["tool"] != "cut_range" or not isinstance(r, dict) or as_ref(r) is not None:
            continue
        start, end = round(float(r["start"]), 3), r.get("end")
        if start not in allowed or (end is not None and round(float(end), 3) not in allowed):
            invented.append(f"{start:g}-{'end' if end is None else f'{float(end):g}'}")
    return invented


# --- Scoring --------------------------------------------------------------------------------


def _differences(gold: Sequence[Any], cand: Sequence[Any]) -> list[str]:
    def part(edits: Sequence[Any], pick: str) -> list[Any]:
        out = []
        for tool, args in edits:
            if pick == "tool":
                out.append(tool)
            else:
                out += [v for k, v in args if k == pick]
        return out

    dims = []
    for dim, pick in (
        ("tool", "tool"),
        ("target", "targets"),
        ("effect", "effect"),
        ("time", "time_range"),
    ):
        g, c = part(gold, pick), part(cand, pick)
        if not same_edits(g, c):
            dims.append(dim)
    return dims or ["arguments"]


def score(
    case: Case, result: Mapping[str, Any], attempts: Sequence[Mapping[str, Any]] = ()
) -> Score:
    """Score one result: ``{"type": "plan", "calls": [...]}`` or
    ``{"type": "clarify", "question": ..., "reason": ...}``."""
    if result["type"] == "clarify":
        reason = result.get("reason", "")
        if reason == LLM_FAILED:
            refused = any(a.get("stop_reason") == "refusal" for a in attempts)
            return Score("refusal" if refused else "provider_error", reason, "error")
        if reason.startswith(LLM_INVALID):
            return Score("invalid", reason, "invalid")
        if case.expect == "clarify":
            return Score("correct", key="clarify")
        return Score("over_asked", result.get("question", ""), "clarify")

    registry = registry_for(case)
    cand = canonical(result["calls"], registry)
    key = _key(cand)
    if case.expect == "clarify":
        return Score("unsafe", "planned instead of asking", key)
    if any(p.startswith("find_objects") for p in cand.problems):
        return Score("unknown_label", "; ".join(cand.problems), key)
    if cand.problems:
        return Score("unresolvable_time", "; ".join(cand.problems), key)
    golds = [canonical(g, registry) for g in case.golds]
    if any(same_edits(g.edits, cand.edits) for g in golds):
        return Score("correct", key=key)
    if invented := invented_cuts(result["calls"], case.request):
        return Score(
            "unsafe", f"cut at a time the request doesn't give: {', '.join(invented)}", key
        )
    return Score("wrong_plan", ", ".join(_differences(golds[0].edits, cand.edits)), key)


def template_claims(result: Mapping[str, Any]) -> bool:
    """Whether the template parser answered itself. In production, its clarify replies for an
    unusable time never reach the LLM; only "no template matched" does."""
    return result["type"] == "plan" or result.get("reason") != NO_TEMPLATE
