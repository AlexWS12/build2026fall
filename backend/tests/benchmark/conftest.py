"""Fixtures for the F-09 tests: temp data dir, manifest and labels dir, and tiny clips that the
ffmpeg CLI generates once per session (no video is committed).

The ffmpeg flags here work on ffmpeg 4.4 and later. Without ffmpeg the clip tests are skipped;
CI sets ``VIBE_REQUIRE_FFMPEG=1`` so a missing ffmpeg fails instead of passing by skipping.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from bench_helpers import register

from vibe.benchmark.manifest import ClipEntry, ClipManifest, save_manifest
from vibe.storage import Storage


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


@pytest.fixture(scope="session")
def clip_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if shutil.which("ffmpeg") is None:
        message = "ffmpeg is not on PATH (needed to generate the test clips)"
        if os.environ.get("VIBE_REQUIRE_FFMPEG") == "1":
            pytest.fail(message)
        pytest.skip(message)
    return tmp_path_factory.mktemp("bench-clips")


@pytest.fixture(scope="session")
def clip(clip_dir: Path) -> Path:
    """160x120, 10 fps, 1 s, H.264 with an AAC tone. Upper-case name and a space, like a phone."""
    path = clip_dir / "Street Walk.MP4"
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=s=160x120:r=10:d=1",
        "-f", "lavfi", "-i", "sine=f=440:r=16000:d=1",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
        "-f", "mp4", str(path),
    )  # fmt: skip
    return path


@pytest.fixture(scope="session")
def other_clip(clip_dir: Path) -> Path:
    """A different video: 128x96, 15 fps, no audio."""
    path = clip_dir / "parking.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc=s=128x96:r=15:d=1",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path),
    )  # fmt: skip
    return path


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    return Storage(tmp_path / "data")


@pytest.fixture
def manifest_path(tmp_path: Path) -> Path:
    path = tmp_path / "clips.json"
    save_manifest(ClipManifest(), path)
    return path


@pytest.fixture
def labels_dir(tmp_path: Path) -> Path:
    return tmp_path / "labels"


@pytest.fixture
def entry(clip: Path, manifest_path: Path, storage: Storage) -> ClipEntry:
    """``clip`` registered in the temp manifest and stored in the temp data dir."""
    return register(clip, manifest_path, storage)
