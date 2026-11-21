"""Stitching fragments and the head fallback (F-10b), on synthetic tracks: no model, no video."""

import pytest

from vibe.contract import Keyframe, Track
from vibe.modules.vision.stitch import HeadSettings, StitchSettings, head_fallback, stitch


def track(track_id: str, label: str, boxes: list[tuple[float, tuple]], state="detected") -> Track:
    return Track(
        track_id=track_id,
        label=label,
        source="detector",
        keyframes=[Keyframe(t=t, box=b, conf=0.9, state=state) for t, b in boxes],
    )


def moving(track_id: str, start: float, end: float, x0: float, label: str = "person") -> Track:
    """A 0.1-wide box moving right 0.1 per second, sampled every 0.5 s."""
    boxes, t = [], start
    while t <= end + 1e-9:
        x = x0 + 0.1 * (t - start)
        boxes.append((round(t, 3), (x, 0.2, x + 0.1, 0.6)))
        t += 0.5
    return track(track_id, label, boxes)


def test_fragments_where_the_target_was_heading_are_joined() -> None:
    a = moving("a", 0.0, 1.0, 0.1)  # ends at x=0.2
    b = moving("b", 2.0, 3.0, 0.3)  # 1 s later, where a was heading

    out = stitch([b, a])

    assert [t.track_id for t in out] == ["a"]
    assert [k.t for k in out[0].keyframes] == [0.0, 0.5, 1.0, 2.0, 2.5, 3.0]


def test_a_chain_of_fragments_becomes_one_track() -> None:
    parts = [moving("a", 0.0, 1.0, 0.1), moving("b", 1.5, 2.0, 0.25), moving("c", 2.5, 3.0, 0.35)]
    assert [t.track_id for t in stitch(parts)] == ["a"]


def test_too_late_too_far_or_another_label_stay_apart() -> None:
    a = moving("a", 0.0, 1.0, 0.1)
    late = moving("late", 3.0, 4.0, 0.4)  # 2 s gap > 1.5
    far = moving("far", 1.5, 2.0, 0.8)  # far from where a was heading
    face = moving("face", 1.5, 2.0, 0.25, label="face")

    out = stitch([a, late, far, face])

    assert sorted(t.track_id for t in out) == ["a", "face", "far", "late"]


def test_overlapping_tracks_are_two_targets() -> None:
    a, b = moving("a", 0.0, 2.0, 0.1), moving("b", 1.0, 3.0, 0.2)
    assert len(stitch([a, b])) == 2


def test_each_fragment_continues_into_the_best_fit_only() -> None:
    a = moving("a", 0.0, 1.0, 0.1)
    near = moving("near", 1.5, 2.0, 0.25)
    off = moving("off", 1.5, 2.0, 0.32)  # a little further
    out = {t.track_id: t for t in stitch([a, near, off])}
    assert set(out) == {"a", "off"}
    assert len(out["a"].keyframes) == 3 + 2


def test_settings_change_what_joins() -> None:
    a, b = moving("a", 0.0, 1.0, 0.1), moving("b", 2.0, 3.0, 0.3)
    assert len(stitch([a, b], StitchSettings(gap=0.5))) == 2


def test_head_fallback_covers_heads_no_face_box_covers() -> None:
    person = track("p1", "person", [(0.0, (0.4, 0.2, 0.6, 0.9)), (1.0, (0.4, 0.2, 0.6, 0.9))])
    face_at_0 = track("f1", "face", [(0.0, (0.45, 0.2, 0.55, 0.35))])

    out = head_fallback([person, face_at_0])

    added = [t for t in out if t.track_id == "p1_head"]
    assert len(added) == 1 and added[0].label == "face"
    (k,) = added[0].keyframes  # only t=1: at t=0 the detected face covers the head
    assert k.t == 1.0 and k.state == "uncertain" and k.conf <= 0.5
    x1, y1, x2, y2 = k.box
    assert (round(x1, 2), round(y1, 2), round(x2, 2), round(y2, 3)) == (0.44, 0.2, 0.56, 0.375)


def test_no_head_track_when_every_face_was_found() -> None:
    person = track("p1", "person", [(0.0, (0.4, 0.2, 0.6, 0.9))])
    face = track("f1", "face", [(0.0, (0.43, 0.18, 0.57, 0.4))])
    assert head_fallback([person, face]) == [person, face]


def test_head_settings_move_the_box() -> None:
    person = track("p1", "person", [(0.0, (0.0, 0.0, 1.0, 1.0))])
    (k,) = head_fallback([person], HeadSettings(top=0.1, height=0.2, width=1.0))[1].keyframes
    assert k.box == pytest.approx((0.0, 0.1, 1.0, 0.3))
