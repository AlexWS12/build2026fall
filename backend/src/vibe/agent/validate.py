"""Check a plan against the tool registry before anything runs.

Used on template plans (an error there is a bug) and on LLM plans (the error goes back to the
LLM for one retry, then the user is asked). Returns readable errors, never raises on bad input.
"""

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from vibe.agent.plan import Plan, ToolCall, as_ref
from vibe.toolbox import Returns, ToolSpec

_PLACEHOLDERS: Mapping[Returns, Any] = {
    "track_ids": ["placeholder_track"],
    "time_range": {"start": 0, "end": 1},
}
"""Stand-ins for a ``$ref`` so the rest of the arguments can be validated by the tool's model."""


def validate_plan(plan: Plan, registry: Mapping[str, ToolSpec]) -> list[str]:
    """All the problems with ``plan``. An empty list means it's valid."""
    errors: list[str] = []
    returns_by_step: dict[str, Returns] = {}
    for call in plan.calls:
        if call.step_id in returns_by_step:
            errors.append(f"duplicate step_id {call.step_id!r}")
        spec = registry.get(call.tool)
        if spec is None:
            errors.append(f"{call.step_id}: unknown tool {call.tool!r}")
            returns_by_step[call.step_id] = "none"
            continue
        errors += _check_call(call, spec, returns_by_step)
        returns_by_step[call.step_id] = spec.returns
    if not any(registry[c.tool].kind == "edit" for c in plan.calls if c.tool in registry):
        errors.append("plan has no edit call, so it would change nothing")
    return errors


def _check_call(call: ToolCall, spec: ToolSpec, earlier: Mapping[str, Returns]) -> list[str]:
    errors: list[str] = []
    args = dict(call.args)
    for name in sorted(spec.ref_required):
        if name in call.args and as_ref(call.args[name]) is None:
            errors.append(
                f"{call.step_id} ({spec.name}): {name!r} must be a $ref to an earlier step"
            )
    for name, value in call.args.items():
        target = as_ref(value)
        if target is None:
            continue
        wanted = spec.ref_args.get(name)
        if wanted is None:
            errors.append(f"{call.step_id} ({spec.name}): argument {name!r} can't be a $ref")
        elif target not in earlier:
            errors.append(f"{call.step_id} ({spec.name}): $ref to {target!r}, not an earlier step")
        elif earlier[target] != wanted:
            errors.append(
                f"{call.step_id} ({spec.name}): {name!r} needs {wanted}, "
                f"but {target!r} returns {earlier[target]}"
            )
        args[name] = _PLACEHOLDERS.get(wanted or "none")
    try:
        spec.args.model_validate(args)
    except ValidationError as exc:
        details = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        errors.append(f"{call.step_id} ({spec.name}): bad arguments: {details}")
    return errors
