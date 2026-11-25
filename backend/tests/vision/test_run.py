"""The app's analysis settings (VIBE_DETECTORS, VIBE_TRACKER, VIBE_DETECT_EVERY) and
``make_tracker``. The route that uses them is tested in ``tests/test_api_projects.py``."""

import pytest

from vibe import config
from vibe.modules.vision.run import can_analyse, make_tracker
from vibe.modules.vision.tracking import IouTracker, TrackerSettings


@pytest.mark.parametrize(
    ("value", "specs"),
    [
        (None, []),
        ("", []),
        (" ; ", []),
        ("tv-frcnn-mobile", ["tv-frcnn-mobile"]),
        (
            "rfdetr-s; yunet:weights=a.onnx,threshold=0.5 ;",
            ["rfdetr-s", "yunet:weights=a.onnx,threshold=0.5"],
        ),
    ],
)
def test_detectors_are_specs_split_on_semicolons(
    monkeypatch: pytest.MonkeyPatch, value: str | None, specs: list[str]
) -> None:
    if value is None:
        monkeypatch.delenv("VIBE_DETECTORS", raising=False)
    else:
        monkeypatch.setenv("VIBE_DETECTORS", value)
    assert config.detectors() == specs


def test_tracker_defaults_to_iou(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VIBE_TRACKER", raising=False)
    assert config.tracker() == "iou"
    monkeypatch.setenv("VIBE_TRACKER", "  ")
    assert config.tracker() == "iou"


@pytest.mark.parametrize(("value", "every"), [(None, 1), ("", 1), ("3", 3), (" 2 ", 2)])
def test_detect_every(monkeypatch: pytest.MonkeyPatch, value: str | None, every: int) -> None:
    if value is None:
        monkeypatch.delenv("VIBE_DETECT_EVERY", raising=False)
    else:
        monkeypatch.setenv("VIBE_DETECT_EVERY", value)
    assert config.detect_every() == every


@pytest.mark.parametrize("value", ["0", "-1", "1.5", "often"])
def test_detect_every_must_be_a_whole_number_of_at_least_one(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("VIBE_DETECT_EVERY", value)
    with pytest.raises(ValueError, match="VIBE_DETECT_EVERY"):
        config.detect_every()


@pytest.mark.parametrize(
    ("backend", "detectors", "expected"),
    [
        ("stub", "stub", False),  # stub never runs a model
        ("local", "", False),
        ("local", "stub", True),
        ("remote", "stub", True),  # no remote vision server: it runs here
    ],
)
def test_can_analyse(
    monkeypatch: pytest.MonkeyPatch, backend: str, detectors: str, expected: bool
) -> None:
    monkeypatch.setenv("VIBE_BACKEND", backend)
    monkeypatch.setenv("VIBE_DETECTORS", detectors)
    assert can_analyse() is expected


def test_make_tracker_with_defaults() -> None:
    tracker = make_tracker("iou")
    assert isinstance(tracker, IouTracker)
    assert tracker.settings == TrackerSettings()


def test_make_tracker_with_settings() -> None:
    tracker = make_tracker("iou:min_conf=0.85,iou_match=0.2,max_gap=3")
    assert tracker.settings == TrackerSettings(min_conf=0.85, iou_match=0.2, max_gap=3)


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ("sort", "unknown tracker 'sort'"),
        ("iou:speed=2", "unknown speed"),
        ("iou:min_conf=high", "bad settings for tracker 'iou'"),
        ("iou:min_conf=2", "bad settings for tracker 'iou'"),  # out of range
        ("iou:min_conf", "must look like name=value"),
    ],
)
def test_make_tracker_refuses_bad_specs(spec: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        make_tracker(spec)
