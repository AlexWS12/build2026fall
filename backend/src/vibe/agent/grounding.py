"""Cuts the LLM planned at times the user never gave (F-05's open item, R-1b).

A cut loses footage, so the LLM may only cut where the user's own words say: a time they wrote
("0:10 to 0:20", "after 1:30") or a phrase ``resolve_time`` turns into one ("the last third").
Any other cut or keep, such as "cut the boring part" planned as 0:30 to 0:50, is held back:
the planner asks the user to confirm it in words the template parser takes, so nothing is cut
on the LLM's guess alone. The template parser's own plans are exempt: they only use what was
written.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from vibe.agent.plan import Plan, as_ref
from vibe.agent.templates import normalize
from vibe.toolbox.timecodes import TS, TimeParseError, is_bare_number, parse_timestamp

CUT_TOOLS = frozenset({"cut_range", "keep_range"})
"""Edits that remove footage: a keep cuts everything around its range."""

TOLERANCE_S = 0.05
"""How close a planned time must be to a written one to count as the same."""

_TIME = re.compile(rf"(?<![\w:.])({TS})")


@dataclass(frozen=True)
class InventedCut:
    tool: str
    """``cut_range`` or ``keep_range``."""
    when: str
    """The range in the user's terms: "0:10 to 0:20", "after 1:30", "the last third"."""


def written_times(text: str) -> list[float]:
    """Seconds for every time written in ``text`` with a unit or a colon ("10 seconds",
    "1:30"). A bare number ("cut 10 to 20") could be seconds or minutes, so it doesn't count."""
    times = []
    for match in _TIME.finditer(normalize(text)):
        written = match.group(1).strip()
        if is_bare_number(written):
            continue
        try:
            times.append(parse_timestamp(written))
        except TimeParseError:
            continue
    return times


def clock(seconds: float) -> str:
    """``seconds`` as the template parser reads it: 70 -> "1:10", 65.5 -> "1:05.5"."""
    whole, frac = divmod(round(seconds, 3), 1)
    h, rest = divmod(int(whole), 3600)
    m, s = divmod(rest, 60)
    tail = f"{frac:.3f}".lstrip("0").rstrip("0").rstrip(".") if frac else ""
    return f"{h}:{m:02d}:{s:02d}{tail}" if h else f"{m}:{s:02d}{tail}"


def _span(time_range: Mapping[str, Any]) -> str:
    start, end = float(time_range["start"]), time_range.get("end")
    if end is None:
        return f"after {clock(start)}"
    return f"{clock(start)} to {clock(float(end))}"


def _phrase_written(phrase: str, text: str) -> bool:
    """Whether the user said ``phrase`` (a ``resolve_time`` phrase) word for word."""
    phrase = re.sub(r"^the ", "", normalize(phrase))
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", normalize(text)) is not None


def _phrase(phrase: str) -> str:
    """A ``resolve_time`` phrase as the user would say it after "cut": "the last third"."""
    phrase = re.sub(r"^the ", "", normalize(phrase))
    return f"the {phrase}" if re.match(r"(?:first|second|middle|last)\b", phrase) else phrase


def invented_cuts(request: str, plan: Plan) -> list[InventedCut]:
    """The plan's cuts and keeps whose times the user didn't give."""
    written = written_times(request)
    phrases = {
        call.step_id: call.args.get("phrase")
        for call in plan.calls
        if call.tool == "resolve_time" and isinstance(call.args.get("phrase"), str)
    }
    out = []
    for call in plan.calls:
        if call.tool not in CUT_TOOLS:
            continue
        time_range = call.args.get("time_range")
        if (step := as_ref(time_range)) is not None:
            phrase = phrases.get(step)
            if phrase is None or not _phrase_written(phrase, request):
                out.append(InventedCut(call.tool, _phrase(phrase) if phrase else "that part"))
            continue
        if not isinstance(time_range, Mapping):
            continue  # validate_plan has already refused a cut without a range
        ends = [float(time_range["start"])]
        if time_range.get("end") is not None:
            ends.append(float(time_range["end"]))
        given = all(
            (t == 0 and i == 0) or any(abs(t - w) <= TOLERANCE_S for w in written)
            for i, t in enumerate(ends)
        )
        if not given:
            out.append(InventedCut(call.tool, _span(time_range)))
    return out


def confirm_question(invented: list[InventedCut], plan: Plan) -> str:
    """The question for the user: what would be cut, and the words that do it for real."""
    sayings = [
        f"keep only {cut.when}" if cut.tool == "keep_range" else f"cut {cut.when}"
        for cut in invented
    ]
    what = " and ".join(
        f"keep only {cut.when} and cut the rest" if cut.tool == "keep_range" else f"cut {cut.when}"
        for cut in invented
    )
    other_edits = [
        c for c in plan.calls if c.tool not in CUT_TOOLS | {"resolve_time", "find_objects"}
    ]
    rest = " Then ask for the other edits again." if other_edits else ""
    return (
        f"I'd {what}, but you didn't give that time, so it's my guess. Is it right? "
        f"To go ahead, say '{' and '.join(sayings)}'.{rest}"
    )
