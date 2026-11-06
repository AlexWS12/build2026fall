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
from render_clip import FPS, HEIGHT, LONG_SECONDS, SAMPLE_RATE, SECONDS, TONE_HZ, WIDTH


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
def long_clip(render_clip_dir: Path) -> Path:
    """12 s with a tone. Small, so it renders quickly."""
    path = render_clip_dir / "long.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", f"testsrc2=s=160x120:r={FPS}:d={LONG_SECONDS}",
        "-f", "lavfi", "-i", f"sine=f={TONE_HZ}:r={SAMPLE_RATE}:d={LONG_SECONDS}",
        "-ac", "2",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
        str(path),
    )  # fmt: skip
    return path


@pytest.fixture(scope="session")
def hd_red_clip(render_clip_dir: Path) -> Path:
    """Flat red at 1280×720, in BT.709 and tagged so, like a phone's footage."""
    path = render_clip_dir / "hd_red.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", f"color=c=red:s=1280x720:r={FPS}:d=0.2",
        "-vf", "scale=out_color_matrix=bt709,format=yuv420p",
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-c:v", "libx264", "-qp", "0",
        str(path),
    )  # fmt: skip
    return path


def _clip_with_audio(path: Path, *audio_args: str) -> Path:
    """One second of the pattern (lossless, so MKV holds it) plus the tone, encoded as asked."""
    _ffmpeg(
        "-f", "lavfi", "-i", f"testsrc2=s={WIDTH}x{HEIGHT}:r={FPS}:d=1",
        "-f", "lavfi", "-i", f"sine=f={TONE_HZ}:r={SAMPLE_RATE}:d=1",
        "-c:v", "ffv1", *audio_args, "-shortest",
        str(path),
    )  # fmt: skip
    return path


@pytest.fixture(scope="session")
def mono_clip(render_clip_dir: Path) -> Path:
    return _clip_with_audio(render_clip_dir / "mono.mkv", "-ac", "1", "-c:a", "aac")


@pytest.fixture(scope="session")
def unnamed_layout_clip(render_clip_dir: Path) -> Path:
    """Stereo PCM in MKV, which doesn't name its layout: PyAV calls it "2 channels"."""
    return _clip_with_audio(render_clip_dir / "unnamed.mkv", "-ac", "2", "-c:a", "pcm_s16le")


@pytest.fixture(scope="session")
def surround_clip(render_clip_dir: Path) -> Path:
    """7.1 audio, a layout the AAC encoder refuses."""
    return _clip_with_audio(
        render_clip_dir / "surround.mkv", "-af", "aformat=channel_layouts=7.1", "-c:a", "flac"
    )


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
