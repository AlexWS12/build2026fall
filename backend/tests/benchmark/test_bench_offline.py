"""The offline bake-off runner (F-10b): timed detector runs cached where ``report`` scores them."""

from pathlib import Path

import pytest

from vibe.benchmark import offline
from vibe.benchmark.labels import ClipLabels
from vibe.benchmark.manifest import ClipEntry
from vibe.benchmark.report import build_report
from vibe.contract import Keyframe, Track
from vibe.modules.vision import cache
from vibe.storage import Storage

# testsrc2 has saturated color blocks; tolerance keeps the stub finding one despite H.264.
STUB = "stub:color=red,label=face,tolerance=120"


def test_a_run_is_timed_and_cached_under_the_cli_keys(entry: ClipEntry, storage: Storage) -> None:
    row = offline.run_one(entry, storage, [STUB], every=2)

    assert row.status == "ran", row.detail
    assert row.frames == 5  # 1 s at 10 fps, every 2nd frame
    assert {"decode", "track", "total"} <= set(row.seconds)
    assert row.seconds_per_frame is not None and row.seconds_per_frame > 0
    assert row.peak_rss_mb and row.peak_rss_mb > 0
    assert row.tracks_key in cache.track_keys(storage, entry.video.sha256)
    assert sum(row.tracks_by_label.values()) == len(
        cache.load_tracks(storage, entry.video.sha256, row.tracks_key) or []
    )


def test_a_detector_that_cant_load_is_a_skipped_row(
    entry: ClipEntry, storage: Storage, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIBE_MODELS_DIR", str(tmp_path / "none"))

    row = offline.run_one(entry, storage, ["yunet:weights=@yunet"], every=1)

    assert row.status == "skipped"
    assert "yunet" in row.detail
    assert not cache.track_keys(storage, entry.video.sha256)


def test_a_clip_not_stored_here_is_skipped(entry: ClipEntry, tmp_path: Path) -> None:
    row = offline.run_one(entry, Storage(tmp_path / "elsewhere"), [STUB], every=1)
    assert (row.status, row.detail) == ("skipped", "the clip isn't stored on this machine")


def test_every_combination_is_a_row(entry: ClipEntry, storage: Storage) -> None:
    report = offline.run_offline([entry], storage, [[STUB], ["nope"]], everies=[1, 3])
    assert [(r.detectors[0], r.every, r.status) for r in report.rows] == [
        (STUB, 1, "ran"),
        (STUB, 3, "ran"),
        ("nope", 1, "skipped"),
        ("nope", 3, "skipped"),
    ]
    assert report.run.cpu  # the hardware goes with the numbers


def test_markdown_states_hardware_and_tracks_per_labelled_target(
    entry: ClipEntry, storage: Storage, manifest_path: Path, labels_dir: Path
) -> None:
    report = offline.run_offline([entry], storage, [[STUB]])
    truth = Track(
        track_id="face_1",
        label="face",
        source="user",
        keyframes=[Keyframe(t=0.0, box=(0.1, 0.1, 0.3, 0.3), conf=1.0, state="user")],
    )
    labels = ClipLabels(clip_id=entry.clip_id, sha256=entry.video.sha256, tracks=[truth])

    text = offline.render_markdown(report, {entry.clip_id: labels})

    assert "hardware:" in text and "Wall time on this machine only" in text
    found = report.rows[0].tracks_by_label.get("face", 0)
    if found:
        assert f"face {found / 1:.1f}" in text
    # And the cached tracks are what `report` scores.
    (labels_dir).mkdir(exist_ok=True)
    (labels_dir / f"{entry.clip_id}.json").write_text(labels.model_dump_json())
    scored = build_report(manifest_path=manifest_path, labels_dir=labels_dir, storage=storage)
    assert [s.tracks_key for s in scored.scores] == [report.rows[0].tracks_key]


def test_stitched_runs_get_their_own_key(entry: ClipEntry, storage: Storage) -> None:
    from vibe.modules.vision.stitch import StitchSettings

    plain = offline.run_one(entry, storage, [STUB], every=1)
    stitched = offline.run_one(entry, storage, [STUB], every=1, stitch=StitchSettings())

    assert stitched.status == "ran" and stitched.post == ["stitch"]
    assert stitched.tracks_key != plain.tracks_key and stitched.tracks_key.split("-")[-2] == "post"
