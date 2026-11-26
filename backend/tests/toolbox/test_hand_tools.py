"""F-41: the hand tools the UI calls (``confirm_mask``). No GPU, no network, no video files."""

import pytest

from vibe.agent import Plan, ToolCall, execute_plan, validate_plan
from vibe.contract import AddMask, ConfirmMask, Effect, OpLog, TimeRange, Undo
from vibe.toolbox import HAND_TOOLS, TOOLS, UI_TOOLS, ToolContext, ToolError, call_tool


def masked_log() -> OpLog:
    log = OpLog()
    log.append(
        AddMask(
            request_id="r_1",
            source="agent",
            targets=["trk_1"],
            effect=Effect(type="blur"),
            time_range=TimeRange(start=2, end=6),
        )
    )
    return log


def ctx(log: OpLog | None) -> ToolContext:
    return ToolContext(request_id="r_2", source="user", log=log, duration=60.0)


def confirm_args(*targets: str, start: float = 2, end: float | None = 6) -> dict[str, object]:
    return {"targets": list(targets), "time_range": {"start": start, "end": end}}


def test_confirm_writes_one_op_and_the_state_covers_it() -> None:
    log = masked_log()
    result = call_tool("confirm_mask", confirm_args("trk_1"), ctx(log), UI_TOOLS)
    assert len(result.op_ids) == 1
    assert isinstance(log.ops[-1], ConfirmMask)
    assert log.ops[-1].source == "user"
    [span] = log.state().reviewed["trk_1"]
    assert (span.start, span.end) == (2, 6)


def test_duplicate_targets_are_dropped() -> None:
    log = masked_log()
    call_tool("confirm_mask", confirm_args("trk_1", "trk_1"), ctx(log), UI_TOOLS)
    assert log.ops[-1].targets == ["trk_1"]  # type: ignore[union-attr]


def test_an_unmasked_target_is_refused_and_nothing_is_written() -> None:
    log = masked_log()
    before = len(log.ops)
    with pytest.raises(ToolError, match="trk_9"):
        call_tool("confirm_mask", confirm_args("trk_1", "trk_9"), ctx(log), UI_TOOLS)
    assert len(log.ops) == before


def test_a_range_outside_the_mask_is_refused() -> None:
    log = masked_log()
    with pytest.raises(ToolError, match="trk_1"):
        call_tool("confirm_mask", confirm_args("trk_1", start=7, end=9), ctx(log), UI_TOOLS)
    assert len(log.ops) == 1


def test_a_range_that_only_partly_overlaps_the_mask_is_accepted() -> None:
    log = masked_log()
    call_tool("confirm_mask", confirm_args("trk_1", start=1.9, end=3), ctx(log), UI_TOOLS)
    assert isinstance(log.ops[-1], ConfirmMask)


def test_it_needs_a_timeline() -> None:
    with pytest.raises(ToolError, match="timeline"):
        call_tool("confirm_mask", confirm_args("trk_1"), ctx(None), UI_TOOLS)


def test_extra_arguments_are_refused() -> None:
    args = {**confirm_args("trk_1"), "effect": {"type": "blur"}}
    with pytest.raises(ToolError, match="bad arguments"):
        call_tool("confirm_mask", args, ctx(masked_log()), UI_TOOLS)


def test_only_the_ui_registry_has_it() -> None:
    assert "confirm_mask" not in TOOLS
    assert "confirm_mask" in HAND_TOOLS
    assert "confirm_mask" in UI_TOOLS
    assert set(UI_TOOLS) == set(TOOLS) | {"confirm_mask", "mask_targets"}
    with pytest.raises(ToolError, match="unknown tool"):
        call_tool("confirm_mask", confirm_args("trk_1"), ctx(masked_log()))


def plan() -> Plan:
    return Plan(calls=(ToolCall(step_id="s1", tool="confirm_mask", args=confirm_args("trk_1")),))


def test_a_literal_target_plan_validates_with_ui_tools_only() -> None:
    assert validate_plan(plan(), UI_TOOLS) == []
    errors = validate_plan(plan(), TOOLS)
    assert any("unknown tool 'confirm_mask'" in e for e in errors)


def test_execute_plan_writes_it_as_one_request_and_undo_removes_it() -> None:
    log = masked_log()
    done = execute_plan(plan(), log, request_id="r_2", source="user", registry=UI_TOOLS)
    assert done.ok
    assert [op.request_id for op in done.ops] == ["r_2"]
    assert "trk_1" in log.state().reviewed
    log.append(Undo(request_id="r_3", source="user", undo_request_id="r_2"))
    assert log.state().reviewed == {}
