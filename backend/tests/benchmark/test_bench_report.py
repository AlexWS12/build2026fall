"""F-09 report: the exit check (runs on the stub backend and prints a table), scoring cached
tracks against labels, and the CLI."""

from pathlib import Path

import pytest
from pydantic import TypeAdapter

from vibe.benchmark import labels as labels_mod
from vibe.benchmark import manifest as manifest_mod
from vibe.benchmark.__main__ import main
from vibe.benchmark.labels import ClipLabels
from vibe.benchmark.manifest import ClipEntry, atomic_write
from vibe.benchmark.report import Report, build_report, render_markdown, render_text, run_info
from vibe.contract import Keyframe, Track
from vibe.storage import Storage

FACE = (0.40, 0.20, 0.60, 0.50)
TRACKS = TypeAdapter(list[Track])


def face_track(track_id: str, source: str = "detector") -> Track:
    state = "user" if source == "user" else "detected"
    return Track(
        track_id=track_id,
        label="face",
        source=source,  # type: ignore[arg-type]
        keyframes=[Keyframe(t=t, box=FACE, conf=1.0, state=state) for t in (0.0, 0.5)],
    )


def write_labels(entry: ClipEntry, labels_dir: Path, sha256: str | None = None) -> None:
    labels = ClipLabels(
        clip_id=entry.clip_id,
        sha256=sha256 or entry.video.sha256,
        tracks=[face_track("gt_face", source="user")],
    )
    atomic_write(labels_dir / f"{entry.clip_id}.json", labels.model_dump_json(indent=2))


