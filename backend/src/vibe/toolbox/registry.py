"""Every tool, by name. **Shared file:** tool names and arguments are what the planner, the API
and the UI call, so post in the team chat before changing them (HANDOFF.md, "Shared files")."""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from pydantic import ValidationError

from vibe.toolbox import tools
from vibe.toolbox.context import ToolContext
from vibe.toolbox.spec import ToolError, ToolSpec

_TOOLS = (
    ToolSpec(
        name="find_objects",
        kind="query",
        description="Find every track with a detector label. Stub: filters the given tracks.",
        args=tools.FindObjectsArgs,
        result=tools.FindObjectsResult,
        returns="track_ids",
        run=tools.find_objects,
    ),
    ToolSpec(
        name="resolve_time",
        kind="query",
        description="Turn a phrase like 'first half' into a time range, using the duration.",
        args=tools.ResolveTimeArgs,
        result=tools.ResolveTimeResult,
        returns="time_range",
        run=tools.resolve_time,
    ),
    ToolSpec(
        name="add_mask",
        kind="edit",
        description="Hide tracks with an effect (blur, pixelate, box) over a time range.",
        args=tools.AddMaskArgs,
        result=tools.EditResult,
        returns="none",
        run=tools.add_mask,
        ref_args={"targets": "track_ids", "time_range": "time_range"},
        ref_required=frozenset({"targets"}),
    ),
    ToolSpec(
        name="cut_range",
        kind="edit",
        description="Remove a time range from the output.",
        args=tools.CutRangeArgs,
        result=tools.EditResult,
        returns="none",
        run=tools.cut_range,
        ref_args={"time_range": "time_range"},
    ),
    ToolSpec(
        name="mute",
        kind="edit",
        description="Silence all audio, or one speaker, over a time range.",
        args=tools.MuteArgs,
        result=tools.EditResult,
        returns="none",
        run=tools.mute,
        ref_args={"time_range": "time_range"},
    ),
)

TOOLS: Mapping[str, ToolSpec] = MappingProxyType({t.name: t for t in _TOOLS})


def call_tool(
    name: str,
    args: Mapping[str, Any],
    ctx: ToolContext,
    registry: Mapping[str, ToolSpec] = TOOLS,
) -> Any:
    """Validate ``args`` against the tool's model and run it. Returns the tool's result model.

    Raises ``ToolError`` for an unknown tool, bad arguments, or an edit the timeline refuses.
    """
    spec = registry.get(name)
    if spec is None:
        raise ToolError(f"unknown tool {name!r}; known: {', '.join(registry)}")
    try:
        parsed = spec.args.model_validate(args)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(map(str, e['loc'])) or '(args)'}: {e['msg']}" for e in exc.errors()
        )
        raise ToolError(f"{name}: bad arguments: {details}") from None
    try:
        return spec.run(parsed, ctx)
    except ValidationError as exc:  # a tool built an invalid model: still this call's error
        raise ToolError(f"{name}: {exc}") from None
