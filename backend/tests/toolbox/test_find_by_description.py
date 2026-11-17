"""F-13: ``find_by_description``. Unsure tracks are always returned (privacy fails closed)."""

from pathlib import Path

import pytest
from pydantic import TypeAdapter

from vibe.agent import Plan, plan_request
from vibe.contract import AddMask, Keyframe, OpLog, Track
from vibe.toolbox import DescriberError, ToolContext, ToolError, call_tool

EXAMPLES = Path(__file__).parents[2] / "src" / "vibe" / "contract" / "examples"
STREET = TypeAdapter(list[Track]).validate_json((EXAMPLES / "street.tracks.json").read_bytes())


def person(n: int) -> Track:
    return STREET[0].model_copy(update={"track_id": f"trk_p{n}", "label": "person"})


TRACKS = [person(1), person(2), person(3), STREET[0].model_copy(update={"label": "face"})]
PEOPLE = ["trk_p1", "trk_p2", "trk_p3"]
ARGS = {"label": "person", "description": "in red"}


class FakeDescriber:
    def __init__(self, verdicts: dict[str, str] | None = None, error: Exception | None = None):
        self.verdicts, self.error = verdicts or {}, error
        self.asked: list[tuple[list[str], str]] = []
        self.scenes: list[list[str] | None] = []

    def ask(self, tracks, description, scene=None):
        self.asked.append(([t.track_id for t in tracks], description))
        self.scenes.append(None if scene is None else [t.track_id for t in scene])
        if self.error is not None:
            raise self.error
        return self.verdicts


def ctx(describer: FakeDescriber | None, log: OpLog | None = None, tracks=TRACKS) -> ToolContext:
    return ToolContext(
        request_id="req_1", source="agent", log=log, tracks=tracks, describer=describer
    )


def test_it_only_asks_about_tracks_with_the_label() -> None:
    describer = FakeDescriber()

    call_tool("find_by_description", ARGS, ctx(describer))

    assert describer.asked == [(PEOPLE, "in red")]
    # ... and hands over every track, so "with a bag" can look for bags.
    assert describer.scenes == [[t.track_id for t in TRACKS]]


def test_it_returns_matches_and_unsure_tracks() -> None:
    describer = FakeDescriber({"trk_p1": "yes", "trk_p2": "no"})  # p3 gets no answer: unsure

    result = call_tool("find_by_description", ARGS, ctx(describer))

    assert result.track_ids == ["trk_p1", "trk_p3"]
    assert result.unsure == ["trk_p3"]
    assert result.count == 2
    assert result.left_out == 1


def test_sure_matches_only_leaves_the_unsure_out_and_says_so() -> None:
    describer = FakeDescriber({"trk_p1": "yes", "trk_p2": "no"})  # p3: unsure

    result = call_tool("find_by_description", {**ARGS, "unsure": "leave"}, ctx(describer))

    assert result.track_ids == ["trk_p1"]
    assert result.unsure == ["trk_p3"]  # still reported, so the agent can say one was left
    assert result.left_out == 2


def test_sure_matches_only_with_nobody_sure_is_an_error() -> None:
    describer = FakeDescriber({"trk_p1": "no"})  # p2, p3: unsure

    with pytest.raises(ToolError, match="no person surely matches 'in red', and 2 might"):
        call_tool("find_by_description", {**ARGS, "unsure": "leave"}, ctx(describer))


def test_exclude_leaves_out_only_the_sure_matches() -> None:
    describer = FakeDescriber({"trk_p1": "yes", "trk_p2": "unsure", "trk_p3": "no"})

    result = call_tool("find_by_description", {**ARGS, "exclude": True}, ctx(describer))

    assert result.track_ids == ["trk_p2", "trk_p3"]
    assert result.unsure == ["trk_p2"]


def test_an_answer_that_isnt_a_verdict_counts_as_unsure() -> None:
    describer = FakeDescriber(dict.fromkeys(PEOPLE, "maybe"))

    for exclude in (False, True):
        result = call_tool("find_by_description", {**ARGS, "exclude": exclude}, ctx(describer))
        assert result.track_ids == PEOPLE
        assert result.unsure == PEOPLE


