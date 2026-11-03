"""Test clips for the renderer, generated with the ffmpeg CLI once per test session.

Like the video tests (``tests/video/conftest.py``): without ffmpeg on PATH these tests are
skipped, and ``VIBE_REQUIRE_FFMPEG=1`` (CI sets it) makes a missing ffmpeg fail instead. No video
is committed.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from render_clip import FPS, HEIGHT, SAMPLE_RATE, SECONDS, TONE_HZ, WIDTH


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


@pytest.fixture(scope="session")
def render_clip_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if shutil.which("ffmpeg") is None:
        message = "ffmpeg is not on PATH (needed to generate the test clips)"
        if os.environ.get("VIBE_REQUIRE_FFMPEG") == "1":
            pytest.fail(message)
        pytest.skip(message)
    return tmp_path_factory.mktemp("render_clips")


@pytest.fixture(scope="session")
def pattern_clip(render_clip_dir: Path) -> Path:
    """320×240, 30 fps, 3 s of ffmpeg's moving test pattern, lossless H.264, plus a 440 Hz tone
    at 48 kHz stereo. The pattern is detailed everywhere, so a blur shows."""
    path = render_clip_dir / "pattern.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", f"testsrc2=s={WIDTH}x{HEIGHT}:r={FPS}:d={SECONDS}",
        "-f", "lavfi", "-i", f"sine=f={TONE_HZ}:r={SAMPLE_RATE}:d={SECONDS}",
        "-ac", "2",
        "-c:v", "libx264", "-qp", "0", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
        str(path),
    )  # fmt: skip
    return path


@pytest.fixture(scope="session")
def silent_pattern_clip(render_clip_dir: Path, pattern_clip: Path) -> Path:
    """The pattern clip with its audio stream removed."""
    path = render_clip_dir / "pattern_silent.mp4"
    _ffmpeg("-i", str(pattern_clip), "-an", "-c", "copy", str(path))
    return path


@pytest.fixture(scope="session")
def odd_size_clip(render_clip_dir: Path) -> Path:
    """321×241 (an odd width and height), lossless FFV1 in MKV, no audio."""
    path = render_clip_dir / "odd.mkv"
    _ffmpeg(
        "-f", "lavfi", "-i", f"testsrc2=s={WIDTH + 1}x{HEIGHT + 1}:r={FPS}:d=1",
        "-c:v", "ffv1",
        str(path),
    )  # fmt: skip
    return path
