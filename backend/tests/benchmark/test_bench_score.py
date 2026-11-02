"""F-09 scoring: detection (IoU) and privacy exposure (is the target still visible after masking).

Boxes are normalized [x1, y1, x2, y2]. FACE is 0.2 wide and 0.3 tall (area 0.06); the comments
show the arithmetic each expectation comes from.
"""

import pytest

from vibe.benchmark.score import (
    DEFAULT_PADDING,
    box_at,
    coverage,
    iou,
    pad,
    score_tracks,
)
from vibe.contract import Effect, Keyframe, Track

FACE = (0.40, 0.20, 0.60, 0.50)
OTHER_FACE = (0.70, 0.20, 0.90, 0.50)

Frame = tuple[float, tuple[float, float, float, float]]


def truth(*frames: Frame, label: str = "face", tid: str = "gt") -> Track:
    keyframes = [Keyframe(t=t, box=b, conf=1.0, state="user") for t, b in frames]
    return Track(track_id=tid, label=label, source="user", keyframes=keyframes)


def pred(*frames: Frame, label: str = "face", tid: str = "p") -> Track:
    keyframes = [Keyframe(t=t, box=b, conf=0.9, state="detected") for t, b in frames]
    return Track(track_id=tid, label=label, source="detector", keyframes=keyframes)


# --- Geometry -----------------------------------------------------------------------------


def test_geometry() -> None:
    assert iou(FACE, FACE) == pytest.approx(1)
    assert iou(FACE, OTHER_FACE) == 0
    assert coverage(FACE, (0, 0, 1, 1)) == 1
    assert pad(FACE, 0.5) == pytest.approx((0.35, 0.125, 0.65, 0.575))
    assert pad((0.0, 0.0, 1.0, 1.0), 0.5) == (0.0, 0.0, 1.0, 1.0)  # clipped to the frame


def test_default_padding_is_the_contracts() -> None:
    assert DEFAULT_PADDING == Effect(type="blur").padding


# --- Detection and exposure ---------------------------------------------------------------


def test_a_perfect_prediction() -> None:
    s = score_tracks([truth((0, FACE), (1, FACE))], [pred((0, FACE), (1, FACE))])
    t = s.total
    assert (t.truth_boxes, t.detected, t.exposed) == (2, 2, 0)
    assert (t.recall, t.precision, t.exposure_rate) == (1.0, 1.0, 0.0)
    assert s.moments == 2


def test_a_missed_face_is_an_exposure() -> None:
    t = score_tracks([truth((0, FACE))], []).total
    assert (t.recall, t.exposed, t.precision) == (0.0, 1, None)


def test_a_shifted_big_box_is_a_bad_detection_but_a_good_mask() -> None:
    big = (0.30, 0.10, 0.70, 0.60)  # IoU = 0.06 / 0.20 = 0.3 < 0.5, but it covers all of FACE
    t = score_tracks([truth((0, FACE))], [pred((0, big))]).total
    assert (t.detected, t.exposed) == (0, 0)


def test_a_tight_box_is_a_good_detection_but_a_leak() -> None:
    # IoU = 0.04 / 0.06 = 0.67, so detected. Padded by 0.15 it spans y 0.185..0.415, which
    # covers 0.2 x 0.215 = 0.043 of FACE's 0.06 = 72% < 90%: the chin shows.
    tight = (0.40, 0.20, 0.60, 0.40)
    t = score_tracks([truth((0, FACE))], [pred((0, tight))]).total
    assert (t.detected, t.exposed) == (1, 1)


def test_padding_closes_a_small_gap() -> None:
    # Unpadded it covers 0.18 x 0.28 = 0.0504 of 0.06 = 84% (a leak); padded by 0.15 it spans
    # x 0.3965..0.6035 and y 0.189..0.511, which covers FACE entirely.
    nearly = (0.41, 0.21, 0.59, 0.49)
    assert score_tracks([truth((0, FACE))], [pred((0, nearly))], padding=0).total.exposed == 1
    assert score_tracks([truth((0, FACE))], [pred((0, nearly))]).total.exposed == 0


# --- Time ---------------------------------------------------------------------------------


def test_predictions_are_interpolated_between_keyframes() -> None:
    moving = pred((0, (0.0, 0.2, 0.2, 0.5)), (2, FACE))
    assert box_at(moving, 1.0) == pytest.approx((0.2, 0.2, 0.4, 0.5))
    assert box_at(moving, 2.5) is None and box_at(moving, -0.1) is None
    assert score_tracks([truth((2, FACE))], [moving]).total.detected == 1


def test_a_track_that_ended_doesnt_cover_later_moments() -> None:
    t = score_tracks([truth((0, FACE), (3, FACE))], [pred((0, FACE), (1, FACE))]).total
    assert (t.detected, t.exposed) == (1, 1)


# --- Labels and matching ------------------------------------------------------------------


def test_only_the_same_label_counts() -> None:
    s = score_tracks([truth((0, FACE))], [pred((0, FACE), label="person")])
    face = next(x for x in s.by_label if x.label == "face")
    person = next(x for x in s.by_label if x.label == "person")
    assert face.exposed == 1  # "blur faces" doesn't mask person tracks
    assert (person.predicted_boxes, person.precision) == (1, 0.0)


def test_matching_is_one_to_one() -> None:
    two = [truth((0, FACE), tid="a"), truth((0, OTHER_FACE), tid="b")]
    t = score_tracks(two, [pred((0, FACE))]).total
    assert (t.truth_boxes, t.detected, t.exposed) == (2, 1, 1)

    doubled = score_tracks(
        [truth((0, FACE))], [pred((0, FACE), tid="p1"), pred((0, FACE), tid="p2")]
    )
    assert (doubled.total.detected, doubled.total.precision) == (1, 0.5)


def test_one_box_cannot_detect_two_overlapping_faces() -> None:
    # Two faces side by side, one box over both: IoU 0.057 / 0.063 = 0.90 with each. It may
    # detect one of them, not both, or a crowd would score better recall than it deserves.
    behind = (0.42, 0.20, 0.62, 0.50)
    one_box = (0.41, 0.20, 0.61, 0.50)
    gt = [truth((0, FACE), tid="a"), truth((0, behind), tid="b")]
    t = score_tracks(gt, [pred((0, one_box))]).total
    assert (t.truth_boxes, t.detected) == (2, 1)


def test_no_ground_truth_gives_no_ratios() -> None:
    s = score_tracks([], [])
    assert (s.moments, s.total.recall, s.total.exposure_rate, s.by_label) == (0, None, None, [])
