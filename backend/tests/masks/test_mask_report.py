"""F-11: the mask audit, checked against hand-worked answers. No video, GPU or ffmpeg."""

import ast
import math
import sys
from pathlib import Path

import numpy as np
import pytest
from mask_helpers import TRACKS, scenario_expected, scenario_state, scenario_tracks

import vibe.contract
import vibe.render.geometry as geometry
import vibe.render.mask_report as mask_report
from vibe.contract import (
    AddMask,
    AddRule,
    Cut,
    Effect,
    Keyframe,
    OpLog,
    Rule,
    TimelineState,
    TimeRange,
    Track,
)
from vibe.render import FrameRenderer
from vibe.render.mask_report import MaskAudit, audit_masks

BOX = (0.1, 0.1, 0.3, 0.4)
FPS = 30
EXAMPLES = Path(vibe.contract.__file__).parent / "examples"


def track(track_id: str, *times: float, states: dict[float, str] | None = None) -> Track:
    states = states or {}
    keyframes = [Keyframe(t=t, box=BOX, conf=0.9, state=states.get(t, "detected")) for t in times]
    return Track(track_id=track_id, label="face", source="detector", keyframes=keyframes)


def masked(*ops: object, target: str = "trk_1", time_range: TimeRange | None = None) -> OpLog:
    log = OpLog()
    log.append(
        AddMask(
            request_id="r_1",
            source="agent",
            targets=[target],
            effect=Effect(type="blur"),
            time_range=time_range,
        )
    )
    for op in ops:
        log.append(op)
    return log


def audit(state: TimelineState, tracks: list[Track], duration: float = 10) -> MaskAudit:
    return audit_masks(state, tracks, duration=duration)


def stretches(result: MaskAudit) -> list[tuple[str, float, float]]:
    return [(s.kind, s.start, s.end) for span in result.spans for s in span.stretches]


def scenario() -> MaskAudit:
    return audit(scenario_state(), scenario_tracks())


# --- The scenario -------------------------------------------------------------------------


def test_scenario_matches_the_hand_worked_spans() -> None:
    result = scenario()
    got = [
        {
            "track_id": s.track_id,
            "op_id": s.op_id,
            "request_id": s.request_id,
            "effect": s.effect.type,
            "stretches": [[x.kind, x.start, x.end] for x in s.stretches],
        }
        for s in result.spans
    ]
    assert got == scenario_expected()["spans"]


def test_scenario_matches_the_hand_worked_totals() -> None:
    assert scenario().totals == scenario_expected()["totals"]


def test_the_expected_file_was_worked_out_with_the_renderers_settings() -> None:
    expected = scenario_expected()
    assert expected["duration"] == 10
    assert expected["hold_seconds"] == geometry.HOLD_SECONDS, "rework the answers by hand"
    assert expected["max_gap_seconds"] == geometry.MAX_GAP_SECONDS, "rework the answers by hand"


def test_scenario_is_not_clean() -> None:
    assert not scenario().clean


def test_an_unmasked_track_is_not_reported() -> None:
    assert "trk_unmasked" not in {s.track_id for s in scenario().spans}


def test_the_contract_example_shows_a_mask_outlasting_its_track() -> None:
    log = OpLog.from_jsonl((EXAMPLES / "blur_first_half.jsonl").read_text(encoding="utf-8"))
    tracks = TRACKS.validate_json((EXAMPLES / "street.tracks.json").read_bytes())
    result = audit(log.state(), tracks, duration=15)
    assert stretches(result) == [("covered", 0, 1), ("held", 1, 1.5), ("lost", 1.5, 15)]


# --- Hold ---------------------------------------------------------------------------------


def test_a_single_keyframe_is_lost_away_from_its_hold() -> None:
    result = audit(masked().state(), [track("trk_1", 5)])
    assert stretches(result) == [("lost", 0, 4.5), ("held", 4.5, 5.5), ("lost", 5.5, 10)]


def test_a_hold_before_time_zero_is_clipped() -> None:
    result = audit(masked().state(), [track("trk_1", 0.2)], duration=1)
    assert stretches(result) == [("held", 0, 0.7), ("lost", 0.7, 1)]


