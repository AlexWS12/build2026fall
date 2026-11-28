"""F-14: following a hand-drawn box through the clip (``vibe.modules.vision.follow``).

The matching tests run on numpy frames. The ``follow_box`` tests encode small clips with PyAV
(no ffmpeg install, no GPU), so they also cover decoding at the working size and the chunked
backwards pass. MPEG-4 is lossy, so positions on clips are checked to within a few pixels.
"""

from pathlib import Path

import av
import numpy as np
import pytest

from vibe.contract import Track
from vibe.modules.vision.follow import (
    FollowSettings,
    follow_box,
    follow_frames,
    ncc,
    resize,
    work_size,
)

H, W = 120, 160
RNG = np.random.default_rng(7)
BACKGROUND = (RNG.integers(0, 256, (H, W)) * 0.3 + 40).astype(np.float32)
PATCH = RNG.integers(0, 256, (20, 24)).astype(np.float32)


def scene(x: int, y: int, patch: np.ndarray = PATCH, noise: float = 3.0) -> np.ndarray:
    frame = BACKGROUND.copy()
    ph, pw = patch.shape
    frame[y : y + ph, x : x + pw] = patch
    return frame + np.random.default_rng(x * 1000 + y).normal(0, noise, frame.shape).astype(
        np.float32
    )


def norm_box(x: int, y: int, w: int = 24, h: int = 20) -> tuple[float, float, float, float]:
    return (x / W, y / H, (x + w) / W, (y + h) / H)


def px(box: tuple[float, ...]) -> tuple[float, ...]:
    return (box[0] * W, box[1] * H, box[2] * W, box[3] * H)


# --- ncc and resize --------------------------------------------------------------------------


def test_ncc_finds_the_template_where_it_is() -> None:
    frame = scene(30, 40)
    scores = ncc(frame, PATCH)
    assert np.unravel_index(int(scores.argmax()), scores.shape) == (40, 30)
    assert scores.max() > 0.99
    assert scores.shape == (H - 20 + 1, W - 24 + 1)


def test_ncc_of_a_flat_template_is_zero_not_a_false_match() -> None:
    scores = ncc(scene(30, 40), np.full((10, 10), 128, dtype=np.float32))
    assert not scores.any()


def test_ncc_on_a_flat_area_is_zero() -> None:
    scores = ncc(np.full((30, 30), 90, dtype=np.float32), PATCH)
    assert not scores.any()


def test_ncc_refuses_a_template_bigger_than_the_search_area() -> None:
    with pytest.raises(ValueError, match="bigger than the search area"):
        ncc(np.zeros((10, 10), dtype=np.float32), PATCH)


def test_resize_keeps_a_same_size_image_and_scales_others() -> None:
    assert resize(PATCH, 20, 24) is PATCH
    bigger = resize(PATCH, 40, 48)
    assert bigger.shape == (40, 48)
    assert resize(np.full((5, 7), 3.0, dtype=np.float32), 9, 4) == pytest.approx(3.0)


# --- follow_frames ---------------------------------------------------------------------------


def test_follows_a_moving_target_to_the_pixel() -> None:
    frames = [(i / 10, scene(30 + 3 * i, 40 + i)) for i in range(1, 25)]
    keyframes, stopped = follow_frames((0.0, scene(30, 40)), norm_box(30, 40), frames)
    assert not stopped
    assert [k.t for k in keyframes] == [t for t, _ in frames]
    for i, k in enumerate(keyframes, start=1):
        assert px(k.box) == pytest.approx((30 + 3 * i, 40 + i, 54 + 3 * i, 60 + i), abs=0.5)
        assert k.state == "tracked"
        assert k.conf > 0.9


def test_follows_backwards_too_when_given_frames_latest_first() -> None:
    frames = [(i / 10, scene(30 + 3 * i, 40)) for i in range(9, -1, -1)]
    keyframes, stopped = follow_frames((1.0, scene(60, 40)), norm_box(60, 40), frames)
    assert not stopped
    assert [round(k.t, 1) for k in keyframes] == [round(t, 1) for t, _ in frames]
    assert px(keyframes[-1].box)[0] == pytest.approx(30, abs=0.5)


