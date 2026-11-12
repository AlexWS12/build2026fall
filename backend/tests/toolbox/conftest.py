"""A 90-second test clip for the F-12 round-trip tests, generated with the ffmpeg CLI once per
session (no video is committed), like ``tests/benchmark/conftest.py``.

Without ffmpeg on PATH the clip tests are skipped; CI sets ``VIBE_REQUIRE_FFMPEG=1`` so a missing
ffmpeg fails instead of passing by skipping.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

LONG_CLIP_SECONDS = 90
LONG_CLIP_FPS = 10


@pytest.fixture(scope="session")
def long_clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """64x48, 10 fps, 90 s of testsrc2 with an AAC tone. Tiny, so it renders quickly."""
    if shutil.which("ffmpeg") is None:
        message = "ffmpeg is not on PATH (needed to generate the test clips)"
        if os.environ.get("VIBE_REQUIRE_FFMPEG") == "1":
            pytest.fail(message)
        pytest.skip(message)
    path = tmp_path_factory.mktemp("toolbox-clips") / "long.mp4"
    d = LONG_CLIP_SECONDS
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", f"testsrc2=s=64x48:r={LONG_CLIP_FPS}:d={d}",
            "-f", "lavfi", "-i", f"sine=f=440:r=16000:d={d}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
            str(path),
        ],
        check=True,
    )  # fmt: skip
    return path
