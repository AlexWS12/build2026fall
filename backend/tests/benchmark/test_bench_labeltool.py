"""The label helper (F-09b): frames for a page to draw boxes on, and importing what was drawn."""

import json
from pathlib import Path

import pytest

from vibe.benchmark import labeltool
from vibe.benchmark.labels import ClipLabels
from vibe.benchmark.manifest import BenchmarkError, ClipEntry
from vibe.contract import Keyframe, Track
from vibe.storage import Storage


def saved(entry: ClipEntry, frames: list[dict], label: str = "face") -> dict:
    """What the page's Save button downloads."""
    return {
        "version": labeltool.LABEL_FILE_VERSION,
        "clip_id": entry.clip_id,
        "sha256": entry.video.sha256,
        "label": label,
        "frames": frames,
    }


def person(track_id: str = "m1") -> Track:
    return Track(
        track_id=track_id,
        label="person",
        source="user",
        keyframes=[Keyframe(t=0.0, box=(0.1, 0.1, 0.5, 0.9), conf=1.0, state="user")],
    )


def test_sample_times_end_on_the_last_moment() -> None:
    assert labeltool.sample_times(2.5) == [0.0, 1.0, 2.0, 2.5]
    assert labeltool.sample_times(2.0) == [0.0, 1.0, 2.0]
    assert labeltool.sample_times(1.0, step=0.5) == [0.0, 0.5, 1.0]
    with pytest.raises(ValueError):
        labeltool.sample_times(0)


def test_frames_are_jpegs_at_the_sample_times(entry: ClipEntry, storage: Storage) -> None:
    frames = labeltool.sample_frames(storage.video_path(entry.video), entry.duration, step=0.5)

    times = [t for t, _ in frames]
    assert times[0] == 0.0 and times == sorted(set(times))
    assert len(frames) == 3  # 0, 0.5 and the last frame of a 1 s clip
    assert all(jpg[:3] == b"\xff\xd8\xff" for _, jpg in frames)


def test_page_shows_the_labels_boxes_to_correct(
    entry: ClipEntry, storage: Storage, tmp_path: Path
) -> None:
    frames = labeltool.sample_frames(storage.video_path(entry.video), entry.duration)
    labels = ClipLabels(clip_id=entry.clip_id, sha256=entry.video.sha256, tracks=[person()])

    data = labeltool.page_data(entry, frames, "person", labels)
    page = labeltool.write_label_page(data, tmp_path / "out")

    assert data["boxes"][0] == [{"track": 1, "box": [0.1, 0.1, 0.5, 0.9]}]
    assert labeltool.page_data(entry, frames, "face", labels)["boxes"][0] == []
    html = page.read_text("utf-8")
    assert "/*DATA*/null" not in html and entry.clip_id in html
    assert html.count("<script>") == 1  # nothing in the data closed the script early


def test_import_makes_one_track_per_number_and_keeps_other_labels(entry: ClipEntry) -> None:
    labels = ClipLabels(clip_id=entry.clip_id, sha256=entry.video.sha256, tracks=[person()])
    file = saved(
        entry,
        [
            {"t": 0.0, "boxes": [{"track": 2, "box": [0.6, 0.2, 0.4, 0.1]}]},  # drawn backwards
            {
                "t": 0.5,
                "boxes": [
                    {"track": 2, "box": [0.4, 0.1, 0.6, 0.2]},
                    {"track": 1, "box": [0.0, 0.0, 0.001, 0.5]},
                ],
            },  # track 1 is a click
        ],
    )

    merged = labeltool.import_label_file(file, entry, labels)

    assert [t.track_id for t in merged.tracks] == ["m1", "face_2"]
    face = merged.tracks[1]
    assert (face.label, face.source) == ("face", "user")
    assert [(k.t, k.box, k.conf, k.state) for k in face.keyframes] == [
        (0.0, (0.4, 0.1, 0.6, 0.2), 1.0, "user"),
        (0.5, (0.4, 0.1, 0.6, 0.2), 1.0, "user"),
    ]


def test_importing_again_replaces_that_labels_tracks(entry: ClipEntry) -> None:
    first = labeltool.import_label_file(
        saved(entry, [{"t": 0.0, "boxes": [{"track": 1, "box": [0.1, 0.1, 0.2, 0.2]}]}]),
        entry,
        None,
    )
    again = labeltool.import_label_file(
        saved(entry, [{"t": 0.0, "boxes": [{"track": 3, "box": [0.1, 0.1, 0.2, 0.2]}]}]),
        entry,
        first,
    )
    assert [t.track_id for t in again.tracks] == ["face_3"]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"clip_id": "other"}, "not 'street-walk'"),
        ({"sha256": "0" * 64}, "not 'street-walk'"),
        ({"version": 99}, "not a label-frames file"),
        ({"label": ""}, "names no label"),
        (
            {"frames": [{"t": 60.0, "boxes": [{"track": 1, "box": [0.1, 0.1, 0.2, 0.2]}]}]},
            "past the clip's end",
        ),
    ],
)
def test_import_refuses_files_that_dont_fit(entry: ClipEntry, change: dict, message: str) -> None:
    file = {**saved(entry, []), **change}
    with pytest.raises(BenchmarkError, match=message):
        labeltool.import_label_file(file, entry, None)


def test_cli_imports_a_saved_file(
    entry: ClipEntry,
    labels_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    manifest_path: Path,
) -> None:
    from vibe.benchmark import __main__ as cli
    from vibe.benchmark import labels as labels_mod
    from vibe.benchmark import manifest as manifest_mod

    monkeypatch.setattr(manifest_mod, "MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(labels_mod, "LABELS_DIR", labels_dir)
    file = tmp_path / "saved.json"
    file.write_text(
        json.dumps(saved(entry, [{"t": 0.0, "boxes": [{"track": 1, "box": [0.1, 0.1, 0.3, 0.3]}]}]))
    )

    assert cli.main(["import-labels", entry.clip_id, str(file)]) == 0

    written = labels_mod.load_labels(entry.clip_id, labels_dir)
    assert written is not None and [t.track_id for t in written.tracks] == ["face_1"]
