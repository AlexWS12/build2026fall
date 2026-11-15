"""F-41: the review queue built from the mask audit. No video, GPU or ffmpeg."""

import pytest

from vibe.contract import (
    AddMask,
    ConfirmMask,
    Cut,
    Effect,
    Keyframe,
    OpLog,
    TimelineState,
    TimeRange,
    Track,
    Undo,
)
from vibe.render import ReviewQueue, audit_masks, review_queue

BOX = (0.1, 0.1, 0.3, 0.4)


def track(track_id: str, *times: float, uncertain: tuple[float, ...] = ()) -> Track:
    keyframes = [
        Keyframe(t=t, box=BOX, conf=0.9, state="uncertain" if t in uncertain else "detected")
        for t in times
    ]
    return Track(track_id=track_id, label="face", source="detector", keyframes=keyframes)


def add_mask(target: str = "trk_1", request_id: str = "r_1") -> AddMask:
    return AddMask(
        request_id=request_id, source="agent", targets=[target], effect=Effect(type="blur")
    )


def confirm(start: float, end: float, request_id: str, target: str = "trk_1") -> ConfirmMask:
    return ConfirmMask(
        request_id=request_id,
        source="user",
        targets=[target],
        time_range=TimeRange(start=start, end=end),
    )


def state_of(*ops: object) -> TimelineState:
    log = OpLog()
    for op in ops:
        log.append(op)  # type: ignore[arg-type]
    return log.state()


def spans(queue: ReviewQueue) -> list[tuple[str, float, float]]:
    return [(i.kind, i.start, i.end) for i in queue.items]


def test_covered_only_gives_no_items() -> None:
    queue = review_queue(state_of(add_mask()), [track("trk_1", 0, 1, 2, 3)], duration=3)
    assert queue.items == []
    assert queue.held_seconds == 0


def test_gap_items_come_from_a_second_track_covering_the_lost_middle() -> None:
    tracks = [track("trk_1", 0, 1.2), track("trk_1", 0.4, 0.8)]
    queue = review_queue(state_of(add_mask()), tracks, duration=1.2)
    assert spans(queue) == [("gap", 0, 0.5), ("gap", 0.7, 1.2)]
    assert queue.items[0].label == "face"
    assert queue.items[0].effect.type == "blur"
    assert queue.items[0].request_id == "r_1"


def test_a_long_gap_is_lost_in_the_middle_and_merges_into_one_item() -> None:
    queue = review_queue(state_of(add_mask()), [track("trk_1", 0, 3)], duration=3)
    assert spans(queue) == [("lost", 0, 3)]


def test_an_uncertain_keyframe_gives_one_uncertain_item() -> None:
    queue = review_queue(
        state_of(add_mask()), [track("trk_1", 0, 0.8, 1.6, uncertain=(0.8,))], duration=1.6
    )
    assert spans(queue) == [("uncertain", 0, 1.6)]


def test_touching_flagged_stretches_take_the_worst_kind() -> None:
    queue = review_queue(
        state_of(add_mask()), [track("trk_1", 0, 1.2, 1.7, uncertain=(1.7,))], duration=1.7
    )
    assert spans(queue) == [("lost", 0, 1.7)]


def test_a_masked_id_without_a_track_is_no_track() -> None:
    queue = review_queue(state_of(add_mask("trk_9")), [track("trk_1", 0, 1)], duration=4)
    assert spans(queue) == [("no_track", 0, 4)]
    assert queue.items[0].label is None


def test_held_time_is_counted_not_listed() -> None:
    queue = review_queue(state_of(add_mask()), [track("trk_1", 1, 2, 3)], duration=5)
    assert spans(queue) == [("lost", 0, 0.5), ("lost", 3.5, 5)]
    assert queue.held_seconds == pytest.approx(1.0)


def test_held_time_that_was_reviewed_is_not_counted() -> None:
    state = state_of(add_mask(), confirm(0.5, 1, "r_2"))
    queue = review_queue(state, [track("trk_1", 1, 2, 3)], duration=5)
    assert queue.held_seconds == pytest.approx(0.5)


def test_a_full_confirm_removes_the_item() -> None:
    state = state_of(add_mask(), confirm(0, 3, "r_2"))
    assert review_queue(state, [track("trk_1", 0, 3)], duration=3).items == []


def test_a_confirm_of_the_lost_middle_leaves_the_gap_edges_as_gaps() -> None:
    state = state_of(add_mask(), confirm(0.5, 0.7, "r_2"))
    queue = review_queue(state, [track("trk_1", 0, 1.2)], duration=1.2)
    assert spans(queue) == [("gap", 0, 0.5), ("gap", 0.7, 1.2)]


