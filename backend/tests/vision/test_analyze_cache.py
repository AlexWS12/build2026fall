import re

import pytest
from vision_helpers import FPS, as_frames, square_frames

from vibe.contract import Keyframe, Track
from vibe.modules.vision import cache
from vibe.modules.vision.analyze import analyze_frames, analyze_video, retrack
from vibe.modules.vision.detectors.stub import ColorDetector
from vibe.modules.vision.tracking import IouTracker, TrackerSettings
from vibe.storage import Storage

_SAFE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@-]{0,127}$")


def detectors():
    return [ColorDetector(label="person", color="red"), ColorDetector(label="face", color="blue")]


def test_analyze_frames_tracks_each_object_through_the_clip():
    analysis = analyze_frames(as_frames(square_frames()), detectors(), IouTracker())
    person, face = analysis.tracks
    assert (person.label, len(person.keyframes)) == ("person", 10)
    assert (face.label, len(face.keyframes)) == ("face", 10)
    # The red square moves right 4 px of 96 per frame; the track follows it.
    assert person.keyframes[0].box[0] == pytest.approx(4 / 96, abs=1e-6)
    assert person.keyframes[-1].box[0] == pytest.approx(40 / 96, abs=1e-6)
    assert analysis.frames == 10
    assert set(analysis.seconds) == {"decode", "stub-red", "stub-blue", "track"}
    assert (analysis.detections.width, analysis.detections.height) == (96, 64)


def test_analyze_video_samples_every_nth_frame(square_clip):
    analysis = analyze_video(square_clip, detectors(), IouTracker(), every=2)
    person = next(t for t in analysis.tracks if t.label == "person")
    assert [k.t for k in person.keyframes] == pytest.approx([0, 2 / FPS, 4 / FPS, 6 / FPS, 8 / FPS])
    assert person.keyframes[1].box[0] == pytest.approx(12 / 96, abs=2 / 96)  # lossy codec


def test_analyze_needs_detectors_with_distinct_names():
    with pytest.raises(ValueError, match="at least one"):
        analyze_frames(as_frames(square_frames()), [], IouTracker())
    with pytest.raises(ValueError, match="must differ"):
        analyze_frames(as_frames(square_frames()), [ColorDetector(), ColorDetector()], IouTracker())


def test_retrack_from_cached_detections_gives_the_same_tracks():
    analysis = analyze_frames(as_frames(square_frames()), detectors(), IouTracker())
    assert retrack(analysis.detections, IouTracker()) == analysis.tracks


def test_cache_keys_are_stable_safe_and_change_with_every_setting():
    key = cache.detections_key(detectors(), 1)
    assert key == cache.detections_key(detectors(), 1)
    assert key.startswith("stub-red_stub-blue-k1-")
    assert key != cache.detections_key(detectors(), 2)
    assert key != cache.detections_key([ColorDetector(color="red", tolerance=10)], 1)
    tracks = cache.tracks_key(key, "iou", TrackerSettings())
    assert tracks.startswith(f"{key}-iou-")
    assert tracks != cache.tracks_key(key, "iou", TrackerSettings(min_conf=0.5))
    assert tracks != cache.tracks_key(key, "bytetrack", TrackerSettings())
    for k in (key, tracks):
        assert _SAFE_KEY.match(k)


def test_a_very_long_key_still_fits_and_stays_unique():
    many = [ColorDetector(label="person", color="red", tolerance=i) for i in range(30)]
    for i, det in enumerate(many):
        det.name = f"a-rather-long-detector-name-{i}"
    key = cache.detections_key(many, 1)
    assert _SAFE_KEY.match(key)
    # Only the readable part is cut; the hash at the end still sees every detector.
    assert cache.detections_key(many[:-1] + [ColorDetector(tolerance=99)], 1) != key


@pytest.fixture
def storage(tmp_path, square_clip):
    store = Storage(tmp_path / "data")
    return store, store.add_video(square_clip)


def test_detections_and_tracks_round_trip_through_the_cache(storage):
    store, ref = storage
    analysis = analyze_frames(as_frames(square_frames()), detectors(), IouTracker())
    det_key = cache.detections_key(detectors(), 1)
    cache.save_detections(store, ref.sha256, det_key, analysis.detections)
    assert cache.load_detections(store, ref.sha256, det_key) == analysis.detections

    trk_key = cache.tracks_key(det_key, "iou", TrackerSettings())
    written = cache.save_tracks(store, ref.sha256, trk_key, analysis.tracks)
    assert cache.load_tracks(store, ref.sha256) == written
    assert cache.track_keys(store, ref.sha256) == [trk_key]
    assert cache.detection_keys(store, ref.sha256) == [det_key]


def test_saved_track_ids_carry_the_key_so_two_analyses_never_collide(storage):
    store, ref = storage
    analysis = analyze_frames(as_frames(square_frames()), detectors(), IouTracker())
    a = cache.save_tracks(store, ref.sha256, "one-aaaa1111", analysis.tracks)
    b = cache.save_tracks(store, ref.sha256, "two-bbbb2222", analysis.tracks)
    assert [t.track_id for t in a] == ["trk_aaaa1111_1", "trk_aaaa1111_2"]
    assert not {t.track_id for t in a} & {t.track_id for t in b}


def test_no_analysis_is_none_not_an_empty_list(storage):
    """The toolbox fails "blur all faces" on None instead of masking nothing."""
    store, ref = storage
    assert cache.load_tracks(store, ref.sha256) is None
    assert cache.load_tracks(store, ref.sha256, "missing-00000000") is None
    cache.save_tracks(store, ref.sha256, "empty-00000000", [])
    assert cache.load_tracks(store, ref.sha256) == []


def test_several_cached_analyses_need_an_explicit_key(storage):
    store, ref = storage
    analysis = analyze_frames(as_frames(square_frames()), detectors(), IouTracker())
    cache.save_tracks(store, ref.sha256, "one-aaaa1111", analysis.tracks)
    cache.save_tracks(store, ref.sha256, "two-bbbb2222", analysis.tracks[:1])
    with pytest.raises(cache.AmbiguousAnalysis, match="one-aaaa1111, two-bbbb2222"):
        cache.load_tracks(store, ref.sha256)
    assert len(cache.load_tracks(store, ref.sha256, "two-bbbb2222")) == 1


def test_project_tracks_adds_hand_drawn_tracks_but_needs_an_analysis(tmp_path, square_clip):
    store = Storage(tmp_path / "data")
    project = store.create_project(square_clip)
    drawn = Track(
        track_id="usr_1",
        label="person",
        source="user",
        keyframes=[Keyframe(t=0, box=(0.1, 0.1, 0.2, 0.2), conf=1, state="user")],
    )
    project.save_user_tracks([drawn])
    # Drawn boxes alone must not make "blur every face" look done.
    assert cache.project_tracks(store, project) is None

    analysis = analyze_frames(as_frames(square_frames()), detectors(), IouTracker())
    cache.save_tracks(store, project.info.video.sha256, "k-aaaa1111", analysis.tracks)
    tracks = cache.project_tracks(store, project)
    assert [t.track_id for t in tracks] == ["trk_aaaa1111_1", "trk_aaaa1111_2", "usr_1"]
