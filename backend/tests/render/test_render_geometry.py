"""Track geometry: boxes interpolate, hold and grow past a track's ends, and never shrink."""

import pytest

from vibe.contract import Keyframe, Track
from vibe.render.geometry import (
    HOLD_GROWTH,
    HOLD_SECONDS,
    MAX_GAP_SECONDS,
    MAX_HOLD_GROWTH,
    UNCERTAIN_PADDING,
    TrackPath,
    to_pixels,
)


def track(*keyframes: tuple[float, tuple[float, float, float, float], str]) -> Track:
    return Track(
        track_id="trk_1",
        label="person",
        source="detector",
        keyframes=[Keyframe(t=t, box=box, conf=0.9, state=state) for t, box, state in keyframes],
    )


MOVING = track((1.0, (0.0, 0.0, 0.2, 0.2), "detected"), (2.0, (0.4, 0.2, 0.6, 0.6), "detected"))


def test_box_moves_linearly_between_keyframes() -> None:
    placed = TrackPath(MOVING).at(1.5)
    assert placed.box == pytest.approx((0.2, 0.1, 0.4, 0.4))
    assert placed.extra_padding == 0
    assert not placed.lost


def test_box_at_a_keyframe_is_that_keyframe() -> None:
    path = TrackPath(MOVING)
    assert path.at(1.0).box == (0.0, 0.0, 0.2, 0.2)
    assert path.at(2.0).box == (0.4, 0.2, 0.6, 0.6)


@pytest.mark.parametrize(("t", "held"), [(0.75, 0.25), (2.25, 0.25), (2.5, 0.5)])
def test_box_is_held_and_grows_past_the_ends(t: float, held: float) -> None:
    placed = TrackPath(MOVING).at(t)
    nearest = MOVING.keyframes[0] if t < 1 else MOVING.keyframes[-1]
    assert placed.box == nearest.box
    assert placed.extra_padding == pytest.approx(HOLD_GROWTH * held)
    assert not placed.lost


@pytest.mark.parametrize("t", [1.0 - HOLD_SECONDS - 0.01, 2.0 + HOLD_SECONDS + 0.01, 100.0])
def test_a_lost_target_keeps_its_box_and_is_marked_lost(t: float) -> None:
    placed = TrackPath(MOVING).at(t)
    nearest = MOVING.keyframes[0] if t < 1 else MOVING.keyframes[-1]
    assert placed.box == nearest.box
    assert placed.lost


def test_a_held_box_stops_growing() -> None:
    assert TrackPath(MOVING).at(100.0).extra_padding == MAX_HOLD_GROWTH


def test_single_keyframe_track_is_held_for_good() -> None:
    path = TrackPath(track((0.0, (0.1, 0.1, 0.2, 0.2), "user")))
    assert path.at(0.0).box == (0.1, 0.1, 0.2, 0.2)
    assert not path.at(HOLD_SECONDS).lost
    assert path.at(60.0).box == (0.1, 0.1, 0.2, 0.2)
    assert path.at(60.0).lost


LEFT, RIGHT = (0.05, 0.4, 0.2, 0.7), (0.8, 0.5, 0.95, 0.8)
LONG_GAP = track((1.0, LEFT, "detected"), (8.0, RIGHT, "detected"))


def test_long_gap_covers_both_ends_and_the_space_between() -> None:
    for t in (1.25, 4.5, 7.75):
        assert TrackPath(LONG_GAP).at(t).box == (0.05, 0.4, 0.95, 0.8)


def test_box_in_a_long_gap_counts_from_the_nearest_keyframe() -> None:
    path = TrackPath(LONG_GAP)
    assert path.at(1.25).extra_padding == pytest.approx(HOLD_GROWTH * 0.25)
    assert not path.at(1.25).lost
    assert path.at(7.75).extra_padding == pytest.approx(HOLD_GROWTH * 0.25)
    assert not path.at(7.75).lost
    assert path.at(4.5).extra_padding == MAX_HOLD_GROWTH
    assert path.at(4.5).lost


def test_gap_of_exactly_the_limit_still_moves_in_a_line() -> None:
    path = TrackPath(track((0.0, LEFT, "detected"), (MAX_GAP_SECONDS, RIGHT, "detected")))
    assert path.at(MAX_GAP_SECONDS / 2).box == pytest.approx((0.425, 0.45, 0.575, 0.75))


def test_uncertain_end_of_a_long_gap_adds_padding() -> None:
    path = TrackPath(track((1.0, LEFT, "uncertain"), (8.0, RIGHT, "detected")))
    assert path.at(4.5).extra_padding == MAX_HOLD_GROWTH + UNCERTAIN_PADDING


@pytest.mark.parametrize("states", [("uncertain", "detected"), ("detected", "uncertain")])
def test_uncertain_neighbour_adds_padding(states: tuple[str, str]) -> None:
    box = (0.1, 0.1, 0.3, 0.3)
    path = TrackPath(track((0.0, box, states[0]), (1.0, box, states[1])))
    assert path.at(0.5).extra_padding == UNCERTAIN_PADDING


def test_held_uncertain_box_gets_both_paddings() -> None:
    path = TrackPath(track((0.0, (0.1, 0.1, 0.3, 0.3), "uncertain")))
    assert path.at(0.2).extra_padding == pytest.approx(UNCERTAIN_PADDING + HOLD_GROWTH * 0.2)


def test_padding_grows_the_box_by_that_fraction_of_its_size() -> None:
    # 50 px wide and tall; +20% is 10 px, 5 on each side.
    assert to_pixels((0.25, 0.25, 0.75, 0.75), 0.2, 100, 100) == (20, 20, 80, 80)


def test_no_padding_is_the_box_itself() -> None:
    assert to_pixels((0.25, 0.5, 0.75, 1.0), 0.0, 320, 240) == (80, 120, 240, 240)


def test_pixels_are_clamped_to_the_frame() -> None:
    assert to_pixels((0.0, 0.0, 1.0, 1.0), 0.5, 320, 240) == (0, 0, 320, 240)


@pytest.mark.parametrize(
    "box", [(0.1001, 0.2002, 0.3003, 0.4004), (0.333, 0.333, 0.334, 0.334), (0.7, 0.7, 0.9, 0.9)]
)
def test_rounding_never_shrinks_the_box(box: tuple[float, float, float, float]) -> None:
    width, height = 321, 241
    x1, y1, x2, y2 = to_pixels(box, 0.0, width, height)
    assert x1 <= box[0] * width and y1 <= box[1] * height
    assert x2 >= box[2] * width and y2 >= box[3] * height
    assert x2 > x1 and y2 > y1
