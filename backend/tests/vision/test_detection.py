import pytest
from vision_helpers import square_frames

from vibe.contract import Keyframe
from vibe.modules.vision.detection import iou, normalize_box
from vibe.modules.vision.detectors import DETECTORS, make_detector, parse_spec, resolve_options
from vibe.modules.vision.detectors.stub import ColorDetector


def test_normalize_box_scales_and_clips_to_the_frame():
    assert normalize_box(10, 20, 50, 60, 100, 100) == (0.1, 0.2, 0.5, 0.6)
    # Detectors return boxes past the edge; the contract rejects them, so they're clipped.
    assert normalize_box(-5, -5, 120, 50, 100, 100) == (0.0, 0.0, 1.0, 0.5)


@pytest.mark.parametrize(
    "box",
    [(50, 10, 50, 20), (60, 10, 50, 20), (120, 10, 130, 20), (10, -9, 20, -1)],
    ids=["no width", "flipped", "outside right", "outside top"],
)
def test_normalize_box_drops_boxes_with_nothing_left(box):
    assert normalize_box(*box, 100, 100) is None


def test_every_normalized_box_is_a_valid_contract_keyframe():
    box = normalize_box(0.0000001, 3, 99.9999999, 4, 100, 100)
    assert box is not None
    Keyframe(t=0, box=box, conf=1, state="detected")  # raises if the contract rejects it


def test_iou():
    a = (0.0, 0.0, 0.2, 0.2)
    assert iou(a, a) == 1
    assert iou(a, (0.5, 0.5, 0.6, 0.6)) == 0
    assert iou(a, (0.1, 0.0, 0.3, 0.2)) == pytest.approx(0.02 / 0.06)


def test_color_detector_boxes_the_colored_block():
    found = ColorDetector(label="face", color="blue").detect(square_frames()[0])
    assert [(d.label, d.conf) for d in found] == [("face", 1.0)]
    assert found[0].box == pytest.approx((76 / 96, 44 / 64, 88 / 96, 56 / 64))


def test_parse_spec_keeps_option_values_as_text():
    assert parse_spec("yolo") == ("yolo", {})
    assert parse_spec("yolo:weights=yolo11m.pt, threshold=0.2,every=3,half=true") == (
        "yolo",
        {"weights": "yolo11m.pt", "threshold": "0.2", "every": "3", "half": "true"},
    )
    assert parse_spec("yoloe:prompts=coffee mug|poster") == (
        "yoloe",
        {"prompts": "coffee mug|poster"},
    )
    with pytest.raises(ValueError, match="name=value"):
        parse_spec("yolo:threshold")


def test_options_are_converted_by_the_parameter_they_set():
    # A prompt or a weights file named "1984" is text, not a number (it used to crash the CLI).
    assert resolve_options("yoloe", {"prompts": "1984", "weights": "123"}) == {
        "prompts": "1984",
        "weights": "123",
    }
    assert resolve_options("yolo", {"threshold": "0.2"}) == {"threshold": 0.2}
    assert resolve_options("yolo", {"threshold": "1"}) == {"threshold": 1.0}
    assert resolve_options("stub", {"tolerance": "10", "label": "7"}) == {
        "tolerance": 10,
        "label": "7",
    }
    assert resolve_options("egoblur-face", {"bgr": "false"}) == {"bgr": False}


@pytest.mark.parametrize(
    ("name", "options", "message"),
    [
        ("stub", {"colour": "red"}, "unknown colour"),
        ("stub", {"tolerance": "lots"}, "tolerance='lots' is not a int"),
        ("yolo", {"threshold": "high"}, "threshold='high' is not a float"),
        ("egoblur-face", {"bgr": "maybe"}, "bgr='maybe' is not a bool"),
    ],
)
def test_bad_options_name_the_option(name, options, message):
    with pytest.raises(ValueError, match=message):
        resolve_options(name, options)


def test_make_detector_rejects_unknown_names_and_options():
    with pytest.raises(ValueError, match="unknown detector"):
        make_detector("yolo99")
    with pytest.raises(ValueError, match="bad options"):
        make_detector("stub:colour=red")
    detector = make_detector("stub:color=green,label=car,tolerance=10")
    assert detector.labels == frozenset({"car"})
    assert detector.settings["tolerance"] == 10


def test_ultralytics_detectors_are_flagged_as_not_permissive():
    """D-015: AGPL stays off the default path, so the CLI warns whenever one is used."""
    assert not DETECTORS["yolo"].permissive
    assert not DETECTORS["yoloe"].permissive
    assert DETECTORS["rfdetr-l"].permissive
    assert DETECTORS["egoblur-face"].permissive


def _fake_yunet(grow: float, x: float = 90.0, y: float = 40.0):
    """A YuNetDetector without OpenCV or weights: its model returns one fixed face row."""
    import numpy as np

    from vibe.modules.vision.detectors.yunet_det import YuNetDetector

    det = object.__new__(YuNetDetector)
    det._grow, det._size = grow, (200, 100)

    class Model:
        def setInputSize(self, size):  # noqa: N802 (OpenCV's name)
            pass

        def detect(self, image):
            # x, y, w, h, 10 landmark values, score
            return 1, np.array([[x, y, 20.0, 20.0, *[0.0] * 10, 0.9]])

    det._model = Model()
    return det


def test_yunet_grow_scales_boxes_around_their_centre() -> None:
    import numpy as np

    image = np.zeros((100, 200, 3), dtype=np.uint8)
    (plain,) = _fake_yunet(1.0).detect(image)
    (grown,) = _fake_yunet(2.0).detect(image)

    assert plain.box == (0.45, 0.4, 0.55, 0.6)
    assert grown.box == (0.4, 0.3, 0.6, 0.7)  # twice as wide and tall, same centre
    assert grown.conf == plain.conf


def test_yunet_grow_is_clipped_to_the_frame() -> None:
    import numpy as np

    # A face in the top-left corner: 4x grows it past the frame on two sides.
    (box,) = _fake_yunet(4.0, x=0.0, y=0.0).detect(np.zeros((100, 200, 3), dtype=np.uint8))
    assert box.box == (0.0, 0.0, 0.25, 0.5)