def test_no_track_with_the_label_asks_nothing() -> None:
    describer = FakeDescriber()

    result = call_tool(
        "find_by_description", {"label": "giraffe", "description": "in red"}, ctx(describer)
    )

    assert result.track_ids == [] and result.count == 0
    assert describer.asked == []


def test_it_fails_without_a_describer_or_an_analysis() -> None:
    with pytest.raises(ToolError, match="nothing here can look at the video"):
        call_tool("find_by_description", ARGS, ctx(None))
    with pytest.raises(ToolError, match="no analysis"):
        call_tool("find_by_description", ARGS, ctx(FakeDescriber(), tracks=None))


def test_a_describer_that_cant_look_is_an_error_not_an_empty_result() -> None:
    describer = FakeDescriber(error=DescriberError("no judge"))

    with pytest.raises(ToolError, match="couldn't check 'in red': no judge"):
        call_tool("find_by_description", ARGS, ctx(describer))


def test_a_blank_description_is_a_bad_argument() -> None:
    with pytest.raises(ToolError, match="bad arguments"):
        call_tool("find_by_description", {"label": "person", "description": ""}, ctx(None))


def seen(track_id: str, start: float, end: float) -> Track:
    keys = [
        Keyframe(t=t, box=(0.1, 0.1, 0.2, 0.2), conf=0.9, state="detected") for t in (start, end)
    ]
    return Track(track_id=track_id, label="person", source="detector", keyframes=keys)


SEEN = [seen("trk_a", 2.0, 4.0), seen("trk_b", 2.1, 3.9), seen("trk_c", 8.3, 9.0)]
BLUR = {"type": "blur"}


def tracked_ctx(log: OpLog, tracks=SEEN, duration: float | None = 10.0) -> ToolContext:
    return ToolContext(
        request_id="req_1", source="agent", log=log, tracks=tracks, duration=duration
    )


def spans(log: OpLog) -> list[tuple[list[str], float, float | None]]:
    return [(op.targets, op.time_range.start, op.time_range.end) for op in log.ops]


def test_a_mask_can_follow_each_target_only_while_it_is_tracked() -> None:
    log = OpLog()
    args = {"targets": ["trk_a", "trk_b", "trk_c"], "effect": BLUR, "while_tracked": True}

    result = call_tool("add_mask", args, tracked_ctx(log))

    # The renderer's half-second hold around each track, rounded outward to a quarter-second
    # grid: a and b come and go together, so they share an op.
    assert spans(log) == [(["trk_a", "trk_b"], 1.5, 4.5), (["trk_c"], 7.75, 9.5)]
    assert len(result.op_ids) == 2
    assert {op.request_id for op in log.ops} == {"req_1"}  # one request: one undo


def test_a_followed_mask_stops_at_the_end_of_the_video() -> None:
    log = OpLog()
    args = {"targets": ["trk_c"], "effect": BLUR, "while_tracked": True}

    call_tool("add_mask", args, tracked_ctx(log, duration=9.1))

    assert spans(log) == [(["trk_c"], 7.75, 9.1)]


def test_a_followed_mask_lasts_at_least_as_long_as_the_renderers_hold() -> None:
    """The mask must not stop while the renderer still holds the target's box (review of PR
    33: a quarter-second pad left a gap at each end, and at every handover between fragments).
    Rounding to the grid may add up to one grid step more, never less."""
    from vibe.render.geometry import HOLD_SECONDS
    from vibe.toolbox.tools import TRACKED_GRID_S, TRACKED_PAD_S

    assert TRACKED_PAD_S >= HOLD_SECONDS  # change the renderer's hold, change the pad too

    log = OpLog()
    tracks = [seen(f"trk_{i}", 1 + i * 0.013, 2 + i * 0.017) for i in range(40)]
    args = {"targets": [t.track_id for t in tracks], "effect": BLUR, "while_tracked": True}

    call_tool("add_mask", args, tracked_ctx(log, tracks=tracks))

    by_id = {t.track_id: t for t in tracks}
    for op in log.ops:
        for track_id in op.targets:
            first, last = by_id[track_id].keyframes[0].t, by_id[track_id].keyframes[-1].t
            assert HOLD_SECONDS <= first - op.time_range.start < HOLD_SECONDS + TRACKED_GRID_S
            assert HOLD_SECONDS <= op.time_range.end - last < HOLD_SECONDS + TRACKED_GRID_S


