"""Tiny test clips, generated with the ffmpeg CLI once per test session (no video is committed).

Each frame of the main clip encodes its own index as brightness (see ``clip_spec``), losslessly,
so decode tests can check that a seek landed on exactly the right frame.

Without ffmpeg on PATH the video tests are skipped. Set ``VIBE_REQUIRE_FFMPEG=1`` (CI does) to
make a missing ffmpeg fail instead, so CI can't pass by skipping them.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from clip_spec import (
    AUDIO_DELAY,
    FPS,
    GOP,
    HEIGHT,
    LUMA_BASE,
    LUMA_STEP,
    SAMPLE_RATE,
    SECONDS,
    TONE_HZ,
    VFR_DROP,
    WIDTH,
)


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


@pytest.fixture(scope="session")
def clip_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if shutil.which("ffmpeg") is None:
        message = "ffmpeg is not on PATH (needed to generate the test clips)"
        if os.environ.get("VIBE_REQUIRE_FFMPEG") == "1":
            pytest.fail(message)
        pytest.skip(message)
    return tmp_path_factory.mktemp("clips")


@pytest.fixture(scope="session")
def main_clip(clip_dir: Path) -> Path:
    """320×240, 30 fps, 2 s, lossless H.264, plus a 440 Hz tone at 48 kHz stereo."""
    path = clip_dir / "main.mp4"
    video = (
        f"nullsrc=s={WIDTH}x{HEIGHT}:r={FPS}:d={SECONDS},"
        f"geq=lum='{LUMA_BASE}+N*{LUMA_STEP}':cb=128:cr=128"
    )
    _ffmpeg(
        "-f", "lavfi", "-i", video,
        "-f", "lavfi", "-i", f"sine=f={TONE_HZ}:r={SAMPLE_RATE}:d={SECONDS}",
        "-ac", "2",
        "-c:v", "libx264", "-qp", "0", "-g", str(GOP), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
        str(path),
    )  # fmt: skip
    return path


@pytest.fixture(scope="session")
def vfr_clip(clip_dir: Path, main_clip: Path) -> Path:
    """The main clip with one frame in five dropped and timestamps kept: variable frame rate."""
    path = clip_dir / "vfr.mp4"
    _ffmpeg(
        "-i", str(main_clip),
        "-vf", f"select='not(eq(mod(n\\,{VFR_DROP})\\,2))'", "-fps_mode", "passthrough",
        "-an", "-c:v", "libx264", "-qp", "0", "-g", str(GOP),
        str(path),
    )  # fmt: skip
    return path


@pytest.fixture(scope="session")
def bframes_clip(clip_dir: Path) -> Path:
    """Lossy H.264 with B-frames, so packets arrive out of display order."""
    path = clip_dir / "bframes.mp4"
    _ffmpeg(
        "-f", "lavfi", "-i", f"testsrc2=s={WIDTH}x{HEIGHT}:r={FPS}:d={SECONDS}",
        "-c:v", "libx264", "-bf", "3", "-g", str(GOP), "-pix_fmt", "yuv420p",
        str(path),
    )  # fmt: skip
    return path


@pytest.fixture(scope="session")
def webm_clip(clip_dir: Path, main_clip: Path) -> Path:
    """The main clip as lossless VP9 in WebM: 1 ms timestamps and no frame count in the header."""
    path = clip_dir / "main.webm"
    _ffmpeg(
        "-i", str(main_clip),
        "-an", "-c:v", "libvpx-vp9", "-lossless", "1", "-g", str(GOP),
        str(path),
    )  # fmt: skip
    return path


@pytest.fixture(scope="session")
def silent_clip(clip_dir: Path, main_clip: Path) -> Path:
    """The main clip with its audio stream removed."""
    path = clip_dir / "silent.mp4"
    _ffmpeg("-i", str(main_clip), "-an", "-c", "copy", str(path))
    return path


@pytest.fixture(scope="session")
def delayed_audio_clip(clip_dir: Path, main_clip: Path) -> Path:
    """The main clip with its audio starting AUDIO_DELAY s after the first frame (PCM in MKV, so
    no encoder padding blurs the start)."""
    path = clip_dir / "delayed.mkv"
    _ffmpeg(
        "-i", str(main_clip), "-itsoffset", str(AUDIO_DELAY), "-i", str(main_clip),
        "-map", "0:v", "-map", "1:a", "-c:v", "copy", "-c:a", "pcm_s16le",
        str(path),
    )  # fmt: skip
    return path


@pytest.fixture(scope="session")
def rotated_clip(clip_dir: Path, bframes_clip: Path) -> Path:
    """The B-frames clip (a test pattern, so orientation shows) tagged as rotated 90°, like a
    portrait phone recording."""
    path = clip_dir / "rotated.mp4"
    _ffmpeg("-display_rotation", "90", "-i", str(bframes_clip), "-c", "copy", str(path))
    return path


@pytest.fixture(scope="session")
def audio_only(clip_dir: Path) -> Path:
    path = clip_dir / "tone.m4a"
    _ffmpeg("-f", "lavfi", "-i", f"sine=f={TONE_HZ}:r={SAMPLE_RATE}:d=1", "-c:a", "aac", str(path))
    return path


@pytest.fixture(scope="session")
def junk_file(clip_dir: Path) -> Path:
    """Bytes that aren't media, with a video extension."""
    path = clip_dir / "junk.mp4"
    path.write_bytes(bytes(range(256)) * 20)
    return path
