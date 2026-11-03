"""Fixtures for the F-09 tests: temp data dir, manifest and labels dir, and tiny clips that the
ffmpeg CLI generates once per session (no video is committed).

The ffmpeg flags here work on ffmpeg 4.4 and later. Without ffmpeg the clip tests are skipped;
CI sets ``VIBE_REQUIRE_FFMPEG=1`` so a missing ffmpeg fails instead of passing by skipping.
"""

import os
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

import pytest
from bench_helpers import register

from vibe.benchmark import manifest as manifest_mod
from vibe.benchmark.manifest import ClipEntry, ClipManifest, save_manifest
from vibe.storage import Storage
from vibe.video import VideoInfo


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
def fake_clip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A file the manifest reads as a 1 s 160x120 clip (``probe`` is patched), for tests of
    the bookkeeping around a clip that don't need ffmpeg."""
    path = tmp_path / "src" / "clip.mp4"
    path.parent.mkdir()
    path.write_bytes(b"not really a video " * 64)
    info = VideoInfo(
        path=path, duration=1.0, width=160, height=120, rotation=0, fps=Fraction(10),
        time_base=Fraction(1, 10240), frame_count=10, is_vfr=False, start_time=0.0,
        codec="h264", pix_fmt="yuv420p", audio=None,
    )  # fmt: skip
    monkeypatch.setattr(manifest_mod, "probe", lambda _: info)
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
