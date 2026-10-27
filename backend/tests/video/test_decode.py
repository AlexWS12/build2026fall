"""VideoReader: every read returns exactly the frame asked for, in order or after a seek."""

import random
from pathlib import Path

import numpy as np
import pytest
from clip_spec import FPS, FRAMES, GOP, HEIGHT, LUMA_STEP, VFR_DROP, WIDTH

from vibe.video import FrameIndex, NoVideoStream, UnreadableMedia, VideoReader

VFR_KEPT = [n for n in range(FRAMES) if n % VFR_DROP != 2]


def frame_number(image: np.ndarray) -> int:
    """Recover the frame number the main clip encodes as brightness (see clip_spec).

    Luma 16 + 3n converts to RGB (16 + 3n - 16) × 255/219 on every channel.
    """
    assert image.std() < 1, "main clip frames should be a single flat grey"
    return round(float(image.mean()) / (LUMA_STEP * 255 / 219))


def count_seeks(reader: VideoReader, monkeypatch: pytest.MonkeyPatch) -> list[int]:
    seeks: list[int] = []
    original = reader._seek

    def spy(keyframe: int) -> None:
        seeks.append(keyframe)
        original(keyframe)

    monkeypatch.setattr(reader, "_seek", spy)
    return seeks


def test_sequential(main_clip: Path) -> None:
    with VideoReader(main_clip) as reader:
        assert len(reader) == FRAMES
        frames = list(reader.iter_frames())
    assert [f.index for f in frames] == list(range(FRAMES))
    for f in frames:
        assert f.image.shape == (HEIGHT, WIDTH, 3)
        assert f.image.dtype == np.uint8
        assert frame_number(f.image) == f.index
        assert f.time == pytest.approx(f.index / FPS)


@pytest.mark.parametrize("clip", ["main_clip", "webm_clip"])
def test_random_access_is_exact(clip: str, request: pytest.FixtureRequest) -> None:
    order = list(range(FRAMES))
    random.Random(0).shuffle(order)  # backward jumps, forward jumps, non-keyframes
    with VideoReader(request.getfixturevalue(clip)) as reader:
        for i in order:
            assert frame_number(reader.read(i).image) == i


def test_random_access_matches_sequential_with_bframes(bframes_clip: Path) -> None:
    with VideoReader(bframes_clip) as reader:
        sequential = [f.image for f in reader.iter_frames()]
    order = list(range(FRAMES))
    random.Random(1).shuffle(order)
    with VideoReader(bframes_clip) as reader:
        for i in order:
            np.testing.assert_array_equal(reader.read(i).image, sequential[i])


def test_vfr(vfr_clip: Path) -> None:
    with VideoReader(vfr_clip) as reader:
        for i in reversed(range(len(reader))):
            assert frame_number(reader.read(i).image) == VFR_KEPT[i]
        # At a dropped frame's time, the frame before it is still on screen.
        assert frame_number(reader.read_at(2 / FPS).image) == 1


def test_read_at(main_clip: Path) -> None:
    with VideoReader(main_clip) as reader:
        assert reader.read_at(0.0).index == 0
        assert reader.read_at((7 + 0.5) / FPS).index == 7


def test_iter_frames_range_and_step(main_clip: Path) -> None:
    with VideoReader(main_clip) as reader:
        frames = list(reader.iter_frames(start=5, end=30, step=7))
    assert [f.index for f in frames] == [5, 12, 19, 26]
    assert [frame_number(f.image) for f in frames] == [5, 12, 19, 26]


def test_seeks_only_when_needed(main_clip: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with VideoReader(main_clip) as reader:
        seeks = count_seeks(reader, monkeypatch)
        list(reader.iter_frames())
        assert seeks == [0], "reading in order should decode straight through"

        reader.read(GOP * 2 + 3)
        assert seeks[-1] == GOP * 2, "a backward jump seeks to the keyframe before the target"
        reader.read(GOP * 2 + 5)
        assert len(seeks) == 2, "a short hop forward within a GOP keeps decoding"
        reader.read(GOP * 4 + 1)
        assert seeks[-1] == GOP * 4, "a jump past a keyframe seeks instead of decoding through"


def test_rotation(bframes_clip: Path, rotated_clip: Path) -> None:
    with VideoReader(bframes_clip) as upright, VideoReader(rotated_clip) as rotated:
        for i in (0, 13, FRAMES - 1):
            image = rotated.read(i).image
            assert image.shape == (WIDTH, HEIGHT, 3)
            np.testing.assert_array_equal(image, np.rot90(upright.read(i).image))


def test_size(main_clip: Path, rotated_clip: Path) -> None:
    with VideoReader(main_clip, size=(160, 120)) as reader:
        assert reader.read(0).image.shape == (120, 160, 3)
    # The size is as displayed, so a portrait clip takes a portrait size.
    with VideoReader(rotated_clip, size=(120, 160)) as reader:
        assert reader.read(0).image.shape == (160, 120, 3)


def test_reuses_a_given_index(main_clip: Path) -> None:
    index = FrameIndex.build(main_clip)
    with VideoReader(main_clip, index=index) as reader:
        assert reader.index is index


def test_bad_arguments(main_clip: Path) -> None:
    with VideoReader(main_clip) as reader:
        with pytest.raises(IndexError):
            reader.read(FRAMES)
        with pytest.raises(IndexError):
            reader.read(-1)
        with pytest.raises(ValueError):
            list(reader.iter_frames(step=0))


def test_errors(audio_only: Path, junk_file: Path) -> None:
    with pytest.raises(NoVideoStream):
        VideoReader(audio_only)
    with pytest.raises(UnreadableMedia):
        VideoReader(junk_file)
