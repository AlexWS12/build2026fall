"""F-09 labels: ground truth per clip, in the contract's track format, checked against the clip."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from vibe.benchmark.labels import (
    LABELS_DIR,
    ClipLabels,
    SpeechSegment,
    check_labels,
    load_labels,
    write_labels_template,
)
from vibe.benchmark.manifest import MANIFEST_PATH, BenchmarkError, ClipEntry, load_manifest
from vibe.contract import Keyframe, Track

FACE = (0.40, 0.20, 0.60, 0.50)


def face(track_id: str, *times: float) -> Track:
    return Track(
        track_id=track_id,
        label="face",
        source="user",
        keyframes=[Keyframe(t=t, box=FACE, conf=1.0, state="user") for t in times],
    )


def labels_for(entry: ClipEntry, **fields: object) -> ClipLabels:
    return ClipLabels(clip_id=entry.clip_id, sha256=entry.video.sha256, **fields)


def test_committed_labels_match_the_manifest() -> None:
    manifest = load_manifest(MANIFEST_PATH)
    for path in sorted(LABELS_DIR.glob("*.json")) if LABELS_DIR.exists() else []:
        entry = manifest.get(path.stem)
        assert entry is not None, f"{path.name} has no clip in the manifest"
        labels = load_labels(path.stem)
        assert labels is not None
        assert check_labels(labels, entry) == []


def test_template_round_trips_and_is_never_overwritten(entry: ClipEntry, labels_dir: Path) -> None:
    write_labels_template(entry, labels_dir)
    labels = load_labels(entry.clip_id, labels_dir)
    assert labels == labels_for(entry)
    assert labels is not None and check_labels(labels, entry) == []
    with pytest.raises(BenchmarkError, match="already exists"):
        write_labels_template(entry, labels_dir)


def test_an_unlabelled_clip_has_no_labels(labels_dir: Path) -> None:
    assert load_labels("street-walk", labels_dir) is None


def test_labels_for_another_video_are_flagged(entry: ClipEntry) -> None:
    labels = ClipLabels(clip_id=entry.clip_id, sha256="0" * 64)
    assert any("different video" in p for p in check_labels(labels, entry))


def test_times_past_the_end_are_flagged(entry: ClipEntry) -> None:
    labels = labels_for(
        entry, tracks=[face("f1", 0.0, 5.0)], speech=[SpeechSegment(start=0.5, end=9)]
    )
    [problem] = check_labels(labels, entry)
    assert "f1@5s" in problem and "speech@0.5s" in problem


def test_one_frame_of_rounding_at_the_end_is_allowed(entry: ClipEntry) -> None:
    assert check_labels(labels_for(entry, tracks=[face("f1", entry.duration)]), entry) == []


def test_duplicate_track_ids_are_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate track_id"):
        ClipLabels(clip_id="c", sha256="0" * 64, tracks=[face("f1", 0), face("f1", 1)])


def test_speech_must_end_after_it_starts() -> None:
    with pytest.raises(ValidationError):
        SpeechSegment(start=2, end=2)


def test_a_bad_file_names_itself(labels_dir: Path) -> None:
    labels_dir.mkdir()
    path = labels_dir / "street-walk.json"
    path.write_text('{"labels_version": 1, "clip_id": "street-walk"}')
    with pytest.raises(BenchmarkError, match="street-walk.json"):
        load_labels("street-walk", labels_dir)
    path.write_text("{nope")
    with pytest.raises(BenchmarkError, match="not valid JSON"):
        load_labels("street-walk", labels_dir)
    path.write_text('{"labels_version": 9}')
    with pytest.raises(BenchmarkError, match="labels_version 9"):
        load_labels("street-walk", labels_dir)