def test_stops_after_the_target_has_been_gone_for_lost_after() -> None:
    def frame(i: int) -> np.ndarray:
        return scene(30 + 3 * i, 40) if i < 10 else BACKGROUND + 0.0

    frames = [(i / 10, frame(i)) for i in range(1, 30)]
    keyframes, stopped = follow_frames((0.0, scene(30, 40)), norm_box(30, 40), frames)
    assert stopped
    assert keyframes[-1].t == pytest.approx(0.9)  # the last frame it was seen on
    assert all(k.state == "tracked" for k in keyframes)


def test_a_short_occlusion_is_skipped_not_the_end() -> None:
    def frame(i: int) -> np.ndarray:
        hidden = 10 <= i < 13  # 0.3 s, under lost_after (0.5 s)
        return BACKGROUND + 0.0 if hidden else scene(30 + 2 * i, 40)

    frames = [(i / 10, frame(i)) for i in range(1, 25)]
    keyframes, stopped = follow_frames((0.0, scene(30, 40)), norm_box(30, 40), frames)
    assert not stopped
    times = [round(k.t, 1) for k in keyframes]
    assert 1.0 not in times and 1.2 not in times  # no keyframe while it was hidden
    assert times[-1] == pytest.approx(2.4)
    assert px(keyframes[-1].box)[0] == pytest.approx(30 + 2 * 24, abs=0.5)


def test_a_target_still_missing_when_the_clip_ends_counts_as_lost() -> None:
    # Gone for the last 0.3 s: under lost_after, but the clip ends before it's seen again.
    frames = [(i / 10, scene(30 + 2 * i, 40) if i < 8 else BACKGROUND + 0.0) for i in range(1, 11)]
    keyframes, stopped = follow_frames((0.0, scene(30, 40)), norm_box(30, 40), frames)
    assert stopped
    assert keyframes[-1].t == pytest.approx(0.7)


def test_a_weak_match_is_an_uncertain_keyframe_not_dropped() -> None:
    settings = FollowSettings(learn_rate=0.0)
    blurred = (PATCH + np.roll(PATCH, 1, axis=1) + np.roll(PATCH, 1, axis=0)) / 3
    clutter = np.random.default_rng(11).integers(0, 256, PATCH.shape).astype(np.float32)
    weak = 0.7 * blurred + 0.3 * clutter
    frames = [(0.1, scene(33, 40, patch=weak, noise=12.0))]
    keyframes, _ = follow_frames((0.0, scene(30, 40)), norm_box(30, 40), frames, settings)
    assert len(keyframes) == 1
    k = keyframes[0]
    assert settings.lost_below <= k.conf < settings.uncertain_below
    assert k.state == "uncertain"


def exiting(x: int) -> np.ndarray:
    """The patch at ``x``, cut off by the frame's right edge like a target walking out."""
    frame = BACKGROUND.copy()
    visible = max(0, min(24, W - x))
    if visible:
        frame[40:60, x : x + visible] = PATCH[:, :visible]
    return frame


def test_stops_when_the_target_walks_out_of_the_frame() -> None:
    frames = [(i / 10, exiting(30 + 12 * i)) for i in range(1, 20)]  # gone by i = 11
    keyframes, stopped = follow_frames((0.0, exiting(30)), norm_box(30, 40), frames)
    assert stopped
    # Followed exactly while it was whole; a half-visible target isn't matched, so the last
    # keyframe is at the edge and the renderer's held box covers the exit (geometry.HOLD_*).
    assert keyframes[-1].t == pytest.approx(0.8)
    assert px(keyframes[-1].box) == pytest.approx((126, 40, 150, 60), abs=0.5)
    assert all(0 <= v <= 1 for k in keyframes for v in k.box)


def test_follows_a_target_that_grows() -> None:
    sizes = [(20 + i, 24 + round(1.2 * i)) for i in range(0, 12)]
    big = resize(PATCH, 40, 48)

    def frame(i: int) -> np.ndarray:
        h, w = sizes[i]
        return scene(40, 30, patch=resize(big, h, w))

    frames = [(i / 10, frame(i)) for i in range(1, 12)]
    keyframes, stopped = follow_frames((0.0, frame(0)), norm_box(40, 30), frames)
    assert not stopped
    x1, y1, x2, y2 = px(keyframes[-1].box)
    assert (x2 - x1) > 24 * 1.15  # the box grew with the target
    assert (x1, y1) == pytest.approx((40, 30), abs=2.5)


# --- work_size -------------------------------------------------------------------------------


def test_work_size_is_the_work_width_with_even_sides() -> None:
    assert work_size(1920, 1080, (0.4, 0.4, 0.6, 0.6), FollowSettings()) == (320, 180)
    w, h = work_size(1280, 721, (0.4, 0.4, 0.6, 0.6), FollowSettings())
    assert w % 2 == 0 and h % 2 == 0


