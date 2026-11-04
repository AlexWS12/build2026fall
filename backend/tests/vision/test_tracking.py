import pytest

from vibe.modules.vision.detection import Detection
from vibe.modules.vision.tracking import IouTracker, TrackerSettings

SIZE = (100, 100)


def run(frames: list[tuple[float, list[Detection]]], **settings) -> list:
    tracker = IouTracker(TrackerSettings(**settings))
    for t, dets in frames:
        tracker.update(t, dets, SIZE)
    return tracker.tracks()


def box_at(x: float, y: float = 0.2, w: float = 0.1) -> tuple[float, float, float, float]:
    return (round(x, 6), y, round(x + w, 6), y + w)


def test_a_moving_object_is_one_track_with_a_keyframe_per_detection():
    frames = [(i * 0.1, [Detection("person", box_at(0.1 + 0.02 * i), 0.9)]) for i in range(5)]
    [track] = run(frames)
    assert (track.track_id, track.label, track.source) == ("trk_1", "person", "detector")
    assert [k.t for k in track.keyframes] == pytest.approx([0.0, 0.1, 0.2, 0.3, 0.4])
    assert all(k.state == "detected" for k in track.keyframes)


def test_labels_are_tracked_separately_even_when_boxes_overlap():
    both = [Detection("person", box_at(0.1), 0.9), Detection("face", box_at(0.1), 0.9)]
    tracks = run([(0.0, both), (0.1, both)])
    assert sorted((t.label, len(t.keyframes)) for t in tracks) == [("face", 2), ("person", 2)]


def test_ids_follow_first_sighting_so_reruns_give_the_same_ids():
    frames = [
        (0.0, [Detection("person", box_at(0.6), 0.9)]),
        (0.1, [Detection("face", box_at(0.1), 0.9), Detection("person", box_at(0.6), 0.9)]),
    ]
    assert [(t.track_id, t.label) for t in run(frames)] == [("trk_1", "person"), ("trk_2", "face")]
    assert run(frames) == run(frames)


def test_a_short_miss_continues_the_track_and_a_long_one_starts_a_new_one():
    seen = [Detection("face", box_at(0.3), 0.9)]
    short = run([(0.0, seen), (0.5, []), (0.9, seen)], max_gap=1.0)
    assert [len(t.keyframes) for t in short] == [2]
    long = run([(0.0, seen), (0.5, []), (2.0, seen)], max_gap=1.0)
    assert [len(t.keyframes) for t in long] == [1, 1]


def test_velocity_keeps_a_fast_object_on_its_track():
    # 0 -> 0.05 overlaps enough to link; 0.05 -> 0.13 doesn't (IoU 0.11), but the predicted
    # position (0.10) does.
    xs = [0.0, 0.05, 0.13, 0.21]
    frames = [(i * 0.1, [Detection("person", box_at(x), 0.9)]) for i, x in enumerate(xs)]
    assert [len(t.keyframes) for t in run(frames)] == [4]


def test_privacy_labels_are_kept_at_low_confidence_and_marked_uncertain():
    weak = [Detection("face", box_at(0.1), 0.15), Detection("person", box_at(0.5), 0.15)]
    tracks = run([(0.0, weak)], min_conf=0.3, min_conf_privacy=0.1, uncertain_below=0.5)
    # The weak person is dropped; the weak face is kept (fail closed) and flagged for review.
    assert [(t.label, t.keyframes[0].state) for t in tracks] == [("face", "uncertain")]


def test_frames_must_come_in_time_order():
    tracker = IouTracker()
    tracker.update(1.0, [], SIZE)
    with pytest.raises(ValueError, match="time order"):
        tracker.update(1.0, [], SIZE)
