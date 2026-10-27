"""FrameIndex: frame <-> time on constant-rate, VFR, B-frame and WebM clips."""

from fractions import Fraction
from pathlib import Path

import av
import numpy as np
import pytest
from clip_spec import FPS, FRAMES, SECONDS, VFR_DROP

from vibe.video import FrameIndex, NoVideoStream, UnreadableMedia

# Original frame numbers the VFR clip keeps (it drops index ≡ 2 mod VFR_DROP).
VFR_KEPT = [n for n in range(FRAMES) if n % VFR_DROP != 2]


def decoded_times(path: Path) -> list[float]:
    """Frame times as PyAV reports them after a full decode: the ground truth."""
    with av.open(str(path)) as container:
        return [float(frame.time) for frame in container.decode(video=0)]


def test_constant_rate(main_clip: Path) -> None:
    index = FrameIndex.build(main_clip)
    assert len(index) == FRAMES
    assert not index.is_vfr
    assert index.end == pytest.approx(SECONDS)
    for i in range(FRAMES):
        assert index.time_of(i) == pytest.approx(i / FPS)
        assert index.frame_at(i / FPS) == i
        # Anywhere inside frame i's interval still shows frame i.
        assert index.frame_at((i + 0.5) / FPS) == i


def test_vfr(vfr_clip: Path) -> None:
    index = FrameIndex.build(vfr_clip)
    assert len(index) == len(VFR_KEPT)
    assert index.is_vfr
    for i, n in enumerate(VFR_KEPT):
        assert index.time_of(i) == pytest.approx(n / FPS)
        assert index.frame_at(n / FPS) == i
    # At a dropped frame's time, the frame before it is still on screen.
    assert index.frame_at(2 / FPS) == VFR_KEPT.index(1)


@pytest.mark.parametrize("clip", ["main_clip", "vfr_clip", "bframes_clip", "webm_clip"])
def test_matches_full_decode(clip: str, request: pytest.FixtureRequest) -> None:
    path = request.getfixturevalue(clip)
    index = FrameIndex.build(path)
    np.testing.assert_allclose(index.times, decoded_times(path), atol=1e-9)


def test_bframes_are_put_in_display_order(bframes_clip: Path) -> None:
    with av.open(str(bframes_clip)) as container:
        pts = [p.pts for p in container.demux(video=0) if p.size]
    assert pts != sorted(pts), "fixture should have B-frames (packets out of display order)"
    index = FrameIndex.build(bframes_clip)
    assert np.all(np.diff(index.times) > 0)


def test_webm_rounded_timestamps(webm_clip: Path) -> None:
    # WebM stores times in whole milliseconds, so frame 2 is at 0.067 s, not 2/30 = 0.0667 s.
    index = FrameIndex.build(webm_clip)
    assert len(index) == FRAMES
    for i in range(FRAMES):
        assert index.frame_at(i / FPS) == i


def test_round_trip(vfr_clip: Path) -> None:
    index = FrameIndex.build(vfr_clip)
    for i in range(len(index)):
        assert index.frame_at(index.time_of(i)) == i


def test_out_of_range(main_clip: Path) -> None:
    index = FrameIndex.build(main_clip)
    with pytest.raises(IndexError):
        index.time_of(FRAMES)
    with pytest.raises(IndexError):
        index.time_of(-1)
    with pytest.raises(ValueError):
        index.frame_at(-0.001)
    with pytest.raises(ValueError):
        index.frame_at(index.end)


def test_times_are_read_only(main_clip: Path) -> None:
    index = FrameIndex.build(main_clip)
    with pytest.raises(ValueError):
        index.times[0] = 1.0


def test_from_pts_table() -> None:
    # Timestamps that don't start at 0 are shifted so the first frame is at 0 s.
    index = FrameIndex(np.array([200, 100, 300]), Fraction(1, 100), last_duration=100)
    assert index.times.tolist() == [0.0, 1.0, 2.0]
    assert index.end == 3.0
    assert index.frame_at(1.5) == 1


def test_errors(audio_only: Path, junk_file: Path) -> None:
    with pytest.raises(NoVideoStream):
        FrameIndex.build(audio_only)
    with pytest.raises(UnreadableMedia):
        FrameIndex.build(junk_file)
