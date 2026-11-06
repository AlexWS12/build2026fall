"""F-11: the mask audit, checked against hand-worked answers. No video, GPU or ffmpeg."""

import ast
import math
import sys
from pathlib import Path

import pytest
from mask_helpers import TRACKS, scenario_expected, scenario_state, scenario_tracks

import vibe.contract
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
from vibe.render.mask_report import GAP_SECONDS, MaskAudit, Stretch, audit_masks

BOX = (0.1, 0.1, 0.3, 0.4)
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


def audit(
    state: TimelineState,
    tracks: list[Track],
    duration: float = 10,
    hold: float = 0.5,
    gap: float = 0.5,
) -> MaskAudit:
    return audit_masks(state, tracks, duration=duration, hold_seconds=hold, gap_seconds=gap)


def stretches(result: MaskAudit) -> list[tuple[str, float, float]]:
    return [(s.kind, s.start, s.end) for span in result.spans for s in span.stretches]


def scenario(hold: float = 0.5) -> MaskAudit:
    return audit(scenario_state(), scenario_tracks(), hold=hold)


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


def test_scenario_uses_the_settings_written_in_the_expected_file() -> None:
    expected = scenario_expected()
    result = audit_masks(
        scenario_state(),
        scenario_tracks(),
        duration=expected["duration"],
        hold_seconds=expected["hold_seconds"],
        gap_seconds=expected["gap_seconds"],
    )
    assert result == scenario()


def test_scenario_is_not_clean() -> None:
    assert not scenario().clean


def test_an_unmasked_track_is_not_reported() -> None:
    assert "trk_unmasked" not in {s.track_id for s in scenario().spans}


def test_the_contract_example_shows_a_mask_outlasting_its_track() -> None:
    log = OpLog.from_jsonl((EXAMPLES / "blur_first_half.jsonl").read_text(encoding="utf-8"))
    tracks = TRACKS.validate_json((EXAMPLES / "street.tracks.json").read_bytes())
    result = audit(log.state(), tracks, duration=15)
    assert stretches(result) == [("covered", 0, 1), ("held", 1, 1.5), ("off_screen", 1.5, 15)]


# --- Hold ---------------------------------------------------------------------------------


def test_hold_zero_is_the_rule_with_nothing_past_the_keyframes() -> None:
    result = scenario(hold=0)
    assert result.totals["held"] == 0
    (ends_early,) = [s for s in result.spans if s.track_id == "trk_ends_early"]
    assert ends_early.stretches == (
        Stretch("covered", 0, 4),
        Stretch("off_screen", 4, 7),
        Stretch("off_screen", 8, 10),
    )


def test_a_single_keyframe_with_no_hold_is_off_screen_throughout() -> None:
    result = audit(masked().state(), [track("trk_1", 5)], hold=0)
    assert stretches(result) == [("off_screen", 0, 10)]


def test_a_hold_before_time_zero_is_clipped() -> None:
    result = audit(masked().state(), [track("trk_1", 0.2)], duration=1)
    assert stretches(result) == [("held", 0, 0.7), ("off_screen", 0.7, 1)]


def test_a_held_box_by_an_uncertain_keyframe_is_uncertain() -> None:
    result = audit(masked().state(), [track("trk_1", 0, states={0: "uncertain"})], duration=1)
    assert stretches(result) == [("uncertain", 0, 0.5), ("off_screen", 0.5, 1)]


# --- Gaps and uncertain keyframes ---------------------------------------------------------


def test_keyframes_exactly_gap_seconds_apart_are_covered() -> None:
    result = audit(masked().state(), [track("trk_1", 0, 0.5)], duration=0.5)
    assert stretches(result) == [("covered", 0, 0.5)]


def test_keyframes_just_past_gap_seconds_are_a_gap() -> None:
    result = audit(masked().state(), [track("trk_1", 0, 0.51)], duration=0.51)
    assert stretches(result) == [("gap", 0, 0.51)]


def test_the_gap_default_is_the_labelled_placeholder() -> None:
    assert GAP_SECONDS == 0.5
    assert "UNMEASURED PLACEHOLDER" in Path(mask_report.__file__).read_text(encoding="utf-8")
    state, tracks = masked().state(), [track("trk_1", 0, 0.6)]
    result = audit_masks(state, tracks, duration=0.6, hold_seconds=0.5)
    assert stretches(result) == [("gap", 0, pytest.approx(0.6))]


def test_a_long_gap_next_to_an_uncertain_keyframe_is_uncertain() -> None:
    tracks = [track("trk_1", 0, 3, states={3: "uncertain"})]
    result = audit(masked().state(), tracks, duration=3)
    assert stretches(result) == [("uncertain", 0, 3)]


# --- Shared ids ---------------------------------------------------------------------------


def test_shared_ids_are_off_screen_only_when_every_track_is_absent() -> None:
    tracks = [track("trk_1", 0, 0.5), track("trk_1", 3, 3.5)]
    result = audit(masked().state(), tracks, duration=4, hold=0)
    assert stretches(result) == [
        ("covered", 0, 0.5),
        ("off_screen", 0.5, 3),
        ("covered", 3, 3.5),
        ("off_screen", 3.5, 4),
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


# --- Rules --------------------------------------------------------------------------------


def test_standing_rules_are_listed_and_stop_it_being_clean() -> None:
    rule = Rule(rule_id="rule_1", category="person", effect=Effect(type="blur"))
    log = masked(AddRule(request_id="r_2", source="agent", rule=rule))
    result = audit(log.state(), [track("trk_1", 0, 0.5, 1)], duration=1)
    assert result.rules_not_audited == ("rule_1",)
    assert not result.clean


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


@pytest.mark.parametrize("value", [-1, math.nan, math.inf])
def test_bad_settings_are_errors(value: float) -> None:
    with pytest.raises(ValueError, match="hold_seconds"):
        audit(masked().state(), [], hold=value)
    with pytest.raises(ValueError, match="gap_seconds"):
        audit(masked().state(), [], gap=value)


def test_it_imports_only_the_contract_and_the_standard_library() -> None:
    tree = ast.parse(Path(mask_report.__file__).read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.add(node.module or "")
    outside = {m for m in modules if m.split(".")[0] not in sys.stdlib_module_names}
    assert outside == {"vibe.contract"}
