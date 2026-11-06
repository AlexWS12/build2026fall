"""The per-frame path: masks follow their time ranges and tracks, and it fails closed."""

import socket

import numpy as np
import pytest

from vibe.contract import (
    AddMask,
    AddRule,
    Effect,
    Keyframe,
    Op,
    OpLog,
    Rule,
    TimelineState,
    TimeRange,
    Track,
)
from vibe.render import FrameRenderer, UnsupportedEdit, render_frame

W, H = 320, 240


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("the per-frame path tried to use the network")

    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def noise(seed: int = 0) -> np.ndarray:
    return np.random.default_rng(seed).integers(0, 256, (H, W, 3), dtype=np.uint8)


def box_track(track_id: str, box: tuple[float, float, float, float]) -> Track:
    return Track(
        track_id=track_id,
        label="person",
        source="detector",
        keyframes=[Keyframe(t=t, box=box, conf=0.9, state="detected") for t in range(11)],
    )


def state_of(*ops: Op) -> TimelineState:
    log = OpLog()
    for op in ops:
        log.append(op)
    return log.state()


def mask(targets: list[str], kind: str = "box", time_range: TimeRange | None = None) -> AddMask:
    return AddMask(
        request_id="r_1",
        source="agent",
        targets=targets,
        effect=Effect(type=kind, padding=0),
        time_range=time_range,
    )


LEFT = (0.0, 0.0, 0.25, 0.25)  # pixels x 0..80, y 0..60


def test_masks_only_the_tracks_box() -> None:
    image = noise()
    out = render_frame(image, 1.0, state_of(mask(["trk_1"])), [box_track("trk_1", LEFT)])
    assert not out[:60, :80].any()
    assert np.array_equal(out[60:, :], image[60:, :])
    assert np.array_equal(out[:, 80:], image[:, 80:])


def test_masks_follow_their_time_range() -> None:
    state = state_of(mask(["trk_1"], time_range=TimeRange(start=1, end=2)))
    renderer = FrameRenderer(state, [box_track("trk_1", LEFT)])
    image = noise()
    assert np.array_equal(renderer.render(image, 0.5), image)
    assert not renderer.render(image, 1.0)[:60, :80].any()
    assert np.array_equal(renderer.render(image, 2.0), image)  # end is excluded


def test_unmasked_tracks_are_left_alone() -> None:
    image = noise()
    tracks = [box_track("trk_1", LEFT), box_track("trk_2", (0.5, 0.5, 1.0, 1.0))]
    out = render_frame(image, 1.0, state_of(mask(["trk_1"])), tracks)
    assert np.array_equal(out[120:, 160:], image[120:, 160:])


def test_padding_comes_from_the_effect() -> None:
    effect = Effect(type="box", padding=0.5)
    state = state_of(AddMask(request_id="r_1", source="agent", targets=["trk_1"], effect=effect))
    out = render_frame(noise(), 1.0, state, [box_track("trk_1", (0.25, 0.25, 0.5, 0.5))])
    # 80 × 60 px box grown by half its size: 20 px wider and 15 px taller on each side.
    assert not out[45:135, 60:180].any()
    assert out[:40].any() and out[:, :55].any()


def test_track_with_no_geometry_masks_the_whole_frame() -> None:
    renderer = FrameRenderer(state_of(mask(["trk_gone"])), tracks=[])
    for t in (0.0, 0.5, 1.0):
        assert not renderer.render(noise(), t).any()
    [issue] = renderer.issues  # reported once, not once per frame
    assert issue.track_id == "trk_gone"
    assert issue.first_seen == 0.0


def lost_after_0(track_id: str = "trk_1") -> Track:
    return Track(
        track_id=track_id,
        label="person",
        source="detector",
        keyframes=[Keyframe(t=0.0, box=LEFT, conf=0.9, state="detected")],
    )


def test_lost_track_stays_masked_and_is_reported() -> None:
    renderer = FrameRenderer(state_of(mask(["trk_1"])), [lost_after_0()])
    assert not renderer.render(noise(), 0.25)[:60, :80].any()
    assert renderer.issues == []  # a short hold is normal, so it isn't reported

    for t in (3.5, 5.0, 60.0):
        assert not renderer.render(noise(), t)[:60, :80].any()
    [issue] = renderer.issues  # reported once, not once per frame
    assert issue.track_id == "trk_1"
    assert issue.first_seen == 3.5
    assert "lost" in issue.message


def test_target_seen_by_another_track_with_its_id_is_not_lost() -> None:
    tracks = [lost_after_0(), box_track("trk_1", (0.75, 0.75, 1.0, 1.0))]
    renderer = FrameRenderer(state_of(mask(["trk_1"])), tracks)
    out = renderer.render(noise(), 5.0)
    assert not out[:60, :80].any()  # both stay masked, to be safe
    assert not out[180:, 240:].any()
    assert renderer.issues == []


def test_long_gap_masks_everywhere_the_target_could_be() -> None:
    # Seen on the left at 1 s and on the right at 8 s; nothing saw it in between.
    track = Track(
        track_id="trk_1",
        label="person",
        source="detector",
        keyframes=[
            Keyframe(t=1.0, box=(0.05, 0.4, 0.2, 0.7), conf=0.9, state="detected"),
            Keyframe(t=8.0, box=(0.8, 0.4, 0.95, 0.7), conf=0.9, state="detected"),
        ],
    )
    renderer = FrameRenderer(state_of(mask(["trk_1"])), [track])
    out = renderer.render(noise(), 4.5)
    assert not out[120, 16:304].any()  # left, right and everything between
    assert renderer.issues[0].first_seen == 4.5


def test_tracks_sharing_an_id_are_all_masked() -> None:
    tracks = [box_track("trk_1", LEFT), box_track("trk_1", (0.75, 0.75, 1.0, 1.0))]
    out = render_frame(noise(), 1.0, state_of(mask(["trk_1"])), tracks)
    assert not out[:60, :80].any()
    assert not out[180:, 240:].any()


def test_standing_rules_are_refused() -> None:
    rule = Rule(rule_id="rule_1", category="person", effect=Effect(type="blur"))
    state = state_of(AddRule(request_id="r_1", source="agent", rule=rule))
    with pytest.raises(UnsupportedEdit, match="F-51"):
        FrameRenderer(state, [])


def test_input_frame_is_not_changed() -> None:
    image = noise()
    before = image.copy()
    render_frame(image, 1.0, state_of(mask(["trk_1"], "blur")), [box_track("trk_1", LEFT)])
    assert np.array_equal(image, before)


@pytest.mark.parametrize("kind", ["blur", "pixelate", "box"])
def test_same_input_gives_the_same_frame(kind: str) -> None:
    state = state_of(mask(["trk_1"], kind))
    tracks = [box_track("trk_1", (0.1, 0.2, 0.6, 0.7))]
    assert np.array_equal(
        render_frame(noise(), 1.0, state, tracks), render_frame(noise(), 1.0, state, tracks)
    )


@pytest.mark.usefixtures("no_network")
def test_per_frame_path_never_uses_the_network() -> None:
    renderer = FrameRenderer(state_of(mask(["trk_1"], "blur")), [box_track("trk_1", LEFT)])
    for t in np.arange(0, 1, 1 / 30):
        renderer.render(noise(), float(t))


def test_image_must_have_channels() -> None:
    with pytest.raises(ValueError, match="channels"):
        render_frame(np.zeros((H, W), np.uint8), 0.0, state_of(), [])