def cache_tracks(storage: Storage, entry: ClipEntry, key: str, content: str) -> None:
    path = storage.cache_path(entry.video.sha256, "tracks", key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture(autouse=True)
def stub_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VIBE_BACKEND", raising=False)


# --- Exit check ---------------------------------------------------------------------------


def test_report_runs_on_the_stub_backend_and_prints_a_table(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """F-09's exit check, on the committed manifest. On CI the clips aren't there: that's
    reported as missing, not as a failure."""
    monkeypatch.setenv("VIBE_DATA_DIR", str(tmp_path / "data"))
    code = main(["report"])
    out = capsys.readouterr().out
    assert code == 0, out
    assert "Vibe benchmark report (F-09)" in out
    assert "backend stub" in out and "hardware:" in out
    assert "Clips (manifest:" in out and "Vision vs ground truth" in out


# --- Scoring cached tracks ----------------------------------------------------------------


def test_cached_tracks_are_scored_side_by_side(
    entry: ClipEntry, manifest_path: Path, labels_dir: Path, storage: Storage
) -> None:
    write_labels(entry, labels_dir)
    cache_tracks(storage, entry, "model-a", TRACKS.dump_json([face_track("p1")]).decode())
    cache_tracks(storage, entry, "model-b", "[]")

    report = build_report(manifest_path, labels_dir, storage)

    assert report.problems == []
    [clip] = report.clips
    assert (clip.status, clip.labels, clip.label_targets, clip.label_keyframes) == (
        "ok",
        "ok",
        1,
        2,
    )
    a, b = report.scores
    assert (a.tracks_key, b.tracks_key) == ("model-a", "model-b")
    assert a.score is not None and (a.score.total.recall, a.score.total.exposed) == (1.0, 0)
    assert b.score is not None and (b.score.total.recall, b.score.total.exposed) == (0.0, 2)
    text = render_text(report)
    assert "model-a" in text and "100.0%" in text and "2 (100.0%)" in text
    assert "| clip | tracks | label |" in render_markdown(report)


def test_unreadable_cached_tracks_are_a_problem_not_a_crash(
    entry: ClipEntry, manifest_path: Path, labels_dir: Path, storage: Storage
) -> None:
    write_labels(entry, labels_dir)
    cache_tracks(storage, entry, "broken", '{"not": "tracks"}')
    report = build_report(manifest_path, labels_dir, storage)
    [row] = report.scores
    assert row.status == "unreadable"
    assert any("broken.json" in p for p in report.problems)


def test_labels_for_the_wrong_video_are_a_problem_and_not_scored(
    entry: ClipEntry, manifest_path: Path, labels_dir: Path, storage: Storage
) -> None:
    write_labels(entry, labels_dir, sha256="0" * 64)
    cache_tracks(storage, entry, "model-a", "[]")
    report = build_report(manifest_path, labels_dir, storage)
    assert report.clips[0].labels == "invalid"
    assert report.scores == []
    assert any("different video" in p for p in report.problems)


def test_a_clip_missing_on_this_machine_is_not_a_problem(
    entry: ClipEntry, manifest_path: Path, labels_dir: Path, tmp_path: Path
) -> None:
    report = build_report(manifest_path, labels_dir, Storage(tmp_path / "elsewhere"))
    assert report.clips[0].status == "missing"
    assert report.problems == []
    assert "No clips" not in render_text(report)


def test_a_tampered_clip_is_a_problem(
    entry: ClipEntry, manifest_path: Path, labels_dir: Path, storage: Storage
) -> None:
    stored = storage.video_path(entry.video)
    stored.write_bytes(stored.read_bytes() + b"x")
    report = build_report(manifest_path, labels_dir, storage)
    assert any("size_mismatch" in p for p in report.problems)


def test_report_round_trips_through_json_and_prints_ascii(
    entry: ClipEntry, manifest_path: Path, labels_dir: Path, storage: Storage
) -> None:
    write_labels(entry, labels_dir)
    cache_tracks(storage, entry, "model-a", TRACKS.dump_json([face_track("p1")]).decode())
    report = build_report(manifest_path, labels_dir, storage, verify=True)
    assert Report.model_validate_json(report.model_dump_json()) == report
    render_text(report).encode("ascii")  # Windows consoles piping cp1252 choke on anything else
    render_markdown(report).encode("ascii")


def test_run_info_states_the_hardware() -> None:
    info = run_info()
    assert info.platform and info.cpu and info.gpu and info.python
    assert info.backend == "stub"


# --- CLI ----------------------------------------------------------------------------------


@pytest.fixture
def cli_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, manifest_path: Path) -> Path:
    """Point the CLI at a temp manifest, labels dir and data dir (never the committed ones)."""
    monkeypatch.setattr(manifest_mod, "MANIFEST_PATH", manifest_path)
    monkeypatch.setattr(labels_mod, "LABELS_DIR", tmp_path / "labels")
    monkeypatch.setenv("VIBE_DATA_DIR", str(tmp_path / "data"))
    return tmp_path


def test_cli_registers_a_clip_starts_labels_and_reports(
    cli_paths: Path, clip: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["add-clip", str(clip)]) == 1
    assert "--id" in capsys.readouterr().err

    args = ["--id", "street-walk", "--source", "Jean", "--consent", "agreed", "--tag", "faces"]
    assert main(["add-clip", str(clip), *args]) == 0
    assert "added street-walk" in capsys.readouterr().out
    assert main(["add-clip", str(clip)]) == 0
    assert "already in the manifest" in capsys.readouterr().out

    assert main(["new-labels", "street-walk"]) == 0
    assert (cli_paths / "labels" / "street-walk.json").is_file()
    assert main(["new-labels", "nope"]) == 1

    out_dir = cli_paths / "out"
    assert main(["report", "--out", str(out_dir)]) == 0
    out = capsys.readouterr().out
    assert "street-walk" in out and "ok (0 targets, 0 kf)" in out
    report = Report.model_validate_json((out_dir / "report.json").read_text(encoding="utf-8"))
    assert [c.clip_id for c in report.clips] == ["street-walk"]
    assert (out_dir / "report.md").read_text(encoding="utf-8").startswith("# Vibe benchmark")


def test_cli_rejects_a_file_that_isnt_video(
    cli_paths: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    junk = cli_paths / "junk.mp4"
    junk.write_bytes(b"not a video")
    assert main(["add-clip", str(junk), "--id", "x", "--source", "s", "--consent", "c"]) == 1
    assert capsys.readouterr().err.startswith("error:")