def test_a_held_box_by_an_uncertain_keyframe_is_uncertain() -> None:
    result = audit(masked().state(), [track("trk_1", 0, states={0: "uncertain"})], duration=1)
    assert stretches(result) == [("uncertain", 0, 0.5), ("lost", 0.5, 1)]


# --- Gaps and uncertain keyframes ---------------------------------------------------------


def test_keyframes_exactly_the_limit_apart_are_covered() -> None:
    result = audit(masked().state(), [track("trk_1", 0, 1)], duration=1)
    assert stretches(result) == [("covered", 0, 1)]


def test_keyframes_just_past_the_limit_are_a_gap_with_a_lost_middle() -> None:
    result = audit(masked().state(), [track("trk_1", 0, 1.01)], duration=1.01)
    assert stretches(result) == [
        ("gap", 0, 0.5),
        ("lost", 0.5, pytest.approx(0.51)),
        ("gap", pytest.approx(0.51), 1.01),
    ]


def test_a_long_gap_is_lost_only_away_from_both_keyframes() -> None:
    result = audit(masked().state(), [track("trk_1", 0, 3)], duration=3)
    assert stretches(result) == [("gap", 0, 0.5), ("lost", 0.5, 2.5), ("gap", 2.5, 3)]


def test_a_long_gap_next_to_an_uncertain_keyframe_is_uncertain_then_lost() -> None:
    tracks = [track("trk_1", 0, 3, states={3: "uncertain"})]
    result = audit(masked().state(), tracks, duration=3)
    assert stretches(result) == [
        ("uncertain", 0, 0.5),
        ("lost", 0.5, 2.5),
        ("uncertain", 2.5, 3),
    ]


def test_a_gap_within_the_hold_of_its_keyframes_has_no_lost_middle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mask_report, "HOLD_SECONDS", 1.0)
    monkeypatch.setattr(geometry, "HOLD_SECONDS", 1.0)
    state, tracks = masked().state(), [track("trk_1", 0, 1.5)]
    result = audit(state, tracks, duration=1.5)
    assert stretches(result) == [("gap", 0, 1.5)]
    assert flagged_by_renderer(state, tracks, 1.5) == set()


# --- Shared ids ---------------------------------------------------------------------------


def test_shared_ids_are_lost_only_when_every_track_is() -> None:
    tracks = [track("trk_1", 0, 0.5), track("trk_1", 3, 3.5)]
    result = audit(masked().state(), tracks, duration=4)
    assert stretches(result) == [
        ("covered", 0, 0.5),
        ("held", 0.5, 1),
        ("lost", 1, 2.5),
        ("held", 2.5, 3),
        ("covered", 3, 3.5),
        ("held", 3.5, 4),
    ]


def test_overlapping_shared_tracks_report_the_most_cautious_kind() -> None:
    tracks = [track("trk_1", 0, 0.5, 1), track("trk_1", 0, 1, states={0: "uncertain"})]
    result = audit(masked().state(), tracks, duration=1)
    assert stretches(result) == [("uncertain", 0, 1)]


def test_track_order_does_not_change_the_result() -> None:
    tracks = scenario_tracks()
    assert audit(scenario_state(), tracks[::-1]) == scenario()


# --- Time bounds --------------------------------------------------------------------------


def test_a_span_past_the_end_of_the_video_is_clipped() -> None:
    state = masked(time_range=TimeRange(start=1, end=99)).state()
    result = audit(state, [track("trk_1", 0, 0.5, 1, 1.5, 2)], duration=2)
    assert stretches(result) == [("covered", 1, 2)]


def test_a_span_after_the_end_of_the_video_is_left_out() -> None:
    state = masked(time_range=TimeRange(start=20, end=30)).state()
    assert audit(state, [track("trk_1", 0)]).spans == ()


def test_a_span_wholly_inside_a_cut_is_left_out() -> None:
    cut = Cut(request_id="r_2", source="agent", time_range=TimeRange(start=1, end=5))
    state = masked(cut, time_range=TimeRange(start=2, end=3)).state()
    assert audit(state, [track("trk_1", 0)]).spans == ()


def test_an_open_ended_cut_ends_the_span() -> None:
    cut = Cut(request_id="r_2", source="agent", time_range=TimeRange(start=1))
    result = audit(masked(cut).state(), [track("trk_1", 0, 0.5, 1, 1.5)], duration=2)
    assert stretches(result) == [("covered", 0, 1)]


