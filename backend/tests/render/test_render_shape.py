"""Hiding a target's shape instead of its box (F-15), and every way that falls back to the box.

The rule under test: a mask is used only when it is there and fits; any doubt gives exactly the
padded box the renderer drew before masks existed (privacy fails closed, D-018).
"""

import json
from pathlib import Path

import numpy as np
import pytest

from vibe.contract import AddMask, Effect, Keyframe, OpLog, TimelineState, Track
from vibe.render import FrameRenderer, MaskLookup, export
from vibe.render.__main__ import main
from vibe.render.geometry import Box, TrackPath
from vibe.render.shape import place_shape
from vibe.video import VideoReader

W, H = 320, 240
BOX: Box = (0.25, 0.25, 0.75, 0.75)  # pixels 80..240 x 60..180


def noise(seed: int = 0) -> np.ndarray:
    return np.random.default_rng(seed).integers(0, 256, (H, W, 3), dtype=np.uint8)


def ellipse(box: Box, width: int = W, height: int = H) -> np.ndarray:
    ys, xs = np.mgrid[0:height, 0:width]
    cx, cy = (xs + 0.5) / width, (ys + 0.5) / height
    rx, ry = (box[2] - box[0]) / 2, (box[3] - box[1]) / 2
    return ((cx - box[0] - rx) / rx) ** 2 + ((cy - box[1] - ry) / ry) ** 2 <= 1


class Lookup:
    """A ``MaskLookup`` from a dict, recording what was asked."""

    def __init__(self, masks: dict[tuple[str, float, Box], np.ndarray]) -> None:
        self.masks = masks
        self.asked: list[tuple[str, float, Box]] = []

    def mask(self, track_id: str, t: float, box: Box) -> np.ndarray | None:
        self.asked.append((track_id, t, tuple(box)))
        return self.masks.get((track_id, t, tuple(box)))


def make_track(states: list[str] | None = None, boxes: list[Box] | None = None) -> Track:
    states = states or ["detected"] * 3
    boxes = boxes or [BOX] * len(states)
    return Track(
        track_id="trk_a",
        label="person",
        source="detector",
        keyframes=[
            Keyframe(t=float(i), box=b, conf=0.9, state=s)
            for i, (s, b) in enumerate(zip(states, boxes, strict=True))
        ],
    )


def ellipses(track: Track) -> Lookup:
    return Lookup({("trk_a", k.t, k.box): ellipse(k.box) for k in track.keyframes})


def state_of(kind: str = "blur", padding: float = 0.15) -> TimelineState:
    log = OpLog()
    log.append(
        AddMask(
            request_id="r_1",
            source="agent",
            targets=["trk_a"],
            effect=Effect(type=kind, padding=padding),
        )
    )
    return log.state()


def render(t: float, track: Track, masks: MaskLookup | None, kind: str = "blur") -> np.ndarray:
    return FrameRenderer(state_of(kind), [track], masks).render(noise(), t)


def test_the_shape_is_hidden_and_the_box_corners_are_left_alone() -> None:
    track = make_track()
    image = noise()
    out = FrameRenderer(state_of("box"), [track], ellipses(track)).render(image, 0.0)
    shape = ellipse(BOX)
    assert (out[shape] == 0).all()
    # The box's corners are outside the ellipse even after the margin: untouched.
    assert (out[62:70, 82:90] == image[62:70, 82:90]).all()
    assert (out[170:178, 230:238] == image[170:178, 230:238]).all()
    # Without masks the whole padded box is black.
    boxed = render(0.0, track, None, "box")
    assert (boxed[62:70, 82:90] == 0).all()


def test_the_margin_grows_the_shape_by_half_the_padding() -> None:
    track = make_track()
    out = FrameRenderer(state_of("box", padding=0.15), [track], ellipses(track)).render(
        noise(), 0.0
    )
    # Margin = 0.075 x 120 px (the box's shorter side) = 9 px past the ellipse's left edge (x=80).
    assert (out[120, 72:80] == 0).all()
    assert (out[120, 60:70] != 0).any()


def test_a_blur_inside_the_shape_changes_only_the_shape() -> None:
    track = make_track()
    image = noise()
    out = FrameRenderer(state_of("blur"), [track], ellipses(track)).render(image, 0.0)
    changed = (out != image).any(axis=2)
    assert changed[120, 160]  # the centre
    assert not changed[62:70, 82:90].any()
    assert not changed[:, :60].any()


def test_between_keyframes_both_shapes_are_moved_onto_the_box_and_joined() -> None:
    a, b = (0.1, 0.25, 0.4, 0.75), (0.3, 0.25, 0.6, 0.75)
    track = make_track(["detected", "detected"], [a, b])
    masks = ellipses(track)
    shape = place_shape(masks, "trk_a", TrackPath(track).at(0.5), 0.0, W, H)
    assert shape is not None
    (x1, y1, x2, y2), m = shape
    # Interpolated box: x 0.2..0.5 -> pixels 64..160; both ellipses moved there coincide.
    expected = ellipse((0.2, 0.25, 0.5, 0.75))
    full = np.zeros((H, W), bool)
    full[y1:y2, x1:x2] = m
    assert (full[expected]).all()
    assert {q[1] for q in masks.asked} == {0.0, 1.0}


