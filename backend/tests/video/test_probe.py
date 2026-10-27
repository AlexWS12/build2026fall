"""probe(): metadata for the generated clips, and errors for files that aren't usable video."""

from fractions import Fraction
from pathlib import Path

import pytest
from clip_spec import FPS, FRAMES, HEIGHT, SAMPLE_RATE, SECONDS, VFR_DROP, WIDTH

from vibe.video import AudioInfo, NoVideoStream, UnreadableMedia, VideoError, probe


def test_main_clip(main_clip: Path) -> None:
    info = probe(main_clip)
    assert info.path == main_clip
    assert (info.width, info.height) == (WIDTH, HEIGHT)
    assert info.rotation == 0
    assert info.fps == Fraction(FPS)
    assert info.frame_count == FRAMES
    assert info.duration == pytest.approx(SECONDS, abs=1 / FPS)
    assert info.start_time == 0.0
    assert not info.is_vfr
    assert info.codec == "h264"
    assert info.pix_fmt == "yuv420p"
    assert info.audio == AudioInfo(sample_rate=SAMPLE_RATE, channels=2, codec="aac")


def test_vfr_clip(vfr_clip: Path) -> None:
    info = probe(vfr_clip)
    assert info.is_vfr
    assert info.frame_count == FRAMES - FRAMES // VFR_DROP
    assert info.audio is None


def test_silent_clip_has_no_audio(silent_clip: Path) -> None:
    assert probe(silent_clip).audio is None


def test_rotated_clip_reports_display_size(rotated_clip: Path) -> None:
    info = probe(rotated_clip)
    assert info.rotation == 90
    assert (info.width, info.height) == (HEIGHT, WIDTH)


def test_accepts_str_path(main_clip: Path) -> None:
    assert probe(str(main_clip)).path == main_clip


def test_audio_only_file(audio_only: Path) -> None:
    with pytest.raises(NoVideoStream):
        probe(audio_only)


def test_junk_file(junk_file: Path) -> None:
    with pytest.raises(UnreadableMedia):
        probe(junk_file)


def test_errors_share_a_base_class(junk_file: Path) -> None:
    with pytest.raises(VideoError):
        probe(junk_file)


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        probe(tmp_path / "missing.mp4")
