"""F-14: ``mask_targets``, the hand tool for a clicked or boxed target. No GPU, no video."""

import pytest

from vibe.agent import Plan, ToolCall, execute_plan, validate_plan
from vibe.contract import OpLog, Track, Undo
from vibe.toolbox import HAND_TOOLS, TOOLS, UI_TOOLS, ToolContext, ToolError, call_tool


def track(track_id: str, first: float, last: float) -> Track:
    box = (0.2, 0.2, 0.4, 0.4)
    keyframes = [{"t": t, "box": box, "conf": 1.0, "state": "user"} for t in (first, last)]
    return Track.model_validate(
        {"track_id": track_id, "label": "object", "source": "user", "keyframes": keyframes}
    )


def ctx(log: OpLog | None, tracks: list[Track] | None = None) -> ToolContext:
    return ToolContext(request_id="r_1", source="user", log=log, duration=60.0, tracks=tracks)


def test_it_masks_literal_track_ids_like_add_mask() -> None:
    log = OpLog()
    call_tool("mask_targets", {"targets": ["usr_1"], "effect": {"type": "box"}}, ctx(log), UI_TOOLS)
    [span] = log.state().masks["usr_1"]
    assert (span.start, span.end, span.effect.type) == (0, None, "box")
    assert [op.source for op in log] == ["user"]


def test_while_tracked_follows_the_track() -> None:
    log = OpLog()
    args = {"targets": ["usr_1"], "effect": {"type": "blur"}, "while_tracked": True}
    call_tool("mask_targets", args, ctx(log, [track("usr_1", 10, 20)]), UI_TOOLS)
    [span] = log.state().masks["usr_1"]
    assert 9 <= span.start < 10 and 20 < span.end <= 21


def test_while_tracked_needs_the_track() -> None:
    args = {"targets": ["usr_9"], "effect": {"type": "blur"}, "while_tracked": True}
    with pytest.raises(ToolError, match="no track 'usr_9'"):
        call_tool("mask_targets", args, ctx(OpLog(), [track("usr_1", 0, 1)]), UI_TOOLS)


def test_only_the_ui_registry_has_it_and_the_planner_cant_use_it() -> None:
    assert "mask_targets" in HAND_TOOLS and "mask_targets" not in TOOLS
    plan = Plan(
        calls=(
            ToolCall(
                step_id="s1",
                tool="mask_targets",
                args={"targets": ["x"], "effect": {"type": "blur"}},
            ),
        )
    )
    assert validate_plan(plan, UI_TOOLS) == []
    assert any("unknown tool 'mask_targets'" in e for e in validate_plan(plan, TOOLS))


def test_undo_takes_it_back() -> None:
    log = OpLog()
    plan = Plan(
        calls=(
            ToolCall(
                step_id="s1",
                tool="mask_targets",
                args={"targets": ["usr_1"], "effect": {"type": "blur"}},
            ),
        )
    )
    assert execute_plan(plan, log, request_id="r_1", source="user", registry=UI_TOOLS).ok
    log.append(Undo(request_id="r_2", source="user", undo_request_id="r_1"))
    assert log.state().masks == {}
