"""F-06 exit check: ``python -m vibe.toolbox --help`` lists the tools, and a stub tool writes a
valid op to an example timeline."""

import json
import os
import shutil
import stat
import sys
from pathlib import Path

import pytest

from vibe.contract import Cut, Mute, OpLog
from vibe.toolbox import TOOLS
from vibe.toolbox.__main__ import main

EXAMPLES = Path(__file__).parents[2] / "src" / "vibe" / "contract" / "examples"


@pytest.fixture
def timeline(tmp_path: Path) -> Path:
    path = tmp_path / "timeline.jsonl"
    shutil.copy(EXAMPLES / "blur_first_half.jsonl", path)
    return path


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = main(list(argv))
    out, err = capsys.readouterr()
    return code, out, err


def test_help_lists_every_tool(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exited:
        main(["--help"])

    out = capsys.readouterr().out
    assert exited.value.code == 0
    for name in TOOLS:
        assert name in out


def test_tool_help_lists_its_arguments(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["add_mask", "--help"])

    out = capsys.readouterr().out
    for field in ("targets", "effect", "time_range"):
        assert field in out


def test_stub_tools_write_valid_ops_to_an_example_timeline(
    capsys: pytest.CaptureFixture[str], timeline: Path
) -> None:
    before = len(OpLog.from_jsonl(timeline.read_text()))

    code_cut, out_cut, _ = run(
        capsys, "cut_range", "--log", str(timeline), "--request-id", "req_cut",
        "--args", '{"time_range": {"start": 20, "end": 25}}',
    )  # fmt: skip
    code_mute, out_mute, _ = run(
        capsys, "mute", "--log", str(timeline), "--request-id", "req_mute",
        "--args", '{"time_range": {"start": 30, "end": 40}}',
    )  # fmt: skip

    log = OpLog.from_jsonl(timeline.read_text())
    assert (code_cut, code_mute) == (0, 0)
    assert len(log) == before + 2
    cut, mute = log.ops[-2:]
    assert isinstance(cut, Cut) and isinstance(mute, Mute)
    assert json.loads(out_cut)["op_ids"] == [cut.op_id]
    assert json.loads(out_mute)["op_ids"] == [mute.op_id]
    assert (cut.source, mute.source) == ("user", "user")


def test_query_tool_prints_its_result(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = run(
        capsys, "find_objects", "--tracks", str(EXAMPLES / "street.tracks.json"),
        "--args", '{"label": "person"}',
    )  # fmt: skip

    assert code == 0
    assert json.loads(out)["count"] >= 1


@pytest.mark.parametrize(
    "argv",
    [
        ["cut_range", "--args", '{"time_range": {"start": 5, "end": 1}}'],
        ["mute", "--args", "not json"],
        ["mute", "--args", '["not", "an", "object"]'],
        ["resolve_time", "--args", '{"phrase": "first half"}'],
    ],
)
def test_errors_exit_1_and_leave_the_timeline_unchanged(
    capsys: pytest.CaptureFixture[str], timeline: Path, argv: list[str]
) -> None:
    before = timeline.read_text()

    code, out, err = run(capsys, *argv, "--log", str(timeline))

    assert code == 1
    assert err.startswith("error:")
    assert out == ""
    assert timeline.read_text() == before


def test_edit_tool_without_a_log_is_an_error(capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = run(capsys, "mute", "--args", "{}")

    assert code == 1
    assert "--log" in err


def test_unreadable_timeline_is_an_error_not_a_traceback(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    bad = tmp_path / "bad.jsonl"
    bad.write_bytes(b"\xff\xfe not utf-8")

    code, _, err = run(capsys, "mute", "--log", str(bad))

    assert code == 1
    assert "can't read timeline" in err


def test_query_tools_dont_read_the_timeline(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    bad = tmp_path / "bad.jsonl"
    bad.write_bytes(b"\xff\xfe not utf-8")

    code, out, _ = run(
        capsys, "resolve_time", "--log", str(bad), "--duration", "60",
        "--args", '{"phrase": "first half"}',
    )  # fmt: skip

    assert code == 0
    assert json.loads(out)["time_range"] == {"start": 0.0, "end": 30.0}


def test_writing_keeps_the_timeline_permissions(
    capsys: pytest.CaptureFixture[str], timeline: Path
) -> None:
    timeline.chmod(0o644)

    run(capsys, "mute", "--log", str(timeline))

    assert stat.S_IMODE(timeline.stat().st_mode) == 0o644


@pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0, reason="needs POSIX permissions, not root"
)
def test_unwritable_folder_is_an_error_and_leaves_no_temp_file(
    capsys: pytest.CaptureFixture[str], timeline: Path
) -> None:
    before = timeline.read_text()
    folder = timeline.parent
    folder.chmod(0o555)
    try:
        code, _, err = run(capsys, "mute", "--log", str(timeline))
    finally:
        folder.chmod(0o755)

    assert code == 1
    assert "can't write timeline" in err
    assert timeline.read_text() == before
    assert [p.name for p in folder.iterdir()] == [timeline.name]


def test_zero_length_resolve_time_is_an_error_not_a_traceback(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, _, err = run(
        capsys, "resolve_time", "--duration", "10", "--args", '{"phrase": "first 0 seconds"}'
    )

    assert code == 1
    assert err.startswith("error:")