def test_a_followed_mask_stays_inside_the_requested_range() -> None:
    log = OpLog()
    args = {
        "targets": ["trk_a", "trk_c"],
        "effect": BLUR,
        "time_range": {"start": 3.0, "end": 6.0},
        "while_tracked": True,
    }

    call_tool("add_mask", args, tracked_ctx(log))

    assert spans(log) == [(["trk_a"], 3.0, 4.5)]  # c isn't on screen between 3 s and 6 s


def test_a_followed_mask_with_nothing_on_screen_is_an_error() -> None:
    log = OpLog()
    args = {
        "targets": ["trk_c"],
        "effect": BLUR,
        "time_range": {"start": 5.0, "end": 6.0},
        "while_tracked": True,
    }

    with pytest.raises(ToolError, match="none of the targets is on screen"):
        call_tool("add_mask", args, tracked_ctx(log))
    assert not log.ops


def test_a_followed_mask_needs_the_tracks() -> None:
    args = {"targets": ["trk_a"], "effect": BLUR, "while_tracked": True}

    with pytest.raises(ToolError, match="no analysis"):
        call_tool("add_mask", args, tracked_ctx(OpLog(), tracks=None))
    with pytest.raises(ToolError, match="no track 'trk_zz'"):
        call_tool("add_mask", {**args, "targets": ["trk_zz"]}, tracked_ctx(OpLog()))


def test_a_plain_mask_is_still_one_op_for_the_whole_range() -> None:
    log = OpLog()

    call_tool("add_mask", {"targets": ["trk_a", "trk_c"], "effect": BLUR}, tracked_ctx(log))

    [op] = log.ops
    assert op.targets == ["trk_a", "trk_c"] and op.time_range is None


def test_one_described_person_is_followed_from_the_template_to_the_masks() -> None:
    plan = plan_request("blur the person in red, only if sure")
    assert isinstance(plan, Plan)
    log = OpLog()
    describer = FakeDescriber({"trk_a": "yes", "trk_b": "unsure", "trk_c": "yes"})
    context = ToolContext(
        request_id="req_1",
        source="agent",
        log=log,
        tracks=SEEN,
        duration=10.0,
        describer=describer,
    )

    found = call_tool(plan.calls[0].tool, plan.calls[0].args, context)
    call_tool("add_mask", {**plan.calls[1].args, "targets": found.track_ids}, context)

    assert found.track_ids == ["trk_a", "trk_c"] and found.unsure == ["trk_b"]
    assert spans(log) == [(["trk_a"], 1.5, 4.5), (["trk_c"], 7.75, 9.5)]


def test_a_described_request_runs_from_the_template_to_a_mask() -> None:
    plan = plan_request("blur everyone except the person in red")
    assert isinstance(plan, Plan)
    log = OpLog()
    context = ctx(FakeDescriber({"trk_p1": "yes", "trk_p2": "no", "trk_p3": "no"}), log)

    found = call_tool(plan.calls[0].tool, plan.calls[0].args, context)
    call_tool("add_mask", {**plan.calls[1].args, "targets": found.track_ids}, context)

    [op] = log.ops
    assert isinstance(op, AddMask)
    assert op.targets == ["trk_p2", "trk_p3"]


def test_two_fragments_of_one_person_leave_no_unmasked_gap() -> None:
    """The tracker split one person into two ids with a gap between them (common in crowds):
    the two followed masks still overlap, so nothing is shown in the handover."""
    log = OpLog()
    fragments = [seen("trk_first", 1.0, 2.3), seen("trk_second", 3.2, 4.0)]
    args = {"targets": ["trk_first", "trk_second"], "effect": BLUR, "while_tracked": True}

    call_tool("add_mask", args, tracked_ctx(log, tracks=fragments))

    (first_op, second_op) = sorted(log.ops, key=lambda op: op.time_range.start)
    assert first_op.time_range.end >= second_op.time_range.start