def test_work_size_grows_for_a_tiny_box_but_never_past_the_video() -> None:
    w, _ = work_size(1920, 1080, (0.5, 0.5, 0.51, 0.52), FollowSettings())
    assert w >= 800  # 1% of the width is at least min_template (8) pixels
    assert work_size(96, 64, (0.5, 0.5, 0.51, 0.51), FollowSettings()) == (96, 64)


# --- follow_box on encoded clips -------------------------------------------------------------

CW, CH, FPS = 160, 96, 20


def _clip_frames(n: int, position) -> list[np.ndarray]:
    rng = np.random.default_rng(3)
    background = rng.integers(40, 120, (CH // 2, CW // 2), dtype=np.uint8)
    background = np.repeat(np.repeat(background, 2, axis=0), 2, axis=1)
    target = rng.integers(0, 256, (12, 14), dtype=np.uint8)
    target = np.repeat(np.repeat(target, 2, axis=0), 2, axis=1)  # 24 × 28, 2-px cells
    images = []
    for i in range(n):
        grey = background.copy()
        x, y = position(i)
        grey[y : y + 24, x : x + 28] = target
        images.append(np.stack([grey, grey, grey], axis=-1))
    return images


def _write(path: Path, images: list[np.ndarray]) -> Path:
    with av.open(str(path), "w") as container:
        stream = container.add_stream("mpeg4", rate=FPS)
        stream.width, stream.height, stream.pix_fmt = CW, CH, "yuv420p"
        stream.bit_rate = 4_000_000
        for image in images:
            for packet in stream.encode(av.VideoFrame.from_ndarray(image, format="rgb24")):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return path


def _cbox(x: int, y: int) -> tuple[float, float, float, float]:
    return (x / CW, y / CH, (x + 28) / CW, (y + 24) / CH)


def test_follow_box_covers_the_clip_both_ways_from_the_drawn_frame(tmp_path: Path) -> None:
    clip = _write(tmp_path / "pan.mp4", _clip_frames(60, lambda i: (10 + 2 * i, 30)))
    drawn_at = 30 / FPS  # frame 30: the target is at x = 70
    followed = follow_box(clip, drawn_at, _cbox(70, 30), track_id="usr_test", label="person")

    track = followed.track
    assert isinstance(track, Track)
    assert (track.track_id, track.label, track.source) == ("usr_test", "person", "user")
    assert followed.lost_before is None and followed.lost_after is None
    drawn = [k for k in track.keyframes if k.state == "user"]
    assert len(drawn) == 1 and drawn[0].t == pytest.approx(drawn_at) and drawn[0].conf == 1.0
    # 10 frames a second are looked at (every 2nd frame at 20 fps), both ways to the clip's ends.
    times = [k.t for k in track.keyframes]
    assert times[0] == pytest.approx(0.0) and times[-1] == pytest.approx(58 / FPS)
    assert len(times) == 30
    for k in track.keyframes:
        i = round(k.t * FPS)
        x1 = k.box[0] * CW
        assert x1 == pytest.approx(10 + 2 * i, abs=2.0), f"frame {i}"
        assert k.box[1] * CH == pytest.approx(30, abs=2.0)


def test_follow_box_says_where_it_lost_the_target(tmp_path: Path) -> None:
    # On screen for frames 10..39 only; drawn on frame 20.
    def position(i: int) -> tuple[int, int]:
        return (40 + i, 30) if 10 <= i < 40 else (0, 0)

    images = _clip_frames(60, position)
    rng = np.random.default_rng(3)
    plain = rng.integers(40, 120, (CH // 2, CW // 2), dtype=np.uint8)
    plain = np.repeat(np.repeat(plain, 2, axis=0), 2, axis=1)
    for i in [*range(10), *range(40, 60)]:
        images[i] = np.stack([plain, plain, plain], axis=-1)  # the target isn't there
    clip = _write(tmp_path / "pass.mp4", images)

    followed = follow_box(clip, 20 / FPS, _cbox(60, 30), track_id="usr_x")
    assert followed.lost_before == pytest.approx(10 / FPS, abs=0.11)
    assert followed.lost_after == pytest.approx(38 / FPS, abs=0.11)
    assert followed.track.keyframes[0].t == followed.lost_before
    assert followed.track.keyframes[-1].t == followed.lost_after
