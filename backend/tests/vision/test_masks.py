"""Masks for masked tracks (F-15): only targeted tracks and keyframes, cached, reused. Stub
segmenter only, so no model or GPU."""

from pathlib import Path

import numpy as np
import pytest

from vibe.contract import AddMask, Effect, Keyframe, OpLog, TimelineState, TimeRange, Track
from vibe.modules.vision.masks import KIND, ensure_masks, masks_key, wanted_keyframes
from vibe.modules.vision.segmenters import SEGMENTERS, make_segmenter
from vibe.modules.vision.segmenters.stub import EllipseSegmenter
from vibe.storage import Storage

FPS = 10  # vision_helpers' clips: 10 frames at 10 fps, 96x64


def track(track_id: str, states: list[str] | None = None) -> Track:
    states = states or ["detected"] * 10
    return Track(
        track_id=track_id,
        label="person",
        source="detector",
        keyframes=[
            Keyframe(t=i / FPS, box=(0.1, 0.1, 0.5, 0.6), conf=0.9, state=s)
            for i, s in enumerate(states)
        ],
    )


def masked(*targets: str, start: float = 0, end: float | None = None) -> TimelineState:
    log = OpLog()
    log.append(
        AddMask(
            request_id="r_1",
            source="agent",
            targets=list(targets),
            effect=Effect(type="blur"),
            time_range=TimeRange(start=start, end=end),
        )
    )
    return log.state()


@pytest.fixture
def store(tmp_path: Path, square_clip: Path) -> tuple[Storage, str]:
    storage = Storage(tmp_path / "data")
    return storage, storage.add_video(square_clip).sha256


def test_wanted_keyframes_are_the_span_plus_one_each_side() -> None:
    t = track("trk_a")
    times = [k.t for k in wanted_keyframes(masked("trk_a", start=0.35, end=0.55), t)]
    assert times == pytest.approx([0.3, 0.4, 0.5, 0.6])
    assert wanted_keyframes(masked("trk_b"), t) == []


def test_uncertain_keyframes_get_no_mask() -> None:
    t = track("trk_a", ["detected", "uncertain"] + ["detected"] * 8)
    times = [k.t for k in wanted_keyframes(masked("trk_a", end=0.3), t)]
    assert times == pytest.approx([0.0, 0.2, 0.3])


def test_only_masked_tracks_are_segmented_and_cached(
    store: tuple[Storage, str], square_clip: Path
) -> None:
    storage, sha = store
    tracks = [track("trk_a"), track("trk_b")]
    seg = EllipseSegmenter()
    masks, report = ensure_masks(storage, sha, square_clip, masked("trk_a"), tracks, seg)
    assert (report.tracks, report.keyframes, report.computed, report.reused) == (1, 10, 10, 0)
    assert report.frames_segmented == 10
    assert len(masks) == 10
    assert storage.cache_path(sha, KIND, masks_key(seg, tracks[0])).exists()
    assert not storage.cache_path(sha, KIND, masks_key(seg, tracks[1])).exists()
    k = tracks[0].keyframes[3]
    mask = masks.mask("trk_a", k.t, k.box)
    assert mask is not None and mask.shape == (64, 96)
    assert mask[22, 28] and not mask[7, 10]  # centre of the box in, its corner out
    assert masks.mask("trk_b", k.t, k.box) is None
    assert masks.mask("trk_a", k.t, (0.2, 0.2, 0.3, 0.3)) is None  # another box: no mask


def test_the_second_export_reuses_the_cache(store: tuple[Storage, str], square_clip: Path) -> None:
    storage, sha = store
    tracks = [track("trk_a")]
    ensure_masks(storage, sha, square_clip, masked("trk_a", end=0.3), tracks, EllipseSegmenter())
    _, report = ensure_masks(storage, sha, square_clip, masked("trk_a"), tracks, EllipseSegmenter())
    assert (report.reused, report.computed) == (4, 6)
    _, again = ensure_masks(storage, sha, square_clip, masked("trk_a"), tracks, EllipseSegmenter())
    assert (again.reused, again.computed, again.frames_segmented) == (10, 0, 0)


def test_a_changed_track_or_segmenter_gets_a_new_key() -> None:
    a = track("trk_a")
    moved = a.model_copy(
        update={"keyframes": [a.keyframes[0].model_copy(update={"box": (0.2, 0.1, 0.5, 0.6)})]}
    )
    seg = EllipseSegmenter()
    assert masks_key(seg, a) != masks_key(seg, moved)
    assert masks_key(seg, a) != masks_key(EllipseSegmenter(fail_every=3), a)


def test_an_empty_mask_is_cached_as_none(store: tuple[Storage, str], square_clip: Path) -> None:
    storage, sha = store
    seg = EllipseSegmenter(fail_every=2)
    masks, report = ensure_masks(storage, sha, square_clip, masked("trk_a"), [track("trk_a")], seg)
    assert (report.computed, report.empty, len(masks)) == (10, 5, 5)


def test_segmenter_errors_propagate(store: tuple[Storage, str], square_clip: Path) -> None:
    class Broken(EllipseSegmenter):
        def segment(self, image: np.ndarray, boxes: object) -> list[np.ndarray | None]:
            raise RuntimeError("CUDA out of memory")

    storage, sha = store
    with pytest.raises(RuntimeError, match="out of memory"):
        ensure_masks(storage, sha, square_clip, masked("trk_a"), [track("trk_a")], Broken())


def test_segmenters_by_spec() -> None:
    assert make_segmenter("stub").name == "stub-ellipse"
    assert make_segmenter("stub:fail_every=3").settings == {"fail_every": 3}
    assert SEGMENTERS["sam2"].license.startswith("Apache 2.0")
    with pytest.raises(ValueError, match="unknown segmenter"):
        make_segmenter("sam3")
    with pytest.raises(ValueError, match="bad options"):
        make_segmenter("stub:colour=red")