def test_a_confirm_of_a_long_gaps_lost_middle_leaves_two_gap_items() -> None:
    state = state_of(add_mask(), confirm(0.5, 2.5, "r_2"))
    queue = review_queue(state, [track("trk_1", 0, 3)], duration=3)
    assert spans(queue) == [("gap", 0, 0.5), ("gap", 2.5, 3)]


def test_a_partial_confirm_splits_the_item() -> None:
    state = state_of(add_mask(), confirm(1, 2, "r_2"))
    queue = review_queue(state, [track("trk_1", 0, 3)], duration=3)
    assert spans(queue) == [("lost", 0, 1), ("lost", 2, 3)]


def test_a_partial_confirm_shrinks_the_item() -> None:
    state = state_of(add_mask(), confirm(0, 1, "r_2"))
    queue = review_queue(state, [track("trk_1", 0, 3)], duration=3)
    assert spans(queue) == [("lost", 1, 3)]


def test_adjacent_confirms_from_different_requests_cover_together() -> None:
    state = state_of(add_mask(), confirm(0, 1.5, "r_2"), confirm(1.5, 3, "r_3"))
    assert review_queue(state, [track("trk_1", 0, 3)], duration=3).items == []


def test_undoing_the_confirm_brings_the_item_back() -> None:
    log = OpLog()
    for op in (add_mask(), confirm(0, 3, "r_2")):
        log.append(op)
    tracks = [track("trk_1", 0, 3)]
    assert review_queue(log.state(), tracks, duration=3).items == []
    log.append(Undo(request_id="r_3", source="user", undo_request_id="r_2"))
    assert spans(review_queue(log.state(), tracks, duration=3)) == [("lost", 0, 3)]


def test_cut_ranges_are_left_out() -> None:
    cut = Cut(request_id="r_2", source="agent", time_range=TimeRange(start=1, end=2))
    queue = review_queue(state_of(add_mask(), cut), [track("trk_1", 0, 3)], duration=3)
    assert spans(queue) == [("lost", 0, 1), ("lost", 2, 3)]


def test_item_id_is_stable_and_changes_with_the_range() -> None:
    tracks = [track("trk_1", 0, 3)]
    first = review_queue(state_of(add_mask()), tracks, duration=3)
    again = review_queue(state_of(add_mask()), tracks, duration=3)
    shorter = review_queue(state_of(add_mask(), confirm(0, 1, "r_2")), tracks, duration=3)
    assert first.items[0].item_id == again.items[0].item_id
    assert first.items[0].item_id.startswith("rv_")
    assert shorter.items[0].item_id != first.items[0].item_id


def test_items_are_ordered_by_severity_then_start_then_track() -> None:
    state = state_of(
        add_mask("trk_a"),
        add_mask("trk_b"),
        add_mask("trk_c"),
        add_mask("trk_d"),
    )
    tracks = [
        track("trk_a", 0, 1.2),  # two gaps, around the middle another track covers
        track("trk_a", 0.4, 0.8),
        track("trk_b", 0, 1, 2, uncertain=(1,)),  # uncertain
        track("trk_c", 0, 3),  # lost
    ]  # trk_d has no track: no_track
    queue = review_queue(state, tracks, duration=1.7)
    assert [(i.kind, i.track_id) for i in queue.items] == [
        ("lost", "trk_c"),
        ("no_track", "trk_d"),
        ("uncertain", "trk_b"),
        ("gap", "trk_a"),
        ("gap", "trk_a"),
    ]
    assert [i.start for i in queue.items[-2:]] == [0, 0.7]


def test_frame_times_are_passed_to_the_audit() -> None:
    mask = add_mask()
    mask = mask.model_copy(update={"time_range": TimeRange(start=0.5, end=3)})
    state = state_of(mask)
    tracks = [track("trk_1", 0, 3)]
    frame_times = [0.0, 0.4, 0.8, 1.2, 1.6, 2.0, 2.4, 2.8]
    queue = review_queue(state, tracks, duration=3, frame_times=frame_times)
    audit = audit_masks(state, tracks, duration=3, frame_times=frame_times)
    assert queue.items[0].start == audit.spans[0].stretches[0].start == 0.4
    assert review_queue(state, tracks, duration=3).items[0].start == 0.5


def test_standing_rules_are_reported_not_audited() -> None:
    from vibe.contract import AddRule, Rule

    rule = AddRule(
        request_id="r_1",
        source="agent",
        rule=Rule(rule_id="rule_1", category="person", effect=Effect(type="blur")),
    )
    queue = review_queue(state_of(rule), [], duration=3)
    assert queue.rules_not_audited == ["rule_1"]