def test_an_empty_timeline_is_clean() -> None:
    result = audit(TimelineState(), [track("trk_1", 0)])
    assert result.spans == ()
    assert result.clean


def test_a_fully_covered_mask_is_clean() -> None:
    result = audit(masked().state(), [track("trk_1", 0, 0.5, 1)], duration=1)
    assert result.clean


def test_a_held_box_is_clean_because_the_renderer_reports_nothing() -> None:
    result = audit(masked().state(), [track("trk_1", 0, 0.5)], duration=1)
    assert result.totals["held"] == 0.5
    assert result.clean


def test_a_lost_target_is_not_clean() -> None:
    assert not audit(masked().state(), [track("trk_1", 0, 0.5)], duration=2).clean


def test_a_mask_with_no_track_is_not_clean() -> None:
    assert not audit(masked().state(), [], duration=1).clean


# --- Rules --------------------------------------------------------------------------------


def test_standing_rules_are_listed_and_stop_it_being_clean() -> None:
    rule = Rule(rule_id="rule_1", category="person", effect=Effect(type="blur"))
    log = masked(AddRule(request_id="r_2", source="agent", rule=rule))
    result = audit(log.state(), [track("trk_1", 0, 0.5, 1)], duration=1)
    assert result.rules_not_audited == ("rule_1",)
    assert not result.clean


# --- Against the real renderer -------------------------------------------------------------


def shown_frames(state: TimelineState, duration: float) -> list[float]:
    """Frame times halfway through each 1/FPS step, so none lands on a stretch boundary, where
    floating-point rounding can tip a frame either way. Exact boundaries are tested on their own,
    on times that add up exactly."""
    times = [(i + 0.5) / FPS for i in range(round(duration * FPS))]
    return [t for t in times if not state.is_cut(t)]


def flagged_by_renderer(
    state: TimelineState,
    tracks: list[Track],
    duration: float,
    times: list[float] | None = None,
) -> set[tuple[str, float]]:
    """Each (track id, frame) the renderer reports, skipping cut frames as export does."""
    renderer = FrameRenderer(state, tracks)
    times = shown_frames(state, duration) if times is None else times
    for t in times:
        renderer.render(np.zeros((4, 4, 3), np.uint8), t)
    return {
        (issue.track_id, t)
        for issue in renderer.issues
        for t in times
        if issue.first_seen <= t <= issue.last_seen
    }


def flagged_by_audit(
    state: TimelineState,
    tracks: list[Track],
    duration: float,
    times: list[float] | None = None,
) -> set[tuple[str, float]]:
    times = shown_frames(state, duration) if times is None else times
    return {
        (span.track_id, t)
        for span in audit(state, tracks, duration).spans
        for stretch in span.stretches
        if stretch.kind in ("lost", "no_track")
        for t in times
        if stretch.contains(t)
    }


def test_the_audit_flags_the_same_frames_as_the_renderer_on_the_scenario() -> None:
    state, tracks = scenario_state(), scenario_tracks()
    flagged = flagged_by_renderer(state, tracks, 10)
    assert {track_id for track_id, _ in flagged} == {
        "trk_ends_early",
        "trk_frag_a",
        "trk_frag_b",
        "trk_gap",
        "trk_missing",
        "trk_shared",
        "trk_starts_late",
        "trk_user_single",
    }
    assert flagged_by_audit(state, tracks, 10) == flagged


@pytest.mark.parametrize(
    "tracks",
    [
        [track("trk_1", 5)],
        [track("trk_1", 2, 8)],
        [track("trk_1", 0, 3, states={3: "uncertain"})],
        [track("trk_1", 0, 2), track("trk_1", 1, 6)],
        [track("trk_1", 0, 0.5), track("trk_1", 3, 3.5), track("trk_1", 9)],
    ],
    ids=["one keyframe", "long gap", "uncertain gap", "overlapping gaps", "three tracks"],
)
def test_the_audit_flags_the_same_frames_as_the_renderer(tracks: list[Track]) -> None:
    state = masked().state()
    flagged = flagged_by_renderer(state, tracks, 10)
    assert flagged
    assert flagged_by_audit(state, tracks, 10) == flagged


