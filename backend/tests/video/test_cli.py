"""python -m vibe.video probe: JSON on stdout, or an error message and exit code 1."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from clip_spec import FRAMES, HEIGHT, WIDTH

from vibe.video.__main__ import main


def test_probe_prints_json(main_clip: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["probe", str(main_clip)]) == 0
    info = json.loads(capsys.readouterr().out)
    assert info["path"] == str(main_clip)
    assert (info["width"], info["height"]) == (WIDTH, HEIGHT)
    assert info["fps"] == "30"
    assert info["frame_count"] == FRAMES
    assert info["audio"]["codec"] == "aac"


def test_probe_error(junk_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["probe", str(junk_file)]) == 1
    assert capsys.readouterr().err.startswith("error: ")


def test_runs_as_a_module(main_clip: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "vibe.video", "probe", str(main_clip)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout)["frame_count"] == FRAMES