@pytest.mark.parametrize(
    "case",
    ["no_mask", "uncertain", "held", "lost_gap", "tiny", "wrong_object", "lookup_raises"],
)
def test_any_doubt_gives_exactly_the_padded_box(case: str) -> None:
    states, t = ["detected"] * 3, 0.5
    boxes = None
    if case == "uncertain":
        states = ["detected", "uncertain", "detected"]
    if case == "held":
        t = 2.3  # past the last keyframe
    if case == "lost_gap":
        boxes = [BOX, BOX]
        states = ["detected", "detected"]
    track = make_track(states, boxes)
    if case == "lost_gap":
        track = track.model_copy(
            update={
                "keyframes": [track.keyframes[0], track.keyframes[1].model_copy(update={"t": 3.0})]
            }
        )
        t = 1.5
    masks = ellipses(track)
    if case == "no_mask":
        masks.masks.pop(("trk_a", 1.0, BOX))
    if case == "tiny":
        dot = np.zeros((H, W), bool)
        dot[118:122, 158:162] = True
        masks.masks = dict.fromkeys(masks.masks, dot)
    if case == "wrong_object":
        other = np.zeros((H, W), bool)
        other[60:180, 200:300] = True  # mostly outside the box
        masks.masks = dict.fromkeys(masks.masks, other)

    class Raises:
        def mask(self, track_id: str, t: float, box: Box) -> np.ndarray | None:
            raise ValueError("corrupt RLE")

    lookup: MaskLookup = Raises() if case == "lookup_raises" else masks
    renderer = FrameRenderer(state_of(), [track], lookup)
    out = renderer.render(noise(), t)
    assert (out == render(t, track, None)).all()
    assert renderer.shape_use["trk_a"].box == 1
    assert renderer.shape_use["trk_a"].shape == 0


def test_a_mask_made_at_another_size_is_scaled_to_the_frame() -> None:
    track = make_track()
    big = Lookup({("trk_a", k.t, k.box): ellipse(k.box, 2 * W, 2 * H) for k in track.keyframes})
    out = FrameRenderer(state_of("box"), [track], big).render(noise(), 0.0)
    assert (out[ellipse(BOX)] == 0).all()


def test_without_masks_nothing_changes_and_nothing_is_counted() -> None:
    track = make_track()
    renderer = FrameRenderer(state_of(), [track])
    assert (renderer.render(noise(), 0.5) == render(0.5, track, None)).all()
    assert renderer.shape_use == {}


def test_export_with_masks_hides_the_shape(pattern_clip: Path, tmp_path: Path) -> None:
    from vibe.video import probe

    info = probe(pattern_clip)
    track = Track(
        track_id="trk_a",
        label="person",
        source="detector",
        keyframes=[Keyframe(t=i / 30, box=BOX, conf=0.9, state="detected") for i in range(90)],
    )
    masks = Lookup(
        {("trk_a", k.t, BOX): ellipse(BOX, info.width, info.height) for k in track.keyframes}
    )
    out_box = export(pattern_clip, state_of("box"), [track], tmp_path / "box.mp4")
    out_shape = export(pattern_clip, state_of("box"), [track], tmp_path / "shape.mp4", masks=masks)
    assert out_shape.shape_use["trk_a"].shape == out_shape.frames
    with VideoReader(out_box.path) as a, VideoReader(out_shape.path) as b:
        boxed, shaped = a.read(10).image, b.read(10).image
    assert boxed[64:68, 84:88].max() < 30  # the box's corner is black...
    assert shaped[64:68, 84:88].max() > 60  # ...but not in the shape's export
    assert shaped[118:122, 158:162].max() < 30  # the centre is black in both


def test_cli_segments_with_the_stub_and_reports_it(
    capsys: pytest.CaptureFixture[str],
    pattern_clip: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VIBE_DATA_DIR", str(tmp_path / "data"))
    log = tmp_path / "t.jsonl"
    oplog = OpLog()
    oplog.append(
        AddMask(request_id="r_1", source="agent", targets=["trk_a"], effect=Effect(type="blur"))
    )
    log.write_text(oplog.to_jsonl(), encoding="utf-8")
    tracks = tmp_path / "tracks.json"
    keyframes = [
        {"t": i / 30, "box": list(BOX), "conf": 0.9, "state": "detected"} for i in range(90)
    ]
    tracks.write_text(
        json.dumps(
            [{"track_id": "trk_a", "label": "person", "source": "detector", "keyframes": keyframes}]
        ),
        encoding="utf-8",
    )
    argv = ["export", str(pattern_clip), "--log", str(log), "--tracks", str(tracks)]
    code = main([*argv, "-o", str(tmp_path / "a.mp4"), "--segmenter", "stub"])
    out, err = capsys.readouterr()
    assert code == 0, err
    summary = json.loads(out)
    assert summary["masks"]["computed"] == 90
    assert summary["shape_use"]["trk_a"] == {"shape": 90, "box": 0}
    code = main([*argv, "-o", str(tmp_path / "b.mp4"), "--segmenter", "stub"])
    again = json.loads(capsys.readouterr().out)
    assert (again["masks"]["computed"], again["masks"]["reused"]) == (0, 90)
    assert main([*argv, "-o", str(tmp_path / "c.mp4"), "--segmenter", "nope"]) == 1
    assert "unknown segmenter" in capsys.readouterr().err
