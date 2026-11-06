"""The render CLI: renders a timeline file or a project; bad input gets a message, not a trace."""

import json
from pathlib import Path

import pytest

from vibe.contract import Cut, Mute, TimeRange
from vibe.render.__main__ import main
from vibe.storage import Storage
from vibe.video import probe

HERE = Path(__file__).parent
SESSION = HERE / "render_session.jsonl"
TRACKS = HERE / "render_session.tracks.json"


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def test_help_lists_export(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["--help"])
    assert "export" in capsys.readouterr().out


def test_export_renders_a_timeline_file(
    capsys: pytest.CaptureFixture[str], pattern_clip: Path, tmp_path: Path
) -> None:
    out_path = tmp_path / "out.mp4"
    code, out, err = run(
        capsys, "export", str(pattern_clip), "--log", str(SESSION), "--tracks", str(TRACKS),
        "-o", str(out_path),
    )  # fmt: skip
    assert code == 0, err
    summary = json.loads(out)
    assert summary["path"] == str(out_path)
    assert summary["frames"] == 75
    assert summary["issues"] == []
    assert probe(out_path).duration == pytest.approx(2.5, abs=1 / 30)


def test_project_export_writes_a_new_file_each_time(
    capsys: pytest.CaptureFixture[str],
    pattern_clip: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VIBE_DATA_DIR", str(tmp_path))
    project = Storage(tmp_path).create_project(pattern_clip)
    project.append(Cut(request_id="r_1", source="agent", time_range=TimeRange(start=0, end=1)))
    project.append(Mute(request_id="r_2", source="agent"))

    paths = []
    for _ in range(2):
        code, out, err = run(capsys, "export", "--project", project.id)
        assert code == 0, err
        paths.append(Path(json.loads(out)["path"]))
    assert paths[0] != paths[1]  # a new URL every time, so nothing serves a stale export
    assert all(p.parent == project.exports_dir and p.is_file() for p in paths)
    assert probe(paths[0]).duration == pytest.approx(2.0, abs=1 / 30)


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["export", "clip.mp4", "--log", "missing.jsonl", "-o", "out.mp4"], "can't read timeline"),
        (["export", "--log", str(SESSION), "-o", "out.mp4"], "pass the clip"),
        (["export", "clip.mp4", "--log", str(SESSION)], "-o/--output"),
        (["export", "--project", "prj_nope"], "no project"),
        (["export", "clip.mp4", "--project", "prj_nope"], "don't pass a clip"),
        (["export", "missing.mp4", "--log", str(SESSION), "-o", "out.mp4"], "missing.mp4"),
    ],
)
def test_bad_input_is_an_error_not_a_traceback(
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    message: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VIBE_DATA_DIR", str(tmp_path))
    code, out, err = run(capsys, *argv)
    assert code == 1
    assert err.startswith("error: ") and message in err
    assert out == ""
    assert not (tmp_path / "out.mp4").exists()


def test_output_that_cant_be_written_is_an_error(
    capsys: pytest.CaptureFixture[str], pattern_clip: Path, tmp_path: Path
) -> None:
    out_path = tmp_path / "out.mp4"
    out_path.mkdir()  # a folder in the way: PermissionError or IsADirectoryError, by platform
    code, _, err = run(
        capsys, "export", str(pattern_clip), "--log", str(SESSION), "--tracks", str(TRACKS),
        "-o", str(out_path),
    )  # fmt: skip
    assert code == 1
    assert err.startswith("error: ")
    assert [p.name for p in tmp_path.iterdir()] == ["out.mp4"]  # no temporary file left behind


def test_overwriting_the_source_is_an_error(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"a video")
    code, _, err = run(capsys, "export", str(clip), "--log", str(SESSION), "-o", str(clip))
    assert code == 1
    assert "overwrite" in err
    assert clip.read_bytes() == b"a video"


def test_bad_tracks_file_is_an_error(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    bad = tmp_path / "tracks.json"
    bad.write_text('[{"track_id": "trk_1"}]')
    code, _, err = run(
        capsys, "export", "clip.mp4", "--log", str(SESSION), "--tracks", str(bad),
        "-o", str(tmp_path / "out.mp4"),
    )  # fmt: skip
    assert code == 1
    assert "can't read tracks" in err


def test_unsupported_edit_is_an_error(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    log = tmp_path / "timeline.jsonl"
    log.write_text(
        '{"schema_version": 1}\n'
        '{"kind": "mute", "op_id": "op_1", "request_id": "r_1", "source": "agent", '
        '"speaker": "spk_1"}\n'
    )
    code, _, err = run(capsys, "export", "clip.mp4", "--log", str(log), "-o", "out.mp4")
    assert code == 1
    assert "F-32" in err
