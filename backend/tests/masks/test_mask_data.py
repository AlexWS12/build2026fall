"""F-11: the mask audit scenario is valid contract data and covers what it says it covers."""

from mask_helpers import scenario_expected, scenario_state, scenario_tracks


def test_every_masked_id_has_a_track_except_the_missing_one() -> None:
    masked = set(scenario_state().masks)
    tracked = {t.track_id for t in scenario_tracks()}
    assert masked - tracked == {"trk_missing"}


def test_an_unmasked_track_is_in_the_data() -> None:
    masked = set(scenario_state().masks)
    tracked = {t.track_id for t in scenario_tracks()}
    assert tracked - masked == {"trk_unmasked"}


def test_two_tracks_share_an_id() -> None:
    shared = [t for t in scenario_tracks() if t.track_id == "trk_shared"]
    assert [t.source for t in shared] == ["detector", "user"]


def test_a_box_touches_each_edge_of_the_frame() -> None:
    (edge,) = [t for t in scenario_tracks() if t.track_id == "trk_edge"]
    boxes = [k.box for k in edge.keyframes]
    assert any(b[0] == 0 and b[1] == 0 for b in boxes)
    assert any(b[2] == 1 and b[3] == 1 for b in boxes)


def test_the_user_track_has_one_keyframe() -> None:
    (single,) = [t for t in scenario_tracks() if t.track_id == "trk_user_single"]
    assert [k.state for k in single.keyframes] == ["user"]


def test_one_keyframe_is_uncertain() -> None:
    states = [k.state for t in scenario_tracks() for k in t.keyframes]
    assert states.count("uncertain") == 1


def test_the_timeline_has_bounded_and_open_masks_and_a_cut() -> None:
    state = scenario_state()
    (bounded,) = state.masks["trk_bounded"]
    (opened,) = state.masks["trk_open"]
    assert (bounded.start, bounded.end) == (2, 5)
    assert (opened.start, opened.end) == (6, None)
    assert [(c.start, c.end) for c in state.cuts] == [(7, 8)]


def test_expected_spans_are_ordered_and_name_every_masked_id() -> None:
    expected = scenario_expected()
    ids = [s["track_id"] for s in expected["spans"]]
    assert ids == sorted(scenario_state().masks)
    for span in expected["spans"]:
        times = [t for _, start, end in span["stretches"] for t in (start, end)]
        assert times == sorted(times)


def test_expected_totals_add_up_from_the_expected_stretches() -> None:
    expected = scenario_expected()
    totals = dict.fromkeys(expected["totals"], 0.0)
    for span in expected["spans"]:
        for kind, start, end in span["stretches"]:
            totals[kind] += end - start
    assert totals == expected["totals"]
