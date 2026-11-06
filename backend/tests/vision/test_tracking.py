import sys
import types

import numpy as np
import pytest

from vibe.modules.vision.detection import Detection, NotInstalled
from vibe.modules.vision.tracking import ByteTracker, IouTracker, TrackerSettings

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


# --- ByteTracker glue, against a stand-in for `supervision` and `trackers` -------------------
# This checks our code around the library (what we send, what we do with what comes back), not
# ByteTrack itself: the real library has not been run (HANDOFF entry says so).


class FakeDetections:
    def __init__(self, xyxy, confidence=None, class_id=None, tracker_id=None):
        self.xyxy = np.asarray(xyxy, dtype=np.float32).reshape(-1, 4)
        self.confidence, self.class_id, self.tracker_id = confidence, class_id, tracker_id

    @classmethod
    def empty(cls):
        return cls([])

    def __len__(self):
        return len(self.xyxy)


class FakeByteTrack:
    """Returns a detection with an id once it is confident enough and the tracker has seen a
    second frame, which is how real ByteTrack holds back a new track."""

    def __init__(self, confirm_conf: float, lost_track_buffer: int = 30):
        self.confirm_conf, self.lost_track_buffer, self.frames = confirm_conf, lost_track_buffer, 0
        self.timestamps: list[float] = []

    def update(self, found, timestamp=None):
        self.timestamps.append(timestamp)
        self.frames += 1
        if self.frames < 2 or not len(found):
            return FakeDetections.empty()
        keep = np.flatnonzero(found.confidence >= self.confirm_conf)
        return FakeDetections(found.xyxy[keep], found.confidence[keep], tracker_id=keep + 1)


@pytest.fixture
def fake_libraries(monkeypatch):
    def install(confirm_conf: float = 0.5) -> None:
        sv, lib = types.ModuleType("supervision"), types.ModuleType("trackers")
        sv.Detections = FakeDetections
        lib.ByteTrackTracker = lambda **kw: FakeByteTrack(confirm_conf, **kw)
        monkeypatch.setitem(sys.modules, "supervision", sv)
        monkeypatch.setitem(sys.modules, "trackers", lib)

    return install


def run_bytetrack(frames) -> list:
    tracker = ByteTracker(TrackerSettings())
    for t, dets in frames:
        tracker.update(t, dets, SIZE)
    return tracker.tracks()


def test_bytetrack_needs_its_packages(monkeypatch):
    monkeypatch.setitem(sys.modules, "supervision", None)
    with pytest.raises(NotInstalled, match="trackers supervision"):
        ByteTracker()


def test_bytetrack_gets_real_time_so_max_gap_means_seconds_at_any_stride(fake_libraries):
    fake_libraries()
    tracker = ByteTracker(TrackerSettings(max_gap=2.0))
    seen = [Detection("face", box_at(0.3), 0.9)]
    for t in (0.0, 0.3, 0.6):  # a stride of 3 at 10 fps
        tracker.update(t, seen, SIZE)
    face = tracker._per_label["face"]
    assert face.timestamps == pytest.approx([0.0, 0.3, 0.6])
    assert face.lost_track_buffer == 60  # ByteTrack counts the buffer as seconds * 30


def test_bytetrack_still_covers_the_frames_before_it_confirms_a_track(fake_libraries):
    fake_libraries()
    seen = [Detection("face", box_at(0.3), 0.9)]
    tracks = run_bytetrack([(0.0, seen), (0.1, seen), (0.2, seen)])
    # ByteTrack says nothing on the first frame; that sighting becomes its own track instead of
    # being dropped, and ends once ByteTrack covers the same object (no box drawn twice).
    times = sorted(k.t for t in tracks for k in t.keyframes)
    assert times == pytest.approx([0.0, 0.1, 0.2])
    assert sorted(len(t.keyframes) for t in tracks) == [1, 2]
    assert tracks[1].keyframes[0].box == pytest.approx(box_at(0.3), abs=1e-6)


def test_bytetrack_keeps_a_weak_face_it_never_returns_next_to_a_confirmed_one(fake_libraries):
    fake_libraries(confirm_conf=0.5)
    both = [Detection("face", box_at(0.1), 0.9), Detection("face", box_at(0.6), 0.2)]
    tracks = run_bytetrack([(0.0, both), (0.1, both), (0.2, both)])
    weak = [t for t in tracks if t.keyframes[0].box[0] == pytest.approx(0.6, abs=1e-6)]
    # The 0.2 face is under ByteTrack's bar but over the privacy floor: tracked all three frames.
    assert [len(t.keyframes) for t in weak] == [3]
    assert {k.state for k in weak[0].keyframes} == {"uncertain"}
    # The confident one is never covered twice at the same time.
    strong_times = sorted(k.t for t in tracks if t not in weak for k in t.keyframes)
    assert strong_times == pytest.approx([0.0, 0.1, 0.2])
