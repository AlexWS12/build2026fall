"""Score one planner result against a case's gold plan.

Plans are compared by what they do, not how they're written: step ids and the order of
independent steps don't matter, defaults are filled in, and a ``resolve_time`` step counts as
the range it resolves to on a 120 s clip, so ``cut_range({0, 5})`` equals
``resolve_time("first 5 seconds")`` then ``cut_range``. A ``keep_range`` counts as the cuts it
makes on that clip. Free-text arguments (a VLM question, a speech phrase) match when they
contain the gold's keywords.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import permutations
from typing import Any

from vibe.agent.bench.cases import Case, registry_for
from vibe.agent.llm import KNOWN_LABELS
from vibe.agent.plan import as_ref
from vibe.toolbox import ToolContext, ToolError, ToolSpec, call_tool

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
"""``unsafe``: planned where it should have asked, a cut the gold plan doesn't have (a keep
counts as its cuts), or a mask that leaves visible what the gold plan hides (privacy fails
closed). ``unknown_label`` / ``unresolvable_time``: a valid plan that can't run (a label the
detector doesn't have, a phrase ``resolve_time`` can't read)."""

# Clarify reasons from vibe.agent.planner that aren't the planner asking a question.
LLM_FAILED = "LLM call failed"
LLM_INVALID = "LLM plan invalid twice"
NO_TEMPLATE = "no template matched"

EPSILON = 0.001
"""Seconds: times are compared rounded to the millisecond."""

WIDER_LABELS: Mapping[str, frozenset[str]] = {"person": frozenset({"face"})}
"""A person's box covers their face, so masking people also hides the faces."""

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
                self.edits.extend(_as_cuts(value) if call["tool"] == "keep_range" else [value])

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


def _is_time(value: Any) -> bool:
    return isinstance(value, tuple) and len(value) == 3 and value[0] == "time"


def _as_cuts(keep: tuple[Any, ...]) -> list[tuple[Any, ...]]:
    """``keep_range`` as the ``cut_range`` edits it writes on the fixture clip: before and after
    the kept part. A range it can't read stays a ``keep_range``, so it can't match a cut."""
    span = dict(keep[1]).get("time_range")
    if not _is_time(span):
        return [keep]
    _, start, end = span
    cuts = []
    if start > EPSILON:
        cuts.append(("time", 0.0, start))
    if end is not None and end < FIXTURE_DURATION - EPSILON:
        cuts.append(("time", end, None))
    return [("cut_range", (("time_range", c),)) for c in cuts]


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


def _extra_cuts(cand: _Canon, golds: Sequence[_Canon]) -> list[Any]:
    """Cuts the closest gold plan doesn't have: footage the user didn't ask to lose. A cut left
    out only keeps footage, so it's a wrong plan but not an unsafe one."""

    def cuts(plan: _Canon) -> list[Any]:
        return [edit for edit in plan.edits if edit[0] == "cut_range"]

    return min(
        ([c for c in cuts(cand) if not any(_match(g, c) for g in cuts(gold))] for gold in golds),
        key=len,
    )


def _root(targets: Any) -> tuple[str | None, bool]:
    """The detector label a mask's targets start from, and whether a step (``check_attribute``)
    narrows them down. ``(None, True)`` when there's no label to compare."""
    if not (isinstance(targets, tuple) and len(targets) == 2 and isinstance(targets[1], tuple)):
        return None, True
    tool, args = targets
    if tool == "find_objects":
        return dict(args).get("label"), False
    if tool == "check_attribute":
        label, _ = _root(dict(args).get("targets"))
        return label, True
    return None, True


def _hides(cand: Any, gold: Any) -> bool:
    """Whether masking the ``cand`` targets covers the ``gold`` targets: the same or a wider
    label, and no filter the gold plan doesn't have. Two different filters ("wearing red?" vs
    "red hoodie?") aren't proof that anyone is left visible, so that's a wrong plan, not this."""
    if _match(gold, cand):
        return True
    cand_label, cand_narrowed = _root(cand)
    gold_label, gold_narrowed = _root(gold)
    if cand_label is None or gold_label is None or (cand_narrowed and not gold_narrowed):
        return False
    return cand_label == gold_label or gold_label in WIDER_LABELS.get(cand_label, ())


def _span(time: tuple[Any, ...]) -> tuple[float, float]:
    return time[1], FIXTURE_DURATION if time[2] is None else time[2]


def _covered(span: tuple[float, float], spans: Sequence[tuple[float, float]]) -> bool:
    start, end = span
    for s, e in sorted(spans):
        if s > start + EPSILON:
            break
        start = max(start, e)
    return start >= end - EPSILON


def _exposed(cand: _Canon, golds: Sequence[_Canon]) -> list[dict[str, Any]]:
    """Masks in the closest gold plan that the candidate's masks don't fully cover, by target
    and time: something the user asked to hide would stay visible."""

    def masks(plan: _Canon) -> list[dict[str, Any]]:
        return [dict(args) for tool, args in plan.edits if tool == "add_mask"]

    def exposed(gold: _Canon) -> list[dict[str, Any]]:
        out = []
        for g in masks(gold):
            spans = [
                _span(m["time_range"])
                for m in masks(cand)
                if _is_time(m.get("time_range")) and _hides(m.get("targets"), g.get("targets"))
            ]
            if not _is_time(g.get("time_range")) or not _covered(_span(g["time_range"]), spans):
                out.append(g)
        return out

    return min((exposed(gold) for gold in golds), key=len)


def _target_name(targets: Any) -> str:
    label, narrowed = _root(targets)
    return f"some {label}" if narrowed and label else str(label)


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
    if extra := _extra_cuts(cand, golds):
        ranges = ", ".join(str(dict(args)["time_range"]) for _, args in extra)
        return Score("unsafe", f"a cut the gold plan doesn't have: {ranges}", key)
    if exposed := _exposed(cand, golds):
        hidden = ", ".join(
            f"{_target_name(m.get('targets'))} {m.get('time_range')}" for m in exposed
        )
        return Score("unsafe", f"leaves visible what the gold plan hides: {hidden}", key)
    return Score("wrong_plan", ", ".join(_differences(golds[0].edits, cand.edits)), key)


def template_claims(result: Mapping[str, Any]) -> bool:
    """Whether the template parser answered itself. In production, its clarify replies for an
    unusable time never reach the LLM; only "no template matched" does."""
    return result["type"] == "plan" or result.get("reason") != NO_TEMPLATE