def test_a_mask_starting_between_frames_is_audited_from_the_frame_it_starts_on() -> None:
    """The renderer applies a mask starting at 0.55 to the frame from 0.5 (``until``), with the
    box at 0.5. A keyframe at 1.03 is held from 0.53, so that frame is lost."""
    state = masked(time_range=TimeRange(start=0.55, end=1.0)).state()
    tracks = [track("trk_1", 1.03)]
    times = [i / 10 for i in range(20)]
    renderer = FrameRenderer(state, tracks)
    for t, until in zip(times, [*times[1:], 2.0], strict=True):
        renderer.render(np.zeros((4, 4, 3), np.uint8), t, until)
    assert [(i.first_seen, i.last_seen) for i in renderer.issues] == [(0.5, 0.5)]

    result = audit_masks(state, tracks, duration=2, frame_times=times)
    assert stretches(result) == [
        ("lost", 0.5, pytest.approx(0.53)),
        ("held", pytest.approx(0.53), 1.0),
    ]
    assert not result.clean
    # Without frame times, it's audited from where the mask starts.
    assert stretches(audit(state, tracks, duration=2)) == [("held", 0.55, 1.0)]


def test_a_mask_starting_on_a_frame_or_after_a_cut_frame_keeps_its_start() -> None:
    times = [i / 10 for i in range(20)]
    tracks = [track("trk_1", 0.5, 1.0, 1.5)]
    on_a_frame = masked(time_range=TimeRange(start=0.6, end=1.0)).state()
    result = audit_masks(on_a_frame, tracks, duration=2, frame_times=times)
    assert result.spans[0].stretches[0].start == pytest.approx(0.6)
    # The frame from 0.5 is cut, so export never shows it: the mask first shows at 0.6.
    cut = Cut(request_id="r_2", source="agent", time_range=TimeRange(start=0.5, end=0.52))
    after_a_cut = masked(cut, time_range=TimeRange(start=0.55, end=1.0)).state()
    result = audit_masks(after_a_cut, tracks, duration=2, frame_times=times)
    assert result.spans[0].stretches[0].start == 0.55
    # Starting at the end of the video, it never shows, even with the last frame before it.
    at_the_end = masked(time_range=TimeRange(start=2.0, end=3.0)).state()
    assert audit_masks(at_the_end, tracks, duration=2, frame_times=times).spans == ()


def test_a_frame_exactly_at_the_end_of_a_hold_is_not_lost_as_in_the_renderer() -> None:
    """The renderer counts a box as lost only past ``HOLD_SECONDS``, so a frame exactly at the
    end of a hold isn't lost. Quarter-second frames land exactly on every hold edge here: 0.5 and
    2.5 start a hold, 1.5 and 3.5 end one."""
    state, tracks = masked().state(), [track("trk_1", 1.0, 3.0)]
    times = [i / 4 for i in range(4 * 5)]
    flagged = flagged_by_renderer(state, tracks, 5, times)
    assert ("trk_1", 1.5) not in flagged
    assert ("trk_1", 3.5) not in flagged
    assert flagged_by_audit(state, tracks, 5, times) == flagged


# --- Purity and bad input -----------------------------------------------------------------


def test_the_same_inputs_give_the_same_audit() -> None:
    assert scenario() == scenario()


def test_inputs_are_left_as_they_were() -> None:
    state, tracks = scenario_state(), scenario_tracks()
    before = state.model_dump(), [t.model_dump() for t in tracks]
    audit(state, tracks)
    assert (state.model_dump(), [t.model_dump() for t in tracks]) == before


@pytest.mark.parametrize("duration", [0, -1, math.nan, math.inf])
def test_a_bad_duration_is_an_error(duration: float) -> None:
    with pytest.raises(ValueError, match="duration"):
        audit(masked().state(), [], duration=duration)


def test_it_imports_only_the_contract_the_geometry_and_the_standard_library() -> None:
    tree = ast.parse(Path(mask_report.__file__).read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
    outside = {m for m in modules if m.split(".")[0] not in sys.stdlib_module_names}
    assert outside == {"vibe.contract", "vibe.render.geometry"}
