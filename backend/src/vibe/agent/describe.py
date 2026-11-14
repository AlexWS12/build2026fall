"""What a request did, in plain words, for the chat (docs/ARCHITECTURE.md, "Human in the loop").

Deterministic and built only from the steps that ran: one sentence per edit, with the numbers
the tools returned, so a reply never claims an edit that isn't in the timeline.
"""

from typing import Any

from vibe.agent.execute import Execution, StepReport
from vibe.agent.plan import Plan, as_ref
from vibe.toolbox import TOOLS

_NOUNS = {
    "face": ("face", "faces"),
    "person": ("person", "people"),
    "license_plate": ("license plate", "license plates"),
}
_MASKED = {"blur": "Blurred", "pixelate": "Pixelated", "box": "Covered"}


def clock(seconds: float) -> str:
    """Seconds as M:SS, with tenths only when there are some: 5 -> "0:05", 83.5 -> "1:23.5"."""
    whole, tenth = divmod(round(seconds * 10), 10)
    minutes, secs = divmod(whole, 60)
    text = f"{minutes}:{secs:02d}"
    return f"{text}.{tenth}" if tenth else text


def _when(time_range: dict[str, Any] | None) -> str:
    """A resolved ``time_range`` argument: "0:10–0:20", "0:30 to the end", "the whole video"."""
    if time_range is None:
        return "the whole video"
    start, end = time_range["start"], time_range.get("end")
    if end is None:
        return "the whole video" if start <= 0 else f"{clock(start)} to the end"
    return f"{clock(start)}–{clock(end)}"


def _counted(label: str | None, count: int) -> str:
    """ "2 faces", "1 person"; a label with no plain name stays as the detector wrote it."""
    if label is None:
        return f"{count} {'target' if count == 1 else 'targets'}"
    one, many = _NOUNS.get(label, (f"{label!r} track", f"{label!r} tracks"))
    return f"{count} {one if count == 1 else many}"


def _label(plan: Plan, step: StepReport, steps: dict[str, StepReport]) -> str | None:
    """The label a mask's targets were found by, if they came from a ``find_objects`` step."""
    call = next(c for c in plan.calls if c.step_id == step.step_id)
    found = steps.get(as_ref(call.args.get("targets")) or "")
    return found.args.get("label") if found is not None and found.tool == "find_objects" else None


def _sentence(plan: Plan, step: StepReport, steps: dict[str, StepReport]) -> str:
    args = step.args
    when = _when(args.get("time_range"))
    match step.tool:
        case "add_mask":
            what = _counted(_label(plan, step, steps), len(args["targets"]))
            effect = args["effect"]["type"]
            tail = " with a black box" if effect == "box" else ""
            return f"{_MASKED.get(effect, 'Hid')} {what}{tail} ({when})."
        case "cut_range":
            return f"Cut {when}."
        case "keep_range":
            return f"Kept only {when}; the rest is cut."
        case "mute" | "unmute":
            verb = "Muted" if step.tool == "mute" else "Unmuted"
            who = f" speaker {args['speaker']}" if args.get("speaker") else ""
            still = (step.result or {}).get("still_muted") or []
            more = f" Still muted there: {', '.join(still)}." if still else ""
            return f"{verb}{who} {when}.{more}"
        case "denoise":
            return f"Added noise reduction to {when}."
        case "undo":
            return "Undid the last edit."
        case "redo":
            return "Brought back the last undone edit."
    return f"Ran {step.tool}."


def describe(plan: Plan, done: Execution) -> str:
    """One sentence per edit step of a plan that ran, e.g. "Muted 0:00–0:05."."""
    steps = {s.step_id: s for s in done.steps}
    return " ".join(
        _sentence(plan, s, steps) for s in done.steps if s.ok and TOOLS[s.tool].kind == "edit"
    )


def explain_failure(plan: Plan, done: Execution) -> str:
    """Why nothing was changed: the refusing tool's reason, without its name in front."""
    step = done.steps[-1]
    steps = {s.step_id: s for s in done.steps}
    if step.tool == "add_mask" and step.args.get("targets") == []:
        # Analysed, and nothing with that label is there: say so instead of "bad arguments".
        what = _counted(_label(plan, step, steps), 2).removeprefix("2 ")
        return (
            f"I found no {what} in this video, so there was nothing to hide. Nothing was changed."
        )
    reason = (done.error or "the edit was refused").removeprefix(f"{step.tool}: ")
    if reason[-1] not in ".?!":
        reason += "."
    return f"I couldn't do that: {reason} Nothing was changed."
